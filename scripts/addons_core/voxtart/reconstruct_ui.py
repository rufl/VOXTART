# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Blender adapter for calibrated image-to-voxel reconstruction."""

from __future__ import annotations

import json
from pathlib import Path

import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, IntVectorProperty, StringProperty

from . import operators
from .depth_observations import DepthObservation, PartMask, load_observation, load_part_mask
from .image_intake import ImageFrame, PaletteIntakeSettings, extract_palette_frames, index_image
from .reconstruct import AcceptancePolicy, ReconstructionSettings, reconstruct_views
from .sprite_pipeline import generate_candidates, select_candidate


RECONSTRUCTION_REPORT_PROPERTY = "voxtart_reconstruction_report"
RECONSTRUCTION_UNRESOLVED_CELLS_PROPERTY = "voxtart_reconstruction_unresolved_cells"
RECONSTRUCTION_EVIDENCE_PROPERTY = "voxtart_reconstruction_evidence"
RECONSTRUCTION_ACCEPTANCE_PROPERTY = "voxtart_reconstruction_acceptance"
RECONSTRUCTION_CANDIDATES_PROPERTY = "voxtart_reconstruction_candidates"
RECONSTRUCTION_SELECTED_CANDIDATE_PROPERTY = "voxtart_reconstruction_selected_candidate"
RECONSTRUCTION_OVERLAY_PROPERTY = "voxtart_reconstruction_overlay"
RECONSTRUCTION_SOURCE_PROPERTY = "voxtart_reconstruction_source"
RECONSTRUCTION_SOURCES_PROPERTY = "voxtart_reconstruction_sources"
RECONSTRUCTION_VIEW_PROPERTY = "voxtart_reconstruction_view"
RECONSTRUCTION_VIEWS_PROPERTY = "voxtart_reconstruction_views"
MAX_RECONSTRUCTION_PIXELS = 1_000_000
MAX_RECONSTRUCTION_CELLS = 4_000_000
VIEW_SLOTS = (1, 2, 3, 4, 5, 6)


def _slot_property(slot: int, field: str) -> str:
    if slot not in VIEW_SLOTS:
        raise ValueError(f"reconstruction view slot must be one of {VIEW_SLOTS}: {slot!r}")
    return f"voxtart_reconstruction_view_{slot}_{field}"

_AXIS_ITEMS = (
    ("X", "X", "Use X as the view-depth axis"),
    ("Y", "Y", "Use Y as the view-depth axis"),
    ("Z", "Z", "Use Z as the view-depth axis"),
)
_DIRECTION_ITEMS = (
    ("1", "Positive", "Project into the positive direction of the view axis"),
    ("-1", "Negative", "Project into the negative direction of the view axis"),
)
_BACKGROUND_ITEMS = (
    ("ALPHA", "Alpha", "Treat pixels at or below the alpha threshold as background"),
    ("NONE", "None", "Keep transparent pixels as source colors"),
    ("CORNER", "Corner", "Use the top-left pixel as the background color"),
    ("EXPLICIT", "Explicit", "Use the configured background color"),
)
_MODE_ITEMS = (
    ("SURFACE", "Surface", "Select the nearest viable voxel per source pixel"),
    ("LAYERED", "Layered", "Use the per-pixel front/back depth observation"),
    ("SHELL", "Shell", "Keep the configured number of front layers"),
    ("VOLUME", "Volume", "Keep every viable voxel in the calibrated depth slab"),
    ("REVIEW", "Review candidates", "Generate and persist deterministic reconstruction candidates"),
)
_MATERIAL_ITEMS = (
    ("VISIBLE_ONLY", "Visible only", "Project visible source colors onto the frontmost voxels"),
    ("EVIDENCE", "All evidence", "Keep all ray evidence colors without visible-only projection"),
)


