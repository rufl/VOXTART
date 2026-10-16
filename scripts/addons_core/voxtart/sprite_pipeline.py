# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""End-to-end deterministic sprite-to-voxel orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .depth_observations import DepthObservation, PartMask
from .geometry_skeleton import SkeletonGraph, derive_skeleton
from .guide_prior import (
    GuidePriorResult,
    MeshGuide,
    apply_guide_prior,
    voxelize_mesh_guide,
)
from .image_intake import IndexedImage
from .provenance import sha256_json
from .reconstruct import (
    AcceptanceDecision,
    AcceptancePolicy,
    OrthographicView,
    ReconstructionResult,
    ReconstructionSettings,
    reconstruct_views,
)
from .symmetry import SymmetryResult, apply_missing_only
from .voxel import Cell, Color, VoxelAsset


@dataclass(frozen=True, slots=True)
class SpriteCandidate:
    name: str
    result: ReconstructionResult
    decision: AcceptanceDecision

    @property
    def review_key(self) -> tuple[object, ...]:
        report = self.result.report
        return (
            not self.decision.accepted,
            len(self.result.unresolved),
            report.conflicting_rays,
            report.material_conflicts + report.visible_material_conflicts,
            report.missing_pixels + report.extra_pixels + report.color_mismatches,
            -report.silhouette_iou,
            self.name,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "accepted": self.decision.accepted,
            "reasons": list(self.decision.reasons),
            "asset_digest": self.result.asset.content_digest,
            "report": self.result.report.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SpriteConversionResult:
    asset: VoxelAsset
    reconstruction: ReconstructionResult
    candidates: tuple[SpriteCandidate, ...]
    selected_candidate: SpriteCandidate
    symmetry: SymmetryResult | None
    guide: GuidePriorResult | None
    skeleton: SkeletonGraph




@dataclass(frozen=True, slots=True)
class SpriteConversionSettings:
    axis: str = "Z"
    direction: int = 1
    origin: Cell = (0, 0, 0)
    depth: int = 1
    priority: int = 0
    mode: str = "SURFACE"
    shell_thickness: int = 1
    material_policy: str = "VISIBLE_ONLY"
    symmetry_axis: str | None = None
    symmetry_plane: int = 0
    guide_mode: str = "ALLOW_HIDDEN"
    guide_grid_size: float = 1.0
    guide_voxelizer_mode: str = "SOLID"
    guide_shell_thickness: int = 1
    max_skeleton_nodes: int = 256
    acceptance: AcceptancePolicy = AcceptancePolicy(require_source_identities=True)

    def reconstruction(self) -> ReconstructionSettings:
        return ReconstructionSettings(
            mode=self.mode,
            shell_thickness=self.shell_thickness,
            material_policy=self.material_policy,
        )


def _view(
    image: IndexedImage,
    settings: SpriteConversionSettings,
    *,
    depth_observation: DepthObservation | None = None,
    part_mask: PartMask | None = None,
) -> OrthographicView:
    source_identity = image.source_identity()
    return image.to_view(
        axis=settings.axis,
        direction=settings.direction,
        origin=settings.origin,
        depth=settings.depth,
        priority=settings.priority,
        source_identity=source_identity,
        depth_observation=depth_observation,
        part_mask=part_mask,
    )


def generate_candidates(
    views: Sequence[OrthographicView],
    palette: Sequence[Color],
    *,
    acceptance: AcceptancePolicy = AcceptancePolicy(require_source_identities=False),
    source_provenance: Mapping[str, str] | None = None,
) -> tuple[SpriteCandidate, ...]:
    """Generate deduplicated deterministic depth-policy candidates."""

    if not views:
        raise ValueError("candidate generation requires at least one view")
    all_layered = all(view.depth_observation is not None for view in views)
    settings = [ReconstructionSettings(mode="SURFACE")]
    if all_layered:
        settings.append(ReconstructionSettings(mode="LAYERED"))
    settings.extend(
        (
            ReconstructionSettings(mode="SHELL", shell_thickness=1),
            ReconstructionSettings(mode="VOLUME"),
        )
    )
    candidates: list[SpriteCandidate] = []
    seen: set[str] = set()
    for candidate_settings in settings:
        result = reconstruct_views(
            views,
            palette,
            candidate_settings,
            source_provenance=source_provenance,
        )
        payload = result.asset.to_dict()
        payload.pop("provenance", None)
        digest = sha256_json(payload)
        if digest in seen:
            continue
        seen.add(digest)
        candidates.append(
            SpriteCandidate(
                candidate_settings.mode,
                result,
                result.assess(acceptance),
            )
        )
    return tuple(candidates)
def select_candidate(candidates: Sequence[SpriteCandidate]) -> SpriteCandidate:
    """Select the best review candidate with stable quality precedence."""

    normalized = tuple(candidates)
    if not normalized:
        raise ValueError("candidate selection requires at least one candidate")
    return min(normalized, key=lambda candidate: candidate.review_key)


def convert_sprite(
    image: IndexedImage,
    *,
    settings: SpriteConversionSettings = SpriteConversionSettings(),
    depth_observation: DepthObservation | None = None,
    part_mask: PartMask | None = None,
    guide: VoxelAsset | MeshGuide | None = None,
    source_provenance: Mapping[str, str] | None = None,
) -> SpriteConversionResult:
    """Run intake, constrained reconstruction, mesh/template priors, and skeleton suggestion."""

    if not isinstance(image, IndexedImage):
        raise TypeError("sprite conversion expects an IndexedImage")
    if guide is not None and not isinstance(guide, (VoxelAsset, MeshGuide)):
        raise TypeError("sprite conversion guide must be a VoxelAsset or MeshGuide")
    provenance = image.provenance() if source_provenance is None else source_provenance
    view = _view(image, settings, depth_observation=depth_observation, part_mask=part_mask)
    reconstruction = reconstruct_views(
        (view,),
        image.palette,
        settings.reconstruction(),
        source_provenance=provenance,
    )
    candidates = generate_candidates(
        (view,),
        image.palette,
        acceptance=settings.acceptance,
        source_provenance=provenance,
    )
    selected_candidate = select_candidate(candidates)
    symmetry = None
    asset = reconstruction.asset
    if settings.symmetry_axis is not None:
        symmetry = apply_missing_only(
            asset,
            settings.symmetry_axis,
            plane=settings.symmetry_plane,
            evidence=reconstruction.evidence,
        )
        asset = symmetry.asset
    guide_result = None
    if guide is not None:
        guide_asset = (
            voxelize_mesh_guide(
                guide,
                grid_size=settings.guide_grid_size,
                mode=settings.guide_voxelizer_mode,
                shell_thickness=settings.guide_shell_thickness,
            )
            if isinstance(guide, MeshGuide)
            else guide
        )
        guide_result = apply_guide_prior(
            reconstruction if asset is reconstruction.asset else ReconstructionResult(
                asset=asset,
                unresolved=reconstruction.unresolved,
                report=reconstruction.report,
                evidence=reconstruction.evidence,
                source_identities=reconstruction.source_identities,
            ),
            guide_asset,
            mode=settings.guide_mode,
        )
        asset = guide_result.asset
    skeleton = derive_skeleton(asset, max_nodes=settings.max_skeleton_nodes)
    return SpriteConversionResult(
        asset,
        reconstruction,
        candidates,
        selected_candidate,
        symmetry,
        guide_result,
        skeleton,
    )




__all__ = [
    "SpriteCandidate",
    "SpriteConversionResult",
    "SpriteConversionSettings",
    "convert_sprite",
    "generate_candidates",
    "select_candidate",
]