def _load_image_frame(filepath: str) -> tuple[ImageFrame, Path]:
    path = Path(bpy.path.abspath(str(filepath or ""))).expanduser()
    if not path.is_file():
        raise ValueError(f"image file does not exist: {path}")
    image = None
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
        width, height = (int(image.size[0]), int(image.size[1]))
        if width < 1 or height < 1:
            raise ValueError("image has no pixels")
        if width * height > MAX_RECONSTRUCTION_PIXELS:
            raise ValueError(f"image exceeds the {MAX_RECONSTRUCTION_PIXELS:,}-pixel reconstruction budget")
        raw = tuple(float(value) for value in image.pixels)
        expected = width * height * 4
        if len(raw) != expected:
            raise ValueError("Blender returned an incomplete image pixel buffer")
        rows = tuple(
            tuple(
                tuple(raw[(row * width + column) * 4:(row * width + column + 1) * 4])
                for column in range(width)
            )
            for row in range(height)
        )
        return ImageFrame.from_rows(path.stem or "image", rows), path
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ValueError(f"could not load reconstruction image: {error}") from error
    finally:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass


def _load_optional_observation(
    filepath: str,
    loader,
    *,
    width: int,
    height: int,
    label: str,
):
    path = Path(bpy.path.abspath(str(filepath or ""))).expanduser()
    if not str(filepath or "").strip():
        return None
    if not path.is_file():
        raise ValueError(f"{label} file does not exist: {path}")
    try:
        observation = loader(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"could not load {label}: {error}") from error
    if (observation.width, observation.height) != (width, height):
        raise ValueError(
            f"{label} dimensions {observation.width}x{observation.height} "
            f"do not match image {width}x{height}"
        )
    return observation


def _load_depth_observation(filepath: str, *, width: int, height: int) -> DepthObservation | None:
    return _load_optional_observation(
        filepath,
        load_observation,
        width=width,
        height=height,
        label="depth observation",
    )


def _load_part_mask(filepath: str, *, width: int, height: int) -> PartMask | None:
    return _load_optional_observation(
        filepath,
        load_part_mask,
        width=width,
        height=height,
        label="part mask",
    )


def _view_dict(view) -> dict[str, object]:
    value = {
        "name": view.name,
        "axis": view.axis,
        "direction": view.direction,
        "origin": list(view.origin),
        "depth": view.depth,
        "priority": view.priority,
    }
    if view.depth_observation is not None:
        value["depth_observation"] = {
            "name": view.depth_observation.name,
            "digest": view.depth_observation.digest,
            "method": view.depth_observation.method,
        }
    if view.part_mask is not None:
        value["part_mask"] = {
            "name": view.part_mask.name,
            "digest": view.part_mask.digest,
            "parts": list(view.part_mask.parts),
        }
    return value


def _candidate_review_payload(candidates, selected) -> dict[str, object]:
    return {
        "selected": selected.name,
        "candidates": [candidate.to_dict() for candidate in candidates],
    }


def _store_common_result(obj: bpy.types.Object, result, candidate_review=None) -> None:
    report = result.report.to_dict()
    obj[RECONSTRUCTION_REPORT_PROPERTY] = json.dumps(report, sort_keys=True, separators=(",", ":"))
    obj[RECONSTRUCTION_UNRESOLVED_CELLS_PROPERTY] = json.dumps(
        [list(cell) for cell in result.unresolved],
        sort_keys=True,
        separators=(",", ":"),
    )
    obj[RECONSTRUCTION_EVIDENCE_PROPERTY] = json.dumps(
        result.evidence.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
    )
    obj[RECONSTRUCTION_ACCEPTANCE_PROPERTY] = json.dumps(
        result.assess(AcceptancePolicy(require_source_identities=True)).to_dict(),
        sort_keys=True,
        separators=(",", ":"),
    )
    if candidate_review is not None:
        candidates, selected = candidate_review
        payload = _candidate_review_payload(candidates, selected)
        obj[RECONSTRUCTION_CANDIDATES_PROPERTY] = json.dumps(
            payload["candidates"],
            sort_keys=True,
            separators=(",", ":"),
        )
        obj[RECONSTRUCTION_SELECTED_CANDIDATE_PROPERTY] = selected.name
    operators._update_overlay(
        obj,
        result.asset,
        RECONSTRUCTION_OVERLAY_PROPERTY,
        result.unresolved,
        "Unresolved",
        (0.75, 0.2, 1.0, 1.0),
    )

def _store_result(obj: bpy.types.Object, source: Path, view, result, candidate_review=None) -> None:
    _store_common_result(obj, result, candidate_review)
    obj[RECONSTRUCTION_SOURCE_PROPERTY] = str(source)
    obj[RECONSTRUCTION_VIEW_PROPERTY] = json.dumps(
        _view_dict(view),
        sort_keys=True,
        separators=(",", ":"),
    )


def _store_multiview_result(obj, sources, views, result, candidate_review=None) -> None:
    _store_common_result(obj, result, candidate_review)
    obj[RECONSTRUCTION_SOURCES_PROPERTY] = json.dumps(
        [str(source) for source in sources],
        sort_keys=True,
        separators=(",", ":"),
    )
    obj[RECONSTRUCTION_VIEWS_PROPERTY] = json.dumps(
        [_view_dict(view) for view in views],
        sort_keys=True,
        separators=(",", ":"),
    )



def _report_from_object(obj):
    if obj is None:
        return None
    encoded = obj.get(RECONSTRUCTION_REPORT_PROPERTY)
    if not isinstance(encoded, str):
        return None
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None
def _candidate_review_from_object(obj):
    if obj is None:
        return None
    encoded = obj.get(RECONSTRUCTION_CANDIDATES_PROPERTY)
    selected = obj.get(RECONSTRUCTION_SELECTED_CANDIDATE_PROPERTY)
    if not isinstance(encoded, str) or not isinstance(selected, str):
        return None
    try:
        candidates = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(candidates, list):
        return None
    return candidates, selected


def _draw_candidate_review(layout, obj) -> None:
    review = _candidate_review_from_object(obj)
    if review is None:
        return
    candidates, selected = review
    box = layout.box()
    box.label(text=f"Candidate review · selected: {selected}", icon="SORTALPHA")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        report = candidate.get("report", {})
        if not isinstance(report, dict):
            report = {}
        status = "accepted" if candidate.get("accepted") else "rejected"
        box.label(
            text=(
                f"{candidate.get('name', '?')}: {status}; "
                f"{report.get('occupied_voxels', 0)} voxels; "
                f"{report.get('unresolved_voxels', 0)} unresolved"
            )
        )




def _review_candidates(views, palette, source_provenance):
    candidates = generate_candidates(
        tuple(views),
        palette,
        acceptance=AcceptancePolicy(require_source_identities=True),
        source_provenance=source_provenance,
    )
    return candidates, select_candidate(candidates)


class VOXTART_OT_reconstruct_image(bpy.types.Operator):
    bl_idname = "voxtart.reconstruct_image"
    bl_label = "Reconstruct Image to Voxels"
    bl_description = "Create a non-destructive voxel asset from one calibrated orthographic image"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(name="Image", subtype="FILE_PATH")
    depth_filepath: StringProperty(name="Depth Observation", subtype="FILE_PATH")
    part_mask_filepath: StringProperty(name="Part Mask", subtype="FILE_PATH")
    axis: EnumProperty(name="Depth Axis", items=_AXIS_ITEMS, default="Z")
    direction: EnumProperty(name="Facing", items=_DIRECTION_ITEMS, default="1")
    origin: IntVectorProperty(name="Origin", size=3, default=(0, 0, 0))
    depth: IntProperty(name="Depth", min=1, max=MAX_RECONSTRUCTION_CELLS, default=1)
    priority: IntProperty(name="Priority", default=0)
    background_mode: EnumProperty(name="Background", items=_BACKGROUND_ITEMS, default="ALPHA")
    alpha_threshold: FloatProperty(name="Alpha Threshold", min=0.0, max=1.0, default=0.0)
    background_color: FloatVectorProperty(
        name="Background Color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0, 1.0),
    )
    background_tolerance: FloatProperty(name="Background Tolerance", min=0.0, max=1.0, default=0.0)
    max_colors: IntProperty(name="Maximum Colors", min=1, max=256, default=256)
    mode: EnumProperty(name="Depth Mode", items=_MODE_ITEMS, default="SURFACE")
    shell_thickness: IntProperty(name="Shell Thickness", min=1, max=MAX_RECONSTRUCTION_CELLS, default=1)
    material_policy: EnumProperty(name="Material Policy", items=_MATERIAL_ITEMS, default="VISIBLE_ONLY")

    def invoke(self, context, _event):
        if self.filepath:
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            frame, source = _load_image_frame(self.filepath)
            if frame.width * frame.height * int(self.depth) > MAX_RECONSTRUCTION_CELLS:
                raise ValueError(f"calibrated image exceeds the {MAX_RECONSTRUCTION_CELLS:,}-cell reconstruction budget")
            depth_observation = _load_depth_observation(
                self.depth_filepath,
                width=frame.width,
                height=frame.height,
            )
            part_mask = _load_part_mask(
                self.part_mask_filepath,
                width=frame.width,
                height=frame.height,
            )
            intake_settings = PaletteIntakeSettings(
                background_mode=self.background_mode,
                alpha_threshold=float(self.alpha_threshold),
                background_color=tuple(self.background_color),
                background_tolerance=float(self.background_tolerance),
                max_colors=int(self.max_colors),
            )
            indexed = index_image(frame, intake_settings)
            view = indexed.to_view(
                axis=self.axis,
                direction=int(self.direction),
                origin=tuple(self.origin),
                depth=int(self.depth),
                priority=int(self.priority),
                source_identity=indexed.source_identity(source_id=str(source)),
                depth_observation=depth_observation,
                part_mask=part_mask,
            )
            candidate_review = None
            if self.mode == "REVIEW":
                candidate_review = _review_candidates(
                    (view,),
                    indexed.palette,
                    indexed.provenance(),
                )
                result = candidate_review[1].result
            else:
                result = reconstruct_views(
                    (view,),
                    indexed.palette,
                    ReconstructionSettings(
                        mode=self.mode,
                        shell_thickness=int(self.shell_thickness),
                        material_policy=self.material_policy,
                    ),
                    source_provenance=indexed.provenance(),
                )
            obj = operators._create_asset_object(
                context,
                f"VOXTART {frame.name} Reconstruction",
                result.asset,
            )
            _store_result(obj, source, view, result, candidate_review)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Reconstructed {result.report.occupied_voxels} voxels; "
            f"{result.report.unresolved_voxels} unresolved; "
            f"{result.report.conflicting_rays} conflicts",
        )
        return {"FINISHED"}



def _scene_intake_settings(scene) -> PaletteIntakeSettings:
    return PaletteIntakeSettings(
        background_mode=scene.voxtart_reconstruction_background,
        alpha_threshold=float(scene.voxtart_reconstruction_alpha_threshold),
        background_color=tuple(scene.voxtart_reconstruction_background_color),
        background_tolerance=float(scene.voxtart_reconstruction_background_tolerance),
        max_colors=int(scene.voxtart_reconstruction_max_colors),
    )


class VOXTART_OT_reconstruct_views(bpy.types.Operator):
    bl_idname = "voxtart.reconstruct_views"
    bl_label = "Reconstruct Calibrated Views"
    bl_description = "Create one voxel asset from up to six calibrated orthographic images"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        entries = []
        try:
            for slot in VIEW_SLOTS:
                filepath = str(getattr(scene, _slot_property(slot, "filepath")) or "").strip()
                if not filepath:
                    continue
                frame, source = _load_image_frame(filepath)
                depth = int(getattr(scene, _slot_property(slot, "depth")))
                if frame.width * frame.height * depth > MAX_RECONSTRUCTION_CELLS:
                    raise ValueError(
                        f"view {slot} exceeds the {MAX_RECONSTRUCTION_CELLS:,}-cell reconstruction budget"
                    )
                depth_observation = _load_depth_observation(
                    getattr(scene, _slot_property(slot, "depth_filepath")),
                    width=frame.width,
                    height=frame.height,
                )
                part_mask = _load_part_mask(
                    getattr(scene, _slot_property(slot, "part_mask_filepath")),
                    width=frame.width,
                    height=frame.height,
                )
                entries.append((slot, frame, source, depth, depth_observation, part_mask))
            if not entries:
                raise ValueError("choose at least one calibrated reconstruction view")

            intake_settings = _scene_intake_settings(scene)
            palette = extract_palette_frames(
                tuple(frame for _slot, frame, _source, _depth, _depth_observation, _part_mask in entries),
                intake_settings,
            )
            views = []
            sources = []
            source_provenance = {}
            total_cells = 0
            for slot, frame, source, depth, depth_observation, part_mask in entries:
                indexed = index_image(frame, intake_settings, palette=palette)
                views.append(
                    indexed.to_view(
                        name=f"view_{slot}_{indexed.name}",
                        axis=getattr(scene, _slot_property(slot, "axis")),
                        direction=int(getattr(scene, _slot_property(slot, "direction"))),
                        origin=tuple(getattr(scene, _slot_property(slot, "origin"))),
                        depth=depth,
                        priority=int(getattr(scene, _slot_property(slot, "priority"))),
                        source_identity=indexed.source_identity(source_id=str(source)),
                        depth_observation=depth_observation,
                        part_mask=part_mask,
                    )
                )
                sources.append(source)
                total_cells += frame.width * frame.height * depth
                for key, value in indexed.provenance().items():
                    source_provenance[f"view_{slot}_{key}"] = value
                if depth_observation is not None:
                    source_provenance[f"view_{slot}_depth_digest"] = depth_observation.digest
                if part_mask is not None:
                    source_provenance[f"view_{slot}_part_mask_digest"] = part_mask.digest
            if total_cells > MAX_RECONSTRUCTION_CELLS:
                raise ValueError(
                    f"calibrated views exceed the {MAX_RECONSTRUCTION_CELLS:,}-cell reconstruction budget"
                )
            candidate_review = None
            if scene.voxtart_reconstruction_mode == "REVIEW":
                candidate_review = _review_candidates(
                    tuple(views),
                    palette,
                    source_provenance,
                )
                result = candidate_review[1].result
            else:
                result = reconstruct_views(
                    tuple(views),
                    palette,
                    ReconstructionSettings(
                        mode=scene.voxtart_reconstruction_mode,
                        shell_thickness=int(scene.voxtart_reconstruction_shell_thickness),
                        material_policy=scene.voxtart_reconstruction_material_policy,
                    ),
                    source_provenance=source_provenance,
                )
            obj = operators._create_asset_object(
                context,
                "VOXTART Multi-view Reconstruction",
                result.asset,
            )
            _store_multiview_result(obj, sources, views, result, candidate_review)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Reconstructed {result.report.view_count} views; "
            f"{result.report.occupied_voxels} voxels; "
            f"{result.report.unresolved_voxels} unresolved; "
            f"{result.report.conflicting_rays} conflicts",
        )
        return {"FINISHED"}

class VOXTART_PT_reconstruction(bpy.types.Panel):
    bl_label = "Image Reconstruction"
    bl_idname = "VOXTART_PT_reconstruction"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 20

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "voxtart_reconstruction_filepath", text="Image")
        layout.prop(scene, "voxtart_reconstruction_depth_filepath", text="Depth JSON")
        layout.prop(scene, "voxtart_reconstruction_part_mask_filepath", text="Parts JSON")
        layout.prop(scene, "voxtart_reconstruction_axis", text="Depth Axis")
        layout.prop(scene, "voxtart_reconstruction_direction", text="Facing")
        layout.prop(scene, "voxtart_reconstruction_origin", text="Origin")
        layout.prop(scene, "voxtart_reconstruction_depth", text="Depth")
        layout.prop(scene, "voxtart_reconstruction_priority", text="Priority")
        layout.prop(scene, "voxtart_reconstruction_background", text="Background")
        layout.prop(scene, "voxtart_reconstruction_alpha_threshold")
        if scene.voxtart_reconstruction_background in {"CORNER", "EXPLICIT"}:
            layout.prop(scene, "voxtart_reconstruction_background_color")
            layout.prop(scene, "voxtart_reconstruction_background_tolerance")
        layout.prop(scene, "voxtart_reconstruction_max_colors")
        layout.prop(scene, "voxtart_reconstruction_mode", text="Depth Mode")
        layout.prop(scene, "voxtart_reconstruction_shell_thickness")
        layout.prop(scene, "voxtart_reconstruction_material_policy")

        operator = layout.operator(VOXTART_OT_reconstruct_image.bl_idname, icon="IMAGE_DATA")
        operator.filepath = scene.voxtart_reconstruction_filepath
        operator.depth_filepath = scene.voxtart_reconstruction_depth_filepath
        operator.part_mask_filepath = scene.voxtart_reconstruction_part_mask_filepath
        operator.axis = scene.voxtart_reconstruction_axis
        operator.direction = scene.voxtart_reconstruction_direction
        operator.origin = tuple(scene.voxtart_reconstruction_origin)
        operator.depth = scene.voxtart_reconstruction_depth
        operator.priority = scene.voxtart_reconstruction_priority
        operator.background_mode = scene.voxtart_reconstruction_background
        operator.alpha_threshold = scene.voxtart_reconstruction_alpha_threshold
        operator.background_color = tuple(scene.voxtart_reconstruction_background_color)
        operator.background_tolerance = scene.voxtart_reconstruction_background_tolerance
        operator.max_colors = scene.voxtart_reconstruction_max_colors
        operator.mode = scene.voxtart_reconstruction_mode
        operator.shell_thickness = scene.voxtart_reconstruction_shell_thickness
        operator.material_policy = scene.voxtart_reconstruction_material_policy

        report = _report_from_object(context.active_object)
        if report is None:
            layout.label(text="No reconstruction report yet", icon="INFO")
            return
        report_box = layout.box()
        report_box.label(text="Last Result", icon="CHECKMARK")
        report_box.label(text=f"Voxels: {report.get('occupied_voxels', 0)}")
        report_box.label(text=f"Unresolved: {report.get('unresolved_voxels', 0)}")
        report_box.label(text=f"Conflicts: {report.get('conflicting_rays', 0)}")
        report_box.label(text=f"Missing pixels: {report.get('missing_pixels', 0)}")
        report_box.label(text=f"Extra pixels: {report.get('extra_pixels', 0)}")
        report_box.label(text=f"Color mismatches: {report.get('color_mismatches', 0)}")
        _draw_candidate_review(layout, context.active_object)


class VOXTART_PT_multiview_reconstruction(bpy.types.Panel):
    bl_label = "Multi-view Reconstruction"
    bl_idname = "VOXTART_PT_multiview_reconstruction"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 21

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        for slot in VIEW_SLOTS:
            box = layout.box()
            box.label(text=f"View {slot}", icon="IMAGE_DATA")
            box.prop(scene, _slot_property(slot, "filepath"), text="Image")
            box.prop(scene, _slot_property(slot, "depth_filepath"), text="Depth JSON")
            box.prop(scene, _slot_property(slot, "part_mask_filepath"), text="Parts JSON")
            row = box.row(align=True)
            row.prop(scene, _slot_property(slot, "axis"), text="Axis")
            row.prop(scene, _slot_property(slot, "direction"), text="Facing")
            row = box.row(align=True)
            row.prop(scene, _slot_property(slot, "origin"), text="Origin")
            row.prop(scene, _slot_property(slot, "depth"), text="Depth")
            row.prop(scene, _slot_property(slot, "priority"), text="Priority")
        layout.prop(scene, "voxtart_reconstruction_background", text="Background")
        layout.prop(scene, "voxtart_reconstruction_alpha_threshold")
        if scene.voxtart_reconstruction_background in {"CORNER", "EXPLICIT"}:
            layout.prop(scene, "voxtart_reconstruction_background_color")
            layout.prop(scene, "voxtart_reconstruction_background_tolerance")
        layout.prop(scene, "voxtart_reconstruction_max_colors")
        layout.prop(scene, "voxtart_reconstruction_mode", text="Depth Mode")
        layout.prop(scene, "voxtart_reconstruction_shell_thickness")
        layout.prop(scene, "voxtart_reconstruction_material_policy")
        layout.operator(VOXTART_OT_reconstruct_views.bl_idname, icon="IMAGE_DATA")

        report = _report_from_object(context.active_object)
        if report is None:
            layout.label(text="No reconstruction report yet", icon="INFO")
            return
        report_box = layout.box()
        report_box.label(text="Last Result", icon="CHECKMARK")
        report_box.label(text=f"Views: {report.get('view_count', 0)}")
        report_box.label(text=f"Voxels: {report.get('occupied_voxels', 0)}")
        report_box.label(text=f"Unresolved: {report.get('unresolved_voxels', 0)}")
        report_box.label(text=f"Conflicts: {report.get('conflicting_rays', 0)}")
        report_box.label(text=f"Missing pixels: {report.get('missing_pixels', 0)}")
        report_box.label(text=f"Extra pixels: {report.get('extra_pixels', 0)}")
        report_box.label(text=f"Color mismatches: {report.get('color_mismatches', 0)}")
        _draw_candidate_review(layout, context.active_object)




_CLASSES = (
    VOXTART_OT_reconstruct_image,
    VOXTART_OT_reconstruct_views,
    VOXTART_PT_reconstruction,
    VOXTART_PT_multiview_reconstruction,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_reconstruction_filepath = StringProperty(
        name="Reconstruction Image",
        subtype="FILE_PATH",
        default="",
    )
    bpy.types.Scene.voxtart_reconstruction_depth_filepath = StringProperty(
        name="Reconstruction Depth Observation",
        subtype="FILE_PATH",
        default="",
    )
    bpy.types.Scene.voxtart_reconstruction_part_mask_filepath = StringProperty(
        name="Reconstruction Part Mask",
        subtype="FILE_PATH",
        default="",
    )
    bpy.types.Scene.voxtart_reconstruction_axis = EnumProperty(
        name="Reconstruction Axis",
        items=_AXIS_ITEMS,
        default="Z",
    )
    bpy.types.Scene.voxtart_reconstruction_direction = EnumProperty(
        name="Reconstruction Facing",
        items=_DIRECTION_ITEMS,
        default="1",
    )
    bpy.types.Scene.voxtart_reconstruction_origin = IntVectorProperty(
        name="Reconstruction Origin",
        size=3,
        default=(0, 0, 0),
    )
    bpy.types.Scene.voxtart_reconstruction_depth = IntProperty(
        name="Reconstruction Depth",
        min=1,
        max=MAX_RECONSTRUCTION_CELLS,
        default=1,
    )
    bpy.types.Scene.voxtart_reconstruction_priority = IntProperty(
        name="Reconstruction Priority",
        default=0,
    )
    bpy.types.Scene.voxtart_reconstruction_background = EnumProperty(
        name="Reconstruction Background",
        items=_BACKGROUND_ITEMS,
        default="ALPHA",
    )
    bpy.types.Scene.voxtart_reconstruction_alpha_threshold = FloatProperty(
        name="Reconstruction Alpha Threshold",
        min=0.0,
        max=1.0,
        default=0.0,
    )
    bpy.types.Scene.voxtart_reconstruction_background_color = FloatVectorProperty(
        name="Reconstruction Background Color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0, 1.0),
    )
    bpy.types.Scene.voxtart_reconstruction_background_tolerance = FloatProperty(
        name="Reconstruction Background Tolerance",
        min=0.0,
        max=1.0,
        default=0.0,
    )
    bpy.types.Scene.voxtart_reconstruction_max_colors = IntProperty(
        name="Reconstruction Maximum Colors",
        min=1,
        max=256,
        default=256,
    )
    bpy.types.Scene.voxtart_reconstruction_mode = EnumProperty(
        name="Reconstruction Depth Mode",
        items=_MODE_ITEMS,
        default="SURFACE",
    )
    bpy.types.Scene.voxtart_reconstruction_shell_thickness = IntProperty(
        name="Reconstruction Shell Thickness",
        min=1,
        max=MAX_RECONSTRUCTION_CELLS,
        default=1,
    )
    bpy.types.Scene.voxtart_reconstruction_material_policy = EnumProperty(
        name="Reconstruction Material Policy",
        items=_MATERIAL_ITEMS,
        default="VISIBLE_ONLY",
    )
    for slot in VIEW_SLOTS:
        setattr(
            bpy.types.Scene,
            _slot_property(slot, "filepath"),
            StringProperty(name=f"View {slot} Image", subtype="FILE_PATH", default=""),
        )
        setattr(
            bpy.types.Scene,
            _slot_property(slot, "depth_filepath"),
            StringProperty(name=f"View {slot} Depth Observation", subtype="FILE_PATH", default=""),
        )
        setattr(
            bpy.types.Scene,
            _slot_property(slot, "part_mask_filepath"),
            StringProperty(name=f"View {slot} Part Mask", subtype="FILE_PATH", default=""),
        )
        setattr(
            bpy.types.Scene,
            _slot_property(slot, "axis"),
            EnumProperty(name=f"View {slot} Axis", items=_AXIS_ITEMS, default="Z"),
        )
        setattr(
            bpy.types.Scene,
            _slot_property(slot, "direction"),
            EnumProperty(name=f"View {slot} Facing", items=_DIRECTION_ITEMS, default="1"),
        )
        setattr(
            bpy.types.Scene,
            _slot_property(slot, "origin"),
            IntVectorProperty(name=f"View {slot} Origin", size=3, default=(0, 0, 0)),
        )
        setattr(
            bpy.types.Scene,
            _slot_property(slot, "depth"),
            IntProperty(
                name=f"View {slot} Depth",
                min=1,
                max=MAX_RECONSTRUCTION_CELLS,
                default=1,
            ),
        )
        setattr(
            bpy.types.Scene,
            _slot_property(slot, "priority"),
            IntProperty(name=f"View {slot} Priority", default=slot - 1),
        )


def unregister() -> None:
    for name in (
        "voxtart_reconstruction_material_policy",
        "voxtart_reconstruction_shell_thickness",
        "voxtart_reconstruction_mode",
        "voxtart_reconstruction_max_colors",
        "voxtart_reconstruction_background_tolerance",
        "voxtart_reconstruction_background_color",
        "voxtart_reconstruction_alpha_threshold",
        "voxtart_reconstruction_background",
        "voxtart_reconstruction_priority",
        "voxtart_reconstruction_depth",
        "voxtart_reconstruction_origin",
        "voxtart_reconstruction_direction",
        "voxtart_reconstruction_axis",
        "voxtart_reconstruction_part_mask_filepath",
        "voxtart_reconstruction_depth_filepath",
        "voxtart_reconstruction_filepath",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for slot in reversed(VIEW_SLOTS):
        for field in (
            "part_mask_filepath",
            "depth_filepath",
            "priority",
            "depth",
            "origin",
            "direction",
            "axis",
            "filepath",
        ):
            name = _slot_property(slot, field)
            if hasattr(bpy.types.Scene, name):
                delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "RECONSTRUCTION_ACCEPTANCE_PROPERTY",
    "RECONSTRUCTION_CANDIDATES_PROPERTY",
    "RECONSTRUCTION_EVIDENCE_PROPERTY",
    "RECONSTRUCTION_REPORT_PROPERTY",
    "RECONSTRUCTION_SELECTED_CANDIDATE_PROPERTY",
    "RECONSTRUCTION_SOURCE_PROPERTY",
    "RECONSTRUCTION_SOURCES_PROPERTY",
    "RECONSTRUCTION_UNRESOLVED_CELLS_PROPERTY",
    "RECONSTRUCTION_OVERLAY_PROPERTY",
    "RECONSTRUCTION_VIEW_PROPERTY",
    "RECONSTRUCTION_VIEWS_PROPERTY",
    "VOXTART_OT_reconstruct_image",
    "VOXTART_OT_reconstruct_views",
    "VOXTART_PT_reconstruction",
    "VOXTART_PT_multiview_reconstruction",
    "register",
    "unregister",
]
