# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""System-Python regression suite for Blender-independent VOXTART features.

Run from the repository root with::

    python tests/python/voxtart_core_test.py

The suite intentionally uses only the Python standard library so it can run
without a Blender binary. Blender-facing registration and operator behavior is
covered by ``voxtart_blender_test.py`` when executed inside Blender.
"""

from __future__ import annotations

import base64
import io
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "scripts" / "addons_core"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from voxtart import asset_placer, blob, blob_gallery, bloformer, blockbench_formats  # noqa: E402
from voxtart import edit, game_rig, io as asset_io, kitbash, layers, mcp_protocol, pick  # noqa: E402
from voxtart import mesh_optimize, meshing, minecraft_formats, minecraft_nbt  # noqa: E402
from voxtart import character_editor, depth_observations, geometry_skeleton, guide_prior, image_intake, optimize, optimizer, palette, palette_reduce, reconstruct, scene_assembly, scene_hierarchy, shading, spritesheet, stacking, symmetry, sprite_pipeline, sprite_sequence, tiler, voxel, voxel_brush, voxel_formats, voxelizer, volume_ops  # noqa: E402
from voxtart import metrics, provenance  # noqa: E402


PALETTE = [
    (0.1, 0.1, 0.1, 1.0),
    (0.4, 0.2, 0.1, 1.0),
    (0.2, 0.6, 0.3, 1.0),
    (0.8, 0.8, 0.8, 1.0),
]
EXPECTED_TESTS = 112


class VoxelBrushTest(unittest.TestCase):
    def test_line_box_sphere_and_mirror_cover_all_edges(self):
        self.assertEqual(
            voxel_brush.line_cells((0, 0, 0), (3, 0, 0)),
            ((0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)),
        )
        shell = voxel_brush.box_cells((0, 0, 0), (2, 2, 2), hollow=True)
        self.assertEqual(len(shell), 26)
        self.assertNotIn((1, 1, 1), shell)
        self.assertIn((0, 1, 1), shell)
        sphere = voxel_brush.sphere_cells((0, 0, 0), 1)
        self.assertEqual(len(sphere), 7)
        mirrored = voxel_brush.mirror_cells(
            ((0, 0, 0),),
            ((0, 0, 0), (2, 0, 0)),
            axes=(True, False, False),
        )
        self.assertEqual(mirrored, ((0, 0, 0), (2, 0, 0)))

    def test_selection_and_cylinder_are_deterministic(self):
        voxels = {
            (0, 0, 0): 1,
            (1, 0, 0): 1,
            (4, 0, 0): 1,
            (0, 0, 1): 2,
        }
        self.assertEqual(
            voxel_brush.select_color(voxels, (0, 0, 0)),
            ((0, 0, 0), (1, 0, 0)),
        )
        self.assertEqual(
            voxel_brush.select_box(voxels, (0, 0, 0), (1, 0, 1)),
            ((0, 0, 0), (0, 0, 1), (1, 0, 0)),
        )
        cylinder = voxel_brush.cylinder_cells((0, 0, 0), 1, 3)
        self.assertEqual(len(cylinder), 15)

    def test_face_and_projected_selection_tools_are_deterministic(self):
        face = voxel_brush.face_brush_cells((1, 0, 0), (1, 0, 0), 2, depth=2)
        self.assertEqual(len(face), 18)
        self.assertIn((2, 1, 1), face)
        self.assertNotIn((0, 0, 0), face)
        voxels = {
            (0, 0, 0): 1,
            (1, 0, 0): 1,
            (0, 1, 0): 1,
            (4, 0, 0): 1,
        }
        self.assertEqual(
            voxel_brush.select_connected(voxels, (0, 0, 0)),
            ((0, 0, 0), (0, 1, 0), (1, 0, 0)),
        )
        self.assertEqual(
            voxel_brush.select_flat_surface(
                {(0, 0, 0): 1, (0, 1, 0): 1, (1, 0, 0): 1, (1, 1, 0): 1},
                (0, 0, 0),
                (-1, 0, 0),
            ),
            ((0, 0, 0), (0, 1, 0)),
        )
        projected = {
            (0, 0, 0): (1.0, 1.0),
            (1, 0, 0): (4.0, 1.0),
            (2, 0, 0): (9.0, 9.0),
        }
        self.assertEqual(
            voxel_brush.select_lasso(projected, ((0, 0), (5, 0), (5, 5), (0, 5))),
            ((0, 0, 0), (1, 0, 0)),
        )
        self.assertEqual(
            voxel_brush.select_circle(projected, (1.0, 1.0), 1.1),
            ((0, 0, 0),),
        )
def make_asset(cells=None, *, palette_values=None, sockets=()):
    if cells is None:
        cells = {(0, 0, 0): 0}
    return voxel.VoxelAsset(
        grid_size=1.0,
        palette=list(palette_values or PALETTE),
        voxels=dict(cells),
        sockets=list(sockets),
        provenance={"test": "true"},
    )


def solid_cells(size: int, palette_index: int = 0):
    offset = -(size // 2)
    return {
        (offset + x, offset + y, offset + z): palette_index
        for x in range(size)
        for y in range(size)
        for z in range(size)
    }


class VoxelCoreTest(unittest.TestCase):
    def test_socket_and_asset_lifecycle(self):
        socket = voxel.VoxelSocket((1, 2, 3), (0, 0, 1), "head", 90)
        asset = make_asset({(1, 2, 3): 0}, sockets=[socket])
        self.assertEqual(asset.get_voxel((1, 2, 3)), 0)
        self.assertEqual(asset.bounds, ((1, 2, 3), (1, 2, 3)))
        self.assertEqual(asset.sockets[0].to_dict()["rotation"], 90)
        extra = asset.add_palette_color((0.5, 0.5, 0.5, 1.0))
        self.assertEqual(extra, 4)
        asset.set_voxel((2, 2, 2), extra)
        asset.remove_voxel((1, 2, 3))
        restored = voxel.VoxelAsset.from_dict(asset.to_dict())
        self.assertEqual(restored.voxels, asset.voxels)
        self.assertEqual(restored.provenance, {"test": "true"})
        self.assertEqual(len(voxel.VoxelAsset.box(2).voxels), 8)

    def test_palette_materials_round_trip_and_append(self):
        asset = make_asset({(0, 0, 0): 0})
        asset.set_palette_material(0, voxel.PaletteMaterial.from_preset("GLASS"))
        asset.set_palette_material(
            1,
            voxel.PaletteMaterial(
                kind="CUSTOM",
                alpha=0.7,
                metallic=0.8,
                roughness=0.15,
                transmission=0.2,
                emission_strength=2.5,
                ior=1.6,
            ),
        )
        payload = asset.to_dict()
        self.assertEqual(payload["palette_materials"][0]["kind"], "GLASS")
        self.assertEqual(asset.provenance, {"test": "true"})
        restored = voxel.VoxelAsset.from_dict(payload)
        self.assertEqual(restored.get_palette_materials(), asset.get_palette_materials())
        self.assertEqual(restored.provenance, asset.provenance)
        new_index = asset.add_palette_color((0.5, 0.5, 0.5, 1.0))
        self.assertEqual(new_index, 4)
        self.assertEqual(asset.get_palette_materials()[-1], voxel.PaletteMaterial())
        with self.assertRaises(ValueError):
            voxel.PaletteMaterial(metallic=1.1)
        with self.assertRaises(ValueError):
            voxel.PaletteMaterial.from_preset("UNKNOWN")

    def test_typed_part_assignments_round_trip_with_asset(self):
        asset = make_asset({(0, 0, 0): 0, (1, 0, 0): 1})
        asset.set_part((0, 0, 0), "TORSO")
        asset.set_part((1, 0, 0), "HEAD")
        restored = voxel.VoxelAsset.from_dict(asset.to_dict())
        self.assertEqual(restored.part_assignments, asset.part_assignments)
        self.assertEqual(json.loads(restored.provenance["part_assignments"]), [
            [[0, 0, 0], "TORSO"],
            [[1, 0, 0], "HEAD"],
        ])
        asset.remove_voxel((1, 0, 0))
        self.assertNotIn((1, 0, 0), asset.part_assignments)
        with self.assertRaises(KeyError):
            asset.set_part((2, 0, 0), "HAND_L")

    def test_invalid_core_values(self):
        with self.assertRaises(ValueError):
            voxel.VoxelSocket((0, 0, 0), (0, 0, 0))
        with self.assertRaises(ValueError):
            voxel.VoxelSocket((0, 0, 0), (1, 0, 0), rotation=45)
        with self.assertRaises(ValueError):
            voxel.VoxelSocket((0, 0, 0), (1, 0, 0), rotation=90.0)
        with self.assertRaises(ValueError):
            voxel.VoxelAsset(grid_size=0.0)
        for invalid_grid_size in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                voxel.VoxelAsset(grid_size=invalid_grid_size)
        with self.assertRaises(ValueError):
            voxel.VoxelAsset(palette=[(float("nan"), 0.0, 0.0, 1.0)])
        with self.assertRaises(ValueError):
            voxel.VoxelAsset.from_dict({"schema_version": True, "grid_size": 1, "palette": PALETTE})
        with self.assertRaises(ValueError):
            voxel.VoxelAsset.from_dict({"schema_version": 1, "grid_size": 1, "palette": PALETTE, "voxels": [[0, 0, 0, 1.5]]})
        with self.assertRaises(ValueError):
            voxel.VoxelAsset(palette=[(object(), 0.0, 0.0, 1.0)])
        with self.assertRaises(ValueError):
            voxel.VoxelAsset(grid_size=object())
        with self.assertRaises(ValueError):
            make_asset().set_voxel((0, 0, 0), 99)




class VoxelizerCoreTest(unittest.TestCase):
    def test_surface_and_shell_modes_preserve_palette_indices(self):
        source = solid_cells(5, palette_index=2)
        surface = voxelizer.filter_voxels(source, mode="SURFACE")
        shell = voxelizer.filter_voxels(source, mode="SHELL", shell_thickness=2)
        self.assertEqual(len(source), 125)
        self.assertEqual(len(surface), 98)
        self.assertEqual(len(shell), 124)
        self.assertTrue(all(index == 2 for index in surface.values()))
        self.assertEqual(len(voxelizer.filter_voxels(source, mode="SOLID")), 125)
        asset = voxel.VoxelAsset(
            grid_size=0.25,
            palette=[PALETTE[0], PALETTE[1], PALETTE[2]],
            voxels=source,
            provenance={"source": "test"},
        )
        filtered = voxelizer.apply_voxelizer_mode(
            asset,
            mode="SURFACE",
            shell_thickness=3,
        )
        self.assertEqual(len(filtered.voxels), 98)
        self.assertEqual(filtered.grid_size, 0.25)
        self.assertEqual(filtered.palette, asset.palette)
        self.assertEqual(filtered.provenance["voxelizer_mode"], "SURFACE")
        self.assertEqual(filtered.provenance["voxelizer_shell_thickness"], "3")
        self.assertEqual(len(asset.voxels), 125)

    def test_voxelizer_validation_and_thick_shell_boundaries(self):
        with self.assertRaises(ValueError):
            voxelizer.filter_voxels({}, mode="UNKNOWN")
        with self.assertRaises(ValueError):
            voxelizer.filter_voxels({}, mode="SHELL", shell_thickness=0)
        with self.assertRaises(ValueError):
            voxelizer.filter_voxels({}, mode="SHELL", shell_thickness=1.5)
        with self.assertRaises(ValueError):
            voxelizer.filter_voxels({}, mode="SHELL", shell_thickness=True)
        self.assertEqual(
            len(voxelizer.filter_voxels(solid_cells(3), mode="SHELL", shell_thickness=3)),
            27,
        )
        single = {(0, 0, 0): 7}
        self.assertEqual(voxelizer.filter_voxels(single, mode="SURFACE"), single)

class VolumeOpsTest(unittest.TestCase):
    def test_mirror_rotate_translate_and_crop_preserve_contracts(self):
        asset = make_asset(
            {
                (0, 0, 0): 1,
                (1, 0, 0): 2,
            },
            sockets=[voxel.VoxelSocket((1, 0, 0), (1, 0, 0), "edge")],
        )
        mirrored = volume_ops.mirror_asset(asset, "X")
        self.assertEqual(mirrored.voxels, {(1, 0, 0): 1, (0, 0, 0): 2})
        self.assertEqual(mirrored.sockets[0].position, (0, 0, 0))
        self.assertEqual(mirrored.sockets[0].normal, (-1, 0, 0))
        rotated = volume_ops.rotate_asset(asset, "Z", 90)
        self.assertEqual(rotated.voxels, {(0, 0, 0): 1, (0, 1, 0): 2})
        self.assertEqual(rotated.sockets[0].position, (0, 1, 0))
        self.assertEqual(rotated.sockets[0].normal, (0, 1, 0))
        translated = volume_ops.translate_asset(asset, (-3, 2, 1))
        self.assertEqual(translated.voxels, {(-3, 2, 1): 1, (-2, 2, 1): 2})
        cropped = volume_ops.crop_asset(
            translated,
            minimum=(-3, 2, 1),
            maximum=(-2, 2, 1),
            rebase=True,
        )
        self.assertEqual(cropped.voxels, {(0, 0, 0): 1, (1, 0, 0): 2})
        self.assertEqual(cropped.grid_size, asset.grid_size)
        self.assertEqual(asset.voxels, {(0, 0, 0): 1, (1, 0, 0): 2})

    def test_split_and_transform_validation(self):
        asset = make_asset({(-1, 0, 0): 0, (0, 0, 0): 1, (4, 0, 0): 2})
        chunks = volume_ops.split_asset(asset, 4)
        self.assertEqual(tuple(chunks), ((-4, 0, 0), (0, 0, 0), (4, 0, 0)))
        self.assertEqual(chunks[(-4, 0, 0)].voxels, {(3, 0, 0): 0})
        self.assertEqual(chunks[(0, 0, 0)].voxels, {(0, 0, 0): 1})
        self.assertEqual(chunks[(4, 0, 0)].voxels, {(0, 0, 0): 2})
        with self.assertRaises(ValueError):
            volume_ops.rotate_asset(asset, "X", 45)
        with self.assertRaises(ValueError):
            volume_ops.translate_asset(asset, (0, 0, 1.5))
        with self.assertRaises(ValueError):
            volume_ops.split_asset(asset, 0)

    def test_all_rotation_axes_empty_bounds_and_crop_edges(self):
        asset = make_asset(
            {
                (0, 0, 0): 0,
                (1, 0, 0): 1,
                (0, 1, 0): 2,
                (0, 0, 1): 3,
            },
            sockets=[voxel.VoxelSocket((1, 1, 1), (1, 0, 0), "corner")],
        )
        for axis in volume_ops.TRANSFORM_AXES:
            for degrees in volume_ops.ROTATION_DEGREES:
                rotated = volume_ops.rotate_asset(asset, axis, degrees)
                self.assertEqual(len(rotated.voxels), len(asset.voxels))
        self.assertEqual(volume_ops.rotate_asset(asset, "X", 0).voxels, asset.voxels)
        reference = volume_ops.mirror_asset(
            asset,
            "Y",
            reference_bounds=((0, 0, 0), (2, 2, 2)),
        )
        self.assertEqual(reference.bounds, ((0, 1, 0), (1, 2, 1)))
        empty = voxel.VoxelAsset(palette=list(PALETTE))
        self.assertEqual(volume_ops.mirror_asset(empty, "X").voxels, {})
        self.assertEqual(volume_ops.rotate_asset(empty, "Z", 90).voxels, {})
        compact = volume_ops.crop_asset(asset, rebase=True)
        self.assertEqual(compact.bounds, ((0, 0, 0), (1, 1, 1)))
        retained = volume_ops.crop_asset(
            asset,
            minimum=(0, 0, 0),
            maximum=(1, 1, 1),
            rebase=False,
        )
        self.assertEqual(retained.voxels, asset.voxels)
        self.assertEqual(volume_ops.crop_asset(empty, rebase=True).voxels, {})
        with self.assertRaises(ValueError):
            volume_ops.mirror_asset(asset, "Q")
        with self.assertRaises(ValueError):
            volume_ops.mirror_asset(asset, "X", reference_bounds=((1, 0, 0), (0, 0, 0)))
        with self.assertRaises(ValueError):
            volume_ops.crop_asset(asset, minimum=(0, 0, 0))

    def test_split_carries_local_sockets(self):
        source = make_asset(
            {(-1, 0, 0): 0, (4, 0, 0): 1},
            sockets=[
                voxel.VoxelSocket((-1, 0, 0), (-1, 0, 0), "left"),
                voxel.VoxelSocket((4, 0, 0), (1, 0, 0), "right"),
            ],
        )
        chunks = volume_ops.split_asset(source, 4)
        self.assertEqual(chunks[(-4, 0, 0)].sockets[0].position, (3, 0, 0))
        self.assertEqual(chunks[(4, 0, 0)].sockets[0].position, (0, 0, 0))
        with self.assertRaises(ValueError):
            volume_ops.split_asset(source, True)


class PaletteReductionTest(unittest.TestCase):
    def test_weighted_median_cut_is_deterministic_and_bounded(self):
        colors = (
            (0.0, 0.0, 0.0, 1.0),
            (1.0, 1.0, 1.0, 1.0),
            (1.0, 0.0, 0.0, 1.0),
            (0.0, 1.0, 0.0, 1.0),
        )
        first = palette_reduce.reduce_palette(colors, 2)
        second = palette_reduce.reduce_palette(colors, 2)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertTrue(all(len(color) == 4 for color in first))
        with self.assertRaises(ValueError):
            palette_reduce.reduce_palette(colors, 0)
        with self.assertRaises(ValueError):
            palette_reduce.reduce_palette(colors, 2, method="KMEANS")

    def test_palette_reduction_boundary_inputs(self):
        colors = (
            (0.0, 0.0, 0.0, 1.0),
            (1.0, 1.0, 1.0, 1.0),
            (1.0, 0.0, 0.0, 1.0),
            (0.0, 1.0, 0.0, 1.0),
        )
        self.assertEqual(palette_reduce.reduce_palette(colors, 4), list(colors))
        palette_reduce.reduce_palette(
            (*colors, (0.0, 0.0, 1.0, 1.0)),
            3,
            weights=(100.0, 1.0, 1.0, 1.0, 1.0),
        )
        with self.assertRaises(ValueError):
            palette_reduce.reduce_palette((), 2)
        with self.assertRaises(ValueError):
            palette_reduce.reduce_palette(colors, 2, weights=(1.0,))
        with self.assertRaises(ValueError):
            palette_reduce.reduce_palette(colors, 2, weights=(1.0, -1.0, 1.0, 1.0))
        point = palette_reduce._ColorPoint(colors[0], 1.0, 0)
        self.assertIsNone(palette_reduce._box_split((point,), (0,)))
        self.assertEqual(palette_reduce._box_split((point, point), (0, 1)), ((0,), (1,)))
        report = palette_reduce.PaletteReductionReport(1, 1, 0, 0, 0, "MEDIAN_CUT")
        self.assertFalse(report.changed)
        self.assertFalse(report.to_dict()["changed"])

    def test_empty_asset_palette_reduction(self):
        empty = voxel.VoxelAsset(palette=[
            (0.0, 0.0, 0.0, 1.0),
            (1.0, 1.0, 1.0, 1.0),
            (1.0, 0.0, 0.0, 1.0),
        ])
        reduced, report = palette_reduce.reduce_asset_palette(empty, 2)
        self.assertEqual(reduced.voxels, {})
        self.assertEqual(report.source_voxels, 0)
        self.assertLessEqual(len(reduced.palette), 2)

    def test_asset_reduction_preserves_cells_scale_sockets_and_provenance(self):
        asset = voxel.VoxelAsset(
            grid_size=0.25,
            palette=[
                (0.0, 0.0, 0.0, 1.0),
                (1.0, 1.0, 1.0, 1.0),
                (1.0, 0.0, 0.0, 1.0),
                (0.0, 1.0, 0.0, 1.0),
            ],
            voxels={
                (0, 0, 0): 0,
                (1, 0, 0): 1,
                (2, 0, 0): 2,
                (3, 0, 0): 3,
                (4, 0, 0): 3,
            },
            sockets=[voxel.VoxelSocket((4, 0, 0), (1, 0, 0), "end")],
            provenance={"source": "test"},
        )
        reduced, report = palette_reduce.reduce_asset_palette(asset, 2)
        self.assertEqual(set(reduced.voxels), set(asset.voxels))
        self.assertEqual(reduced.grid_size, asset.grid_size)
        self.assertEqual(reduced.sockets, asset.sockets)
        self.assertEqual(reduced.provenance["palette_reduction_method"], "MEDIAN_CUT")
        self.assertLessEqual(len(reduced.palette), 2)
        self.assertEqual(report.source_voxels, report.target_voxels)
        self.assertEqual(asset.palette[0], (0.0, 0.0, 0.0, 1.0))


class TilerTest(unittest.TestCase):
    def _atlas(self):
        return tiler.PixelTexture.from_rgba(
            4,
            2,
            (
                (1.0, 0.0, 0.0, 1.0),
                (0.0, 0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0, 1.0),
                (0.0, 0.0, 1.0, 0.5),
                (1.0, 1.0, 0.0, 1.0),
                (0.0, 0.0, 0.0, 0.0),
                (1.0, 0.0, 1.0, 1.0),
                (0.0, 1.0, 1.0, 1.0),
            ),
        )

    def test_atlas_projection_scale_and_conversion(self):
        atlas = self._atlas()
        tile = tiler.extract_tile(atlas, 2, 2, 1)
        self.assertEqual(tile.origin, (2, 0))
        self.assertEqual(tile.uv_rect(), (0.5, 0.0, 1.0, 1.0))
        self.assertEqual(
            tiler.tile_uvs(
                tile,
                ((0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.5)),
                tiler.TilerSettings(pixel_scale=0.25),
            ),
            ((0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)),
        )
        self.assertEqual(
            tiler.tile_uvs(
                tile,
                ((0.0, 0.0), (1.0, 0.0), (1.0, 0.5), (0.0, 0.5)),
                tiler.TilerSettings(pixel_scale=0.25),
            ),
            ((0.5, 0.0), (1.5, 0.0), (1.5, 1.0), (0.5, 1.0)),
        )
        self.assertEqual(
            tiler.tile_uvs(
                tile,
                ((0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.5)),
                tiler.TilerSettings(pixel_scale=0.25, rotation=90),
            ),
            ((1.0, 0.0), (1.0, 1.0), (0.5, 1.0), (0.5, 0.0)),
        )
        for rotation, expected in (
            (180, ((1.0, 1.0), (0.5, 1.0), (0.5, 0.0), (1.0, 0.0))),
            (270, ((0.5, 1.0), (0.5, 0.0), (1.0, 0.0), (1.0, 1.0))),
        ):
            self.assertEqual(
                tiler.tile_uvs(
                    tile,
                    ((0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.5)),
                    tiler.TilerSettings(pixel_scale=0.25, rotation=rotation),
                ),
                expected,
            )
        flipped = tiler.tile_uvs(
            tile,
            ((0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.5)),
            tiler.TilerSettings(pixel_scale=0.25, flip_x=True, flip_y=True),
        )
        self.assertEqual(flipped, ((1.0, 1.0), (0.5, 1.0), (0.5, 0.0), (1.0, 0.0)))
        with self.assertRaises(ValueError):
            tiler.tile_uvs(
                tile,
                ((0.0, 0.0), (0.5, 0.0)),
                tiler.TilerSettings(pixel_scale=0.25),
            )
        with self.assertRaises(ValueError):
            tiler.tile_uvs(
                tile,
                ((0.0, 0.0), (0.0, 0.5), (0.0, 1.0)),
                tiler.TilerSettings(pixel_scale=0.25),
            )
        settings = tiler.TilerSettings(pixel_scale=0.25, axis="Z", depth=2)
        projection = tiler.project_tile(tile, (3, 4, 5), settings)
        self.assertEqual(projection.dimensions, (2, 2, 2))
        self.assertEqual(projection.world_size, (0.5, 0.5, 0.5))
        self.assertEqual(len(projection.cells), 8)
        asset = voxel.VoxelAsset(grid_size=0.25, palette=list(PALETTE))
        report = tiler.apply_tile(asset, tile, (3, 4, 5), settings)
        self.assertEqual(report.projected, 8)
        self.assertEqual(report.footprint, 8)
        self.assertEqual(len(asset.voxels), 8)
        converted, converted_report = tiler.tile_to_voxel_asset(
            tile,
            settings,
            palette=PALETTE,
            anchor=(0, 0, 0),
        )
        self.assertEqual(converted.grid_size, 0.25)
        self.assertEqual(converted_report.world_size, (0.5, 0.5, 0.5))
        self.assertEqual(converted.provenance["workflow"], "pixel_tiler")

    def test_pixel_grouping_keeps_pixel_art_crisp_and_coarse(self):
        red = (1.0, 0.0, 0.0, 1.0)
        green = (0.0, 1.0, 0.0, 1.0)
        blue = (0.0, 0.0, 1.0, 1.0)
        tile = tiler.Tile(
            pixels=tiler.PixelTexture(
                4,
                2,
                (
                    red,
                    red,
                    (0.0, 0.0, 0.0, 0.0),
                    blue,
                    red,
                    green,
                    (0.0, 0.0, 0.0, 0.0),
                    blue,
                ),
            ),
            index=0,
            atlas_size=(4, 2),
            origin=(0, 0),
        )
        settings = tiler.TilerSettings(pixel_scale=0.5, pixels_per_voxel=2)
        projection = tiler.project_tile(tile, (0, 0, 0), settings)
        self.assertEqual(projection.dimensions, (2, 1, 1))
        self.assertEqual(projection.world_size, (1.0, 0.5, 0.5))
        self.assertEqual(len(projection.cells), 2)
        self.assertEqual(projection.cells[(0, 0, 0)], red)
        self.assertEqual(projection.cells[(1, 0, 0)], blue)
        converted, report = tiler.tile_to_voxel_asset(
            tile,
            settings,
            palette=[red, green, blue],
        )
        self.assertEqual(len(converted.voxels), 2)
        self.assertEqual(report.projected, 2)
        self.assertEqual(converted.provenance["pixels_per_voxel"], "2")

    def test_seamless_generation_and_voxel_conversion(self):
        color_a = (0.1, 0.2, 0.3, 1.0)
        color_b = (0.8, 0.7, 0.6, 1.0)
        for pattern in tiler.SEAMLESS_PATTERNS:
            texture = tiler.generate_seamless_texture(
                7,
                5,
                pattern=pattern,
                color_a=color_a,
                color_b=color_b,
                seed=19,
                repeat_x=3,
                repeat_y=2,
            )
            self.assertEqual(texture.width, 7)
            self.assertEqual(texture.height, 5)
            for x in range(texture.width):
                self.assertEqual(texture.pixel(x, 0), texture.pixel(x, texture.height - 1))
            for y in range(texture.height):
                self.assertEqual(texture.pixel(0, y), texture.pixel(texture.width - 1, y))
            self.assertEqual(
                texture,
                tiler.generate_seamless_texture(
                    7,
                    5,
                    pattern=pattern,
                    color_a=color_a,
                    color_b=color_b,
                    seed=19,
                    repeat_x=3,
                    repeat_y=2,
                ),
            )
        noise = tiler.generate_seamless_texture(
            7,
            5,
            pattern="NOISE",
            color_a=color_a,
            color_b=color_b,
            seed=19,
            repeat_x=3,
            repeat_y=2,
        )
        self.assertNotEqual(
            noise.pixels,
            tiler.generate_seamless_texture(
                7,
                5,
                pattern="NOISE",
                color_a=color_a,
                color_b=color_b,
                seed=20,
                repeat_x=3,
                repeat_y=2,
            ).pixels,
        )
        brick = tiler.generate_seamless_tile(
            7,
            5,
            pattern="BRICK",
            color_a=color_a,
            color_b=color_b,
            repeat_x=3,
            repeat_y=2,
        )
        self.assertEqual(brick.uv_rect(), (0.0, 0.0, 1.0, 1.0))
        self.assertIn(color_a, brick.pixels.pixels)
        self.assertIn(color_b, brick.pixels.pixels)
        generated_asset, generated_report = tiler.generate_seamless_voxel_asset(
            4,
            3,
            tiler.TilerSettings(pixel_scale=0.5, axis="Z", depth=2),
            pattern="STRIPES",
            color_a=color_a,
            color_b=color_b,
            repeat_x=2,
            repeat_y=1,
            anchor=(2, 3, 4),
        )
        self.assertEqual(generated_asset.grid_size, 0.5)
        self.assertEqual(len(generated_asset.voxels), 24)
        self.assertEqual(generated_report.world_size, (2.0, 1.5, 1.0))
        self.assertEqual(generated_asset.provenance["generator"], "seamless")
        self.assertEqual(generated_asset.provenance["pattern"], "STRIPES")
        with self.assertRaises(ValueError):
            tiler.generate_seamless_texture(tiler.MAX_TILE_PIXELS + 1, 1)
        with self.assertRaises(ValueError):
            tiler.generate_seamless_texture(0, 4)
        with self.assertRaises(ValueError):
            tiler.generate_seamless_texture(4, 4, pattern="UNKNOWN")
        with self.assertRaises(ValueError):
            tiler.generate_seamless_texture(4, 4, repeat_x=0)
        with self.assertRaises(ValueError):
            tiler.generate_seamless_texture(4, 4, repeat_y=1.5)
        with self.assertRaises(ValueError):
            tiler.generate_seamless_texture(4, 4, seed=True)

    def test_axis_rotation_flip_replace_and_contract(self):
        tile = tiler.extract_tile(self._atlas(), 2, 2, 0)
        for axis in tiler.TILER_AXES:
            projection = tiler.project_tile(
                tile,
                (0, 0, 0),
                tiler.TilerSettings(pixel_scale=1.0, axis=axis, side=-1, depth=2),
            )
            self.assertEqual(len(projection.footprint), 8)
        for rotation in tiler.TILER_ROTATIONS:
            projection = tiler.project_tile(
                tile,
                (0, 0, 0),
                tiler.TilerSettings(pixel_scale=1.0, rotation=rotation, flip_x=True, flip_y=True),
            )
            self.assertEqual(len(projection.footprint), 4)
        replace_asset = voxel.VoxelAsset(
            grid_size=1.0,
            palette=list(PALETTE),
            voxels={(0, 0, 0): 0, (5, 5, 5): 0},
        )
        replace_tile = tiler.Tile(
            tiler.PixelTexture(1, 1, ((1.0, 0.0, 0.0, 1.0),)),
            index=7,
            atlas_size=(1, 1),
            origin=(0, 0),
        )
        replace_report = tiler.apply_tile(
            replace_asset,
            replace_tile,
            (0, 0, 0),
            tiler.TilerSettings(pixel_scale=1.0, mode="REPLACE"),
        )
        self.assertEqual((replace_report.added, replace_report.removed), (1, 1))
        self.assertEqual(len(replace_asset.voxels), 2)
        paint_asset = voxel.VoxelAsset(
            grid_size=1.0,
            palette=[(1.0, 0.0, 0.0, 1.0)],
            voxels={(0, 0, 0): 0},
        )
        paint_report = tiler.apply_tile(
            paint_asset,
            tiler.Tile(
                tiler.PixelTexture(1, 1, ((0.0, 1.0, 0.0, 1.0),)),
                index=8,
                atlas_size=(1, 1),
                origin=(0, 0),
            ),
            (0, 0, 0),
            tiler.TilerSettings(pixel_scale=1.0),
        )
        self.assertEqual(paint_report.painted, 1)
        with self.assertRaises(ValueError):
            tiler.ensure_pixel_scale(replace_asset, 0.5)

    def test_tiler_validation_boundaries(self):
        with self.assertRaises(ValueError):
            tiler.PixelTexture(2, 2, ((1.0, 0.0, 0.0, 1.0),))
        with self.assertRaises(ValueError):
            tiler.extract_tile(self._atlas(), 3, 2, 0)
        with self.assertRaises(ValueError):
            tiler.extract_tile(self._atlas(), 2, 2, 4)
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=0.0)
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=1.0, axis="Q")
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=1.0, side=0)
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=1.0, depth=0)
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=1.0, rotation=45)
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=1.0, side=1.5)
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=1.0, depth=1.5)
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=1.0, rotation=90.5)
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=1.0, mode="PAINT")
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=1.0, pixels_per_voxel=0)
        with self.assertRaises(ValueError):
            tiler.TilerSettings(
                pixel_scale=1.0,
                pixels_per_voxel=tiler.MAX_PIXELS_PER_VOXEL + 1,
            )
        with self.assertRaises(ValueError):
            tiler.tile_to_voxel_asset(
                tiler.extract_tile(self._atlas(), 2, 2, 0),
                tiler.TilerSettings(pixel_scale=1.0),
                palette=[],
            )

        self.assertEqual(
            tiler.PixelTexture.from_rgba(1, 1, (1.0, 0.0, 0.0, 1.0)).pixel(-1, 0),
            (0.0, 0.0, 0.0, 0.0),
        )
        with self.assertRaises(ValueError):
            tiler._color((1.0, 0.0, 0.0))
        with self.assertRaises(ValueError):
            tiler._color((2.0, 0.0, 0.0, 1.0))
        with self.assertRaises(ValueError):
            tiler._cell((True, 0, 0))
        with self.assertRaises(ValueError):
            tiler._positive_dimension(True, "dimension")
        with self.assertRaises(ValueError):
            tiler.PixelTexture(tiler.MAX_TILE_PIXELS + 1, 1, ())
        with self.assertRaises(ValueError):
            tiler.TilerSettings(pixel_scale=1.0, alpha_threshold=2.0)
        full_palette = [
            (index / 255.0, ((index * 5) % 256) / 255.0, ((index * 11) % 256) / 255.0, 1.0)
            for index in range(tiler.MAX_PALETTE_COLORS)
        ]
        full_asset = voxel.VoxelAsset(grid_size=1.0, palette=full_palette)
        unique_tile = tiler.Tile(
            tiler.PixelTexture(1, 1, ((0.123, 0.234, 0.345, 1.0),)),
            index=0,
            atlas_size=(1, 1),
            origin=(0, 0),
        )
        tiler.apply_tile(full_asset, unique_tile, (0, 0, 0), tiler.TilerSettings(pixel_scale=1.0))



class SpritesheetTest(unittest.TestCase):
    def _frame(self, color):
        return tiler.PixelTexture.from_rgba(
            2,
            1,
            (
                color,
                (0.0, 0.0, 0.0, 0.0),
            ),
        )

    def test_frame_sampling_and_row_major_composition(self):
        self.assertEqual(spritesheet.sample_frame_range(2, 7, 2), (2, 4, 6))
        frame_a = self._frame((1.0, 0.0, 0.0, 1.0))
        frame_b = self._frame((0.0, 1.0, 0.0, 1.0))
        frame_c = self._frame((0.0, 0.0, 1.0, 1.0))
        result = spritesheet.compose_spritesheet(
            (frame_a, frame_b, frame_c),
            scene_frames=(10, 20, 30),
            columns=2,
            spacing=1,
            padding=1,
        )
        self.assertEqual(result.layout.size, (7, 5))
        self.assertEqual(result.layout.origin(0), (1, 1))
        self.assertEqual(result.layout.origin(2), (1, 3))
        self.assertEqual(result.texture.pixel(1, 1), frame_a.pixel(0, 0))
        self.assertEqual(result.texture.pixel(4, 1), frame_b.pixel(0, 0))
        self.assertEqual(result.texture.pixel(1, 3), frame_c.pixel(0, 0))
        self.assertEqual(result.to_dict()["frames"][1]["scene_frame"], 20)
        self.assertEqual(result.to_dict()["layout"]["rows"], 2)

        auto = spritesheet.SpritesheetLayout(2, 1, 5)
        self.assertEqual((auto.columns, auto.rows), (3, 2))
        self.assertEqual(spritesheet.compose_spritesheet((frame_a,)).scene_frames, (0,))

    def test_layout_and_composition_boundaries(self):
        with self.assertRaises(ValueError):
            spritesheet.sample_frame_range(4, 1)
        with self.assertRaises(ValueError):
            spritesheet.sample_frame_range(1, 2, 0)
        with self.assertRaises(ValueError):
            spritesheet.sample_frame_range(True, 2)
        with self.assertRaises(ValueError):
            spritesheet.sample_frame_range(1, 2.5)
        with self.assertRaises(ValueError):
            spritesheet.sample_frame_range(0, spritesheet.MAX_SPRITESHEET_FRAMES)
        with self.assertRaises(ValueError):
            spritesheet.SpritesheetLayout(0, 1, 1)
        with self.assertRaises(ValueError):
            spritesheet.SpritesheetLayout(1, 1, 1, columns=2)
        with self.assertRaises(ValueError):
            spritesheet.SpritesheetLayout(2048, 2048, 2)
        with self.assertRaises(ValueError):
            spritesheet.SpritesheetLayout(8193, 1, 1)
        with self.assertRaises(ValueError):
            spritesheet.SpritesheetLayout(1, 1, spritesheet.MAX_SPRITESHEET_FRAMES + 1)
        layout = spritesheet.SpritesheetLayout(1, 1, 1)
        with self.assertRaises(ValueError):
            layout.origin(1)
        with self.assertRaises(ValueError):
            spritesheet.compose_spritesheet(())
        with self.assertRaises(TypeError):
            spritesheet.compose_spritesheet((object(),))
        with self.assertRaises(ValueError):
            spritesheet.compose_spritesheet((self._frame((1, 0, 0, 1)), tiler.PixelTexture.from_rgba(1, 1, ((1, 1, 1, 1),))))
        with self.assertRaises(ValueError):
            spritesheet.compose_spritesheet((self._frame((1, 0, 0, 1)),), scene_frames=(1, 2))

        result = spritesheet.compose_spritesheet((self._frame((1, 0, 0, 1)),))
        with self.assertRaises(ValueError):
            spritesheet.SpritesheetResult(result.texture, result.layout, ())
        with self.assertRaises(ValueError):
            spritesheet.SpritesheetResult(
                tiler.PixelTexture.from_rgba(1, 1, ((1, 1, 1, 1),)),
                result.layout,
                (0,),
            )
class EditTest(unittest.TestCase):
    def test_transaction_operations_reports_and_protection(self):
        asset = make_asset({(0, 0, 0): 0})
        with edit.VoxelEditTransaction(asset, protected={(0, 0, 0)}) as transaction:
            transaction.add((0, 0, 0), 1, overwrite=False)
            transaction.add((1, 0, 0), 1)
            transaction.paint((1, 0, 0), 2)
            transaction.remove((0, 0, 0))
            transaction.fill_box((2, 0, 0), (2, 0, 1), 3)
            report = transaction.commit()
        self.assertEqual(report.added, 3)
        self.assertEqual(report.painted, 1)
        self.assertEqual(report.skipped_protected, 2)
        self.assertEqual(asset.get_voxel((1, 0, 0)), 2)
        self.assertEqual(asset.get_voxel((2, 0, 1)), 3)

    def test_flood_fill_components_and_rollback(self):
        asset = make_asset({(0, 0, 0): 0, (1, 0, 0): 0, (4, 0, 0): 1})
        report = edit.edit_asset(
            asset,
            "flood",
            lambda transaction: transaction.flood_fill((0, 0, 0), (2, 0, 0), 2, minimum=(0, 0, 0)),
        )
        self.assertEqual(report.painted, 2)
        components = edit.connected_components(asset)
        self.assertEqual([len(component) for component in components], [2, 1])
        original = dict(asset.voxels)
        with self.assertRaises(RuntimeError):
            with edit.VoxelEditTransaction(asset) as transaction:
                transaction.add((9, 9, 9), 0)
                raise RuntimeError("rollback")
        self.assertEqual(asset.voxels, original)
        with self.assertRaises(ValueError):
            edit.edit_asset(
                asset,
                "bad",
                lambda transaction: transaction.flood_fill(
                    (0, 0, 0),
                    (0, 0, 0),
                    0,
                    minimum=(1, 0, 0),
                ),
            )
        moved = make_asset({(0, 0, 0): 0, (1, 0, 0): 0})
        with edit.VoxelEditTransaction(moved, operation="move") as transaction:
            transaction.remove((0, 0, 0))
            transaction.add((2, 0, 0), 0)
            move_report = transaction.commit()
        self.assertTrue(move_report.changed)


class PaletteTest(unittest.TestCase):
    def test_text_json_and_source_palettes(self):
        self.assertEqual(palette.coerce_color("#f00"), (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(palette.coerce_color((255, 0, 0, 128))[3], 128 / 255.0)
        self.assertEqual(len(palette.normalize_palette(["#fff", "#fff"])), 1)
        self.assertEqual(palette.parse_hex_palette("#ff0000\n1 2 3"), [(1.0, 0.0, 0.0, 1.0), (1.0, 2 / 255, 3 / 255, 1.0)])
        self.assertEqual(len(palette.parse_gpl("GIMP PALETTE\nName: x\n255 0 0 Red")), 1)
        self.assertEqual(len(palette.parse_jasc_pal("JASC-PAL\n0100\n1\n0 0 255")), 1)
        self.assertEqual(len(palette.parse_json_palette('{"colors": ["#abcdef"]}')), 1)
        self.assertEqual(palette.nearest_palette_index(PALETTE[0], PALETTE), 0)
        remapped = palette.remap_voxels_to_palette({(0, 0, 0): 0}, PALETTE, [PALETTE[1]])
        self.assertEqual(remapped[(0, 0, 0)], 0)
        self.assertEqual(palette.source_by_id("lospec_dawnbringer_16").name.startswith("Dawn"), True)
        with self.assertRaises(KeyError):
            palette.source_by_id("missing")
        self.assertEqual(len(palette.scene_palette(SimpleNamespace())), len(palette.DEFAULT_PALETTE))
    def test_palette_gradient_reorder_and_compact_preserve_contracts(self):
        gradient = palette.palette_gradient("#000", "#fff", 3)
        self.assertEqual(gradient[0], (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(gradient[-1], (1.0, 1.0, 1.0, 1.0))
        reordered, index_map = palette.reorder_palette(["#f00", "#0f0", "#00f"], [2, 0, 1])
        self.assertEqual(reordered[0], (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(index_map, {2: 0, 0: 1, 1: 2})
        compacted, compact_map = palette.compact_palette(
            ["#f00", "#f00", "#0f0", "#00f"],
            [0, 1, 3],
        )
        self.assertEqual(compacted, [(1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)])
        self.assertEqual(compact_map, {0: 0, 1: 0, 3: 1})
        with self.assertRaises(ValueError):
            palette.reorder_palette(["#f00", "#0f0"], [0, 0])
        with self.assertRaises(ValueError):
            palette.palette_gradient("#000", "#fff", 0)
        self.assertEqual(
            palette.palette_gradient("#000", "#fff", 1),
            [(0.0, 0.0, 0.0, 1.0)],
        )
        with self.assertRaises(ValueError):
            palette.reorder_palette([], [])
        with self.assertRaises(ValueError):
            palette.compact_palette([], [])
        fallback, fallback_map = palette.compact_palette(PALETTE, [99])
        self.assertEqual(fallback, [PALETTE[0]])
        self.assertEqual(fallback_map, {0: 0})

    def test_ase_and_palette_dispatch(self):
        name = struct.pack(">H", 1) + "R".encode("utf-16-be")
        body = name + b"RGB " + struct.pack(">fff", 1.0, 0.0, 0.0) + struct.pack(">H", 0)
        ase = b"ASEF" + struct.pack(">HHI", 1, 0, 1) + struct.pack(">HI", 1, len(body)) + body
        self.assertEqual(len(palette.parse_ase(ase)), 1)
        self.assertEqual(len(palette.parse_palette_bytes(b"#123456", ".txt")), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.hex"
            path.write_text("#abcdef\n", encoding="utf-8")
            self.assertEqual(len(palette.load_palette_file(path)), 1)
        with self.assertRaises(ValueError):
            palette.parse_palette_bytes(b"bad", ".ase")

    def test_palette_validation_and_scene_collection(self):
        self.assertEqual(palette.coerce_color("#abcd"), (0.6666666666666666, 0.7333333333333333, 0.8, 0.8666666666666667))
        with self.assertRaises(ValueError):
            palette.coerce_color("not-a-color")
        with self.assertRaises(ValueError):
            palette.normalize_palette([])
        with self.assertRaises(ValueError):
            palette.parse_gpl("not GPL")
        with self.assertRaises(ValueError):
            palette.parse_jasc_pal("not PAL")
        self.assertEqual(
            palette.parse_json_palette('{"palette": [[1, 0, 0, 1]]}'),
            [(1.0, 0.0, 0.0, 1.0)],
        )
        collection_scene = SimpleNamespace(
            voxtart_palette_colors=[SimpleNamespace(color=(0.2, 0.3, 0.4, 1.0))]
        )
        self.assertEqual(palette.scene_palette(collection_scene), [(0.2, 0.3, 0.4, 1.0)])
        with self.assertRaises(ValueError):
            palette.nearest_palette_index((0, 0, 0, 1), [])
        with self.assertRaises(ValueError):
            palette.remap_voxels_to_palette({}, [], PALETTE)
        def ase_block(model, values):
            label = struct.pack(">H", 1) + "X".encode("utf-16-be")
            payload = label + model + struct.pack(">" + "f" * len(values), *values) + struct.pack(">H", 0)
            return struct.pack(">HI", 1, len(payload)) + payload
        ase = b"ASEF" + struct.pack(">HHI", 1, 0, 3) + b"".join(
            (ase_block(b"GRAY", (0.5,)), ase_block(b"CMYK", (0.1, 0.2, 0.3, 0.4)), ase_block(b"LAB ", (0.1, 0.2, 0.3)))
        )
        self.assertEqual(len(palette.parse_ase(ase)), 2)


class LayerAndIOTest(unittest.TestCase):
    def test_layer_stack_blends_and_roundtrip(self):
        palette_values = [(1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 0.5)]
        base = layers.Layer(name="Base", voxels={(0, 0, 0): 0})
        base.surface_backend = "MARCHING"
        stack = layers.LayerStack(layers=[base])
        for mode in layers.BLEND_MODES:
            stack.add(mode)
            stack.active.blend_mode = mode
            stack.active.voxels = {(0, 0, 0): 1}
            self.assertIn((0, 0, 0), stack.composite(palette_values))
            stack.remove_active()
        stack.add("Top")
        stack.active.voxels = {(1, 0, 0): 1}
        stack.active_index = 1
        duplicate = stack.duplicate_active()
        self.assertEqual(duplicate.name, "Top Copy")
        stack.move_active(-1)
        stack.remap_palette(palette_values, list(reversed(palette_values)))
        stack.remap_palette_indices({0: 1, 1: 0})
        self.assertEqual(stack.active.voxels[(1, 0, 0)], 1)
        encoded = stack.encode()
        restored = layers.LayerStack.decode(encoded)
        self.assertEqual(restored.layers[0].surface_backend, "MARCHING")
        self.assertEqual(restored.to_dict(), stack.to_dict())
        restored.flatten(palette_values)
        with self.assertRaises(ValueError):
            restored.set_active(1.5)
        with self.assertRaises(ValueError):
            restored.move_active(1.5)
        with self.assertRaises(ValueError):
            layers.Layer(opacity=object())
        with self.assertRaises(ValueError):
            restored.composite([(object(), 0.0, 0.0, 1.0)])
        with self.assertRaises(ValueError):
            restored.remap_palette_indices({0.5: 1})
        self.assertEqual(len(restored.layers), 1)

    def test_asset_io_with_and_without_layers(self):
        asset = make_asset({(0, 0, 0): 0})
        stack = layers.LayerStack.from_asset(asset)
        document = asset_io.encode_asset(asset, stack)
        loaded, loaded_stack = asset_io.decode_asset_with_layers(document)
        self.assertEqual(loaded.voxels, asset.voxels)
        self.assertIsNotNone(loaded_stack)
        self.assertIsNone(asset_io.decode_asset_with_layers(asset_io.encode_asset(asset))[1])
        with tempfile.TemporaryDirectory() as directory:
            path = asset_io.save_asset(asset, Path(directory) / "asset.json", stack)
            self.assertEqual(asset_io.load_asset(path).voxels, asset.voxels)
            self.assertEqual(asset_io.load_asset_with_layers(path)[1].to_dict(), stack.to_dict())
        with self.assertRaises(ValueError):
            asset_io.decode_asset('{"format": "bad", "format_version": 1}')


class GeometryAndOptimizationTest(unittest.TestCase):
    def test_meshing_and_mesh_optimization(self):
        asset = make_asset(solid_cells(2))
        greedy = meshing.greedy_mesh(asset)
        marching = meshing.marching_voxel_mesh(asset, iso=0.5)
        self.assertGreater(len(greedy.faces), 0)
        self.assertGreater(len(marching.faces), 0)
        self.assertEqual(len(meshing.scaled_mesh(asset, backend="greedy").vertices), len(greedy.vertices))
        self.assertEqual(len(meshing.scaled_mesh(asset, backend="marching").faces), len(marching.faces))
        with self.assertRaises(ValueError):
            meshing.marching_voxel_mesh(asset, iso=0.0)
        with self.assertRaises(ValueError):
            meshing.scaled_mesh(asset, backend="unknown")
        mesh = meshing.MeshData(
            vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0)],
            faces=[(0, 1, 2), (0, 1, 2), (0, 0, 0)],
            materials=[2, 2, 1],
        )
        optimized, report = mesh_optimize.optimize_mesh_data(mesh, triangulate=True)
        self.assertLessEqual(len(optimized.faces), len(mesh.faces))
        self.assertGreaterEqual(report.removed_duplicate_faces, 1)
        self.assertEqual(report.to_dict()["material_remap"], [2])

    def test_decimation_thresholds_and_protected_material(self):
        asset = make_asset(solid_cells(2), sockets=[voxel.VoxelSocket((0, 0, 0), (1, 0, 0))])
        for threshold in ("majority", "any", "full"):
            output, report = optimize.decimate(asset, optimize.DecimationPolicy(2, threshold=threshold))
            self.assertEqual(output.grid_size, 2.0)
            self.assertEqual(report.threshold, threshold)
        empty, empty_report = optimize.decimate(make_asset({}), optimize.DecimationPolicy(2))
        self.assertEqual(len(empty.voxels), 0)
        self.assertEqual(empty_report.coarse_blocks, 0)
        protected_policy = optimize.DecimationPolicy(2, threshold="full", protected=frozenset({(0, 0, 0)}), protected_palette_index=2)
        output, report = optimize.decimate(asset, protected_policy)
        self.assertGreaterEqual(report.protected_forced, 0)
        self.assertIn((0, 0, 0), output.voxels)
        with self.assertRaises(ValueError):
            optimize.DecimationPolicy(1)


class BlobAndPlacementTest(unittest.TestCase):
    def test_lasso_and_placement_math(self):
        lasso = blob.rounded_lasso_voxels([(0, 0), (2, 0), (2, 2), (0, 2)], depth_voxels=2, symmetry_axis=0)
        self.assertTrue(lasso)
        with self.assertRaises(ValueError):
            blob.rounded_lasso_voxels([(0, 0), (1, 1)], depth_voxels=1)
        self.assertEqual(asset_placer.snap_to_grid((1.2, 2.6, 0), 1), (1, 3, 0))
        self.assertEqual(asset_placer.nearest_point([(2, 0, 0), (1, 0, 0)], (0, 0, 0)), (1.0, 0.0, 0.0))
        self.assertEqual(len(asset_placer.polyline_samples([(0, 0, 0), (2, 0, 0)], spacing=1)), 3)
        self.assertEqual(len(asset_placer.disc_samples((0, 0, 0), 2, 4, seed=1)), 4)
        settings = asset_placer.PlacementSettings(
            seed=4,
            random_position=(1, 1, 1),
            random_rotation=(2, 2, 2),
            random_scale=(0.1, 0.1, 0.1),
        )
        transform = asset_placer.randomized_transform(
            (0, 0, 0), (0, 0, 0), (1, 1, 1), index=0, settings=settings
        )
        self.assertEqual(
            transform,
            asset_placer.randomized_transform(
                (0, 0, 0), (0, 0, 0), (1, 1, 1), index=0, settings=settings
            ),
        )
        pixels = asset_placer.thumbnail_pixels(make_asset(solid_cells(2)), 8, 8)
        self.assertEqual(len(pixels), 8 * 8 * 4)

    def test_kitbash_matching(self):
        source_socket = voxel.VoxelSocket((0, 0, 0), (1, 0, 0), "hand")
        target_socket = voxel.VoxelSocket((2, 0, 0), (-1, 0, 0), "hand")
        source = make_asset(sockets=[source_socket])
        target = make_asset(sockets=[target_socket])
        match = kitbash.find_best_match(source, target)
        self.assertIsNotNone(match)
        self.assertEqual(kitbash.grid_translation(source, target, match), (2.0, 0.0, 0.0))
        self.assertIsNone(kitbash.find_best_match(make_asset(), target))
        with self.assertRaises(ValueError):
            kitbash.opposite((2, 0, 0))

class OptimizerTest(unittest.TestCase):
    def test_voxel_to_game_mesh_backends_and_metrics(self):
        asset = make_asset(solid_cells(2), palette_values=PALETTE[:2])
        greedy_mesh, greedy_report = optimizer.optimize_asset(asset)
        self.assertEqual(greedy_report.backend, "greedy")
        self.assertEqual(greedy_report.source_voxels, 8)
        self.assertEqual(greedy_report.source_faces, 6)
        self.assertEqual(greedy_report.target_faces, 6)
        self.assertEqual(len(greedy_mesh.materials), 6)
        self.assertEqual(greedy_report.to_dict()["material_remap"], [0])
        marching_mesh, marching_report = optimizer.optimize_asset(
            asset,
            optimizer.OptimizationSettings(
                backend="MARCHING",
                triangulate=True,
                compact_materials=False,
            ),
        )
        self.assertEqual(marching_report.backend, "marching")
        self.assertGreater(len(marching_mesh.faces), 0)
        self.assertGreaterEqual(marching_report.triangulated_faces, 0)
        self.assertEqual(
            marching_report.face_reduction_ratio,
            round(1.0 - marching_report.target_faces / marching_report.source_faces, 6),
        )

    def test_optimizer_validation_and_empty_asset_metrics(self):
        with self.assertRaises(ValueError):
            optimizer.OptimizationSettings(backend="simple")
        for field_name in (
            "weld_vertices",
            "remove_degenerate",
            "remove_duplicate",
            "triangulate",
            "compact_materials",
        ):
            with self.assertRaises(TypeError):
                optimizer.OptimizationSettings(**{field_name: 1})
        with self.assertRaises(TypeError):
            optimizer.optimize_asset("not an asset")
        with self.assertRaises(TypeError):
            optimizer.optimize_asset(make_asset(), settings="not settings")
        empty = voxel.VoxelAsset(palette=list(PALETTE))
        mesh, report = optimizer.optimize_asset(empty)
        self.assertEqual(mesh.faces, [])
        self.assertEqual(report.vertex_reduction_ratio, 0.0)
        self.assertEqual(report.face_reduction_ratio, 0.0)


class BlobGalleryAndAssemblyTest(unittest.TestCase):
    def test_gallery_file_lifecycle_and_filters(self):
        socket = voxel.VoxelSocket((0, 0, 0), (0, 1, 0), "joint")
        asset = make_asset(sockets=[socket])
        entry = blob_gallery.make_blob_entry(asset, "Hero", blob_id="hero", category="Characters", tags=("Player", "player"))
        document = blob_gallery.encode_blob(entry)
        restored = blob_gallery.decode_blob(document)
        self.assertEqual(restored.tags, ("player",))
        with tempfile.TemporaryDirectory() as directory:
            gallery = blob_gallery.BlobGallery(Path(directory))
            gallery.save(entry)
            gallery.set_favorite("hero")
            entries, errors = gallery.scan(query="hero", favorites_only=True)
            self.assertEqual(len(entries), 1)
            self.assertFalse(errors)
            self.assertEqual(gallery.categories(), ("Characters",))
            gallery.remove("hero")
            self.assertEqual(gallery.entries(), ())
            (Path(directory) / "bad.voxtart.blob.json").write_text("{}", encoding="utf-8")
            self.assertEqual(len(gallery.scan()[1]), 1)
        with self.assertRaises(ValueError):
            blob_gallery.decode_blob("{}")

    def test_scene_assembly_roundtrip(self):
        placement = scene_assembly.ScenePlacement("p1", "blob", "Hero", scale=(1, 2, 1))
        assembly = scene_assembly.SceneAssembly(settings={"mode": "GRID"})
        assembly.upsert(placement)
        assembly.upsert(scene_assembly.ScenePlacement("p1", "blob", "Updated"))
        self.assertEqual(len(assembly.placements), 1)
        self.assertTrue(assembly.remove("p1"))
        self.assertFalse(assembly.remove("missing"))
        assembly.upsert(placement)
        restored = scene_assembly.SceneAssembly.decode(assembly.encode())
        self.assertEqual(restored.to_dict(), assembly.to_dict())
        with self.assertRaises(ValueError):
            scene_assembly.ScenePlacement("bad/id", "blob", "Name")
        with self.assertRaises(ValueError):
            scene_assembly.ScenePlacement("p2", "blob", "Name", scale=(0, 1, 1))



class CharacterEditorTest(unittest.TestCase):
    def test_recipe_slots_order_and_roundtrip(self):
        recipe = character_editor.CharacterRecipe(
            name="Hero",
            parts=[
                character_editor.CharacterPart("HEAD", "head-id", name="Head"),
                character_editor.CharacterPart("ROOT", "root-id", name="Root"),
                character_editor.CharacterPart("TORSO", "torso-id", name="Torso"),
            ],
            settings={"root_location": [1, 2, 3]},
        )
        self.assertEqual(
            [part.slot for part in recipe.ordered_parts()],
            ["ROOT", "TORSO", "HEAD"],
        )
        recipe.upsert(character_editor.CharacterPart("HEAD", "head-2", name="Helmet"))
        self.assertEqual(recipe.get("HEAD").blob_id, "head-2")
        self.assertTrue(recipe.remove("HEAD"))
        self.assertFalse(recipe.remove("HEAD"))
        recipe.upsert(character_editor.CharacterPart("HEAD", "head-2", visible=False))
        restored = character_editor.CharacterRecipe.decode(recipe.encode())
        self.assertEqual(restored.to_dict(), recipe.to_dict())
        self.assertEqual(character_editor.default_parent_slot("ARM_L"), "TORSO")
        with self.assertRaises(ValueError):
            recipe.upsert(character_editor.CharacterPart("ROOT", "cyclic", parent_slot="TORSO"))
        self.assertEqual(recipe.get("ROOT").blob_id, "root-id")

    def test_recipe_validation_boundaries(self):
        with self.assertRaises(ValueError):
            character_editor.default_parent_slot("UNKNOWN")
        with self.assertRaises(ValueError):
            character_editor.CharacterPart("UNKNOWN", "blob")
        with self.assertRaises(ValueError):
            character_editor.CharacterPart("ROOT", "")
        with self.assertRaises(ValueError):
            character_editor.CharacterPart("ROOT", "blob", parent_slot="ROOT")
        with self.assertRaises(ValueError):
            character_editor.CharacterPart("ROOT", "blob", parent_slot="UNKNOWN")
        with self.assertRaises(ValueError):
            character_editor.CharacterPart("ROOT", "blob", name="x" * 257)
        with self.assertRaises(ValueError):
            character_editor.CharacterPart("ROOT", "blob", source_tag="x" * 97)
        with self.assertRaises(ValueError):
            character_editor.CharacterPart("ROOT", "blob", target_tag="x" * 97)
        with self.assertRaises(ValueError):
            character_editor.CharacterPart.from_dict([])
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe.from_dict([])
        with self.assertRaises(TypeError):
            character_editor.CharacterRecipe().upsert("not a part")
        with self.assertRaises(TypeError):
            character_editor.CharacterRecipe(settings=[])
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe(
                parts=[
                    character_editor.CharacterPart("ROOT", "a"),
                    character_editor.CharacterPart("ROOT", "b"),
                ],
            )
        with self.assertRaises(TypeError):
            character_editor.CharacterRecipe(parts=["not a part"])
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe(
                parts=[
                    character_editor.CharacterPart("ROOT", "a", parent_slot="TORSO"),
                    character_editor.CharacterPart("TORSO", "b", parent_slot="ROOT"),
                ],
            )
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe(
                parts=[
                    character_editor.CharacterPart("ROOT", "a", parent_slot="TORSO"),
                    character_editor.CharacterPart("TORSO", "b", parent_slot="HEAD"),
                    character_editor.CharacterPart("HEAD", "c", parent_slot="ROOT"),
                ]
            )
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe.decode("{}")
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe.decode("{")
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe.from_dict(
                {"format": character_editor.CHARACTER_FORMAT, "format_version": 99, "parts": []}
            )
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe.from_dict(
                {"format": character_editor.CHARACTER_FORMAT, "format_version": 1.5, "parts": []}
            )
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe.from_dict(
                {"format": character_editor.CHARACTER_FORMAT, "format_version": 1, "parts": {}}
            )
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe(
                settings={"bad": object()},
            )
        with self.assertRaises(ValueError):
            character_editor.CharacterRecipe(
                parts=[
                    character_editor.CharacterPart("ROOT", "a"),
                    character_editor.CharacterPart("TORSO", "b"),
                ],
                schema_version=99,
            )
        original_limit = character_editor.MAX_CHARACTER_PARTS
        try:
            character_editor.MAX_CHARACTER_PARTS = 1
            with self.assertRaises(ValueError):
                character_editor.CharacterRecipe(
                    parts=[
                        character_editor.CharacterPart("ROOT", "a"),
                        character_editor.CharacterPart("TORSO", "b"),
                    ]
                )
            character_editor.MAX_CHARACTER_PARTS = 0
            with self.assertRaises(ValueError):
                character_editor.CharacterRecipe().upsert(
                    character_editor.CharacterPart("ROOT", "replacement")
                )
        finally:
            character_editor.MAX_CHARACTER_PARTS = original_limit
        cycle = character_editor.CharacterRecipe(
            parts=[
                character_editor.CharacterPart("ROOT", "root"),
                character_editor.CharacterPart("TORSO", "torso"),
            ]
        )
        cycle.parts[0] = character_editor.CharacterPart(
            "ROOT", "root-cycle", parent_slot="TORSO"
        )
        with self.assertRaises(ValueError):
            cycle.ordered_parts()
        rollback = character_editor.CharacterRecipe(
            parts=[
                character_editor.CharacterPart("HEAD", "head", parent_slot="ACCESSORY"),
            ]
        )
        with self.assertRaises(ValueError):
            rollback.upsert(
                character_editor.CharacterPart("ACCESSORY", "accessory", parent_slot="HEAD")
            )
        self.assertIsNone(rollback.get("ACCESSORY"))

class SceneHierarchyTest(unittest.TestCase):
    def test_collection_parent_tree_and_placed_filter(self):
        records = (
            scene_hierarchy.SceneHierarchyRecord(
                "zulu",
                display_name="Zulu",
                collection_path=("Props",),
                placement_id="z",
                category="Props",
            ),
            scene_hierarchy.SceneHierarchyRecord(
                "child",
                collection_path=("Props",),
                parent_name="zulu",
                placement_id="child",
            ),
            scene_hierarchy.SceneHierarchyRecord(
                "unplaced",
                collection_path=("Props",),
            ),
            scene_hierarchy.SceneHierarchyRecord(
                "hero",
                collection_path=("Characters",),
                placement_id="hero",
                selected=True,
            ),
        )
        root = scene_hierarchy.build_scene_hierarchy(records, include_unplaced=False)
        self.assertEqual([node.name for node in root.children], ["Characters", "Props"])
        props = root.children[1]
        self.assertEqual([node.object_name for node in props.children], ["zulu"])
        self.assertEqual(props.children[0].children[0].object_name, "child")
        rows = scene_hierarchy.flatten_hierarchy(root)
        self.assertEqual([node.object_name for node, _depth in rows if node.is_asset], ["hero", "zulu", "child"])
        self.assertTrue(props.children[0].selected is False)

        with_unplaced = scene_hierarchy.build_scene_hierarchy(records)
        self.assertEqual(
            {
                node.object_name
                for node, _depth in scene_hierarchy.flatten_hierarchy(with_unplaced)
                if node.is_asset
            },
            {"hero", "zulu", "child", "unplaced"},
        )

    def test_parent_cycles_and_validation_are_safe(self):
        cycle = scene_hierarchy.build_scene_hierarchy(
            (
                scene_hierarchy.SceneHierarchyRecord("a", parent_name="b", placement_id="a"),
                scene_hierarchy.SceneHierarchyRecord("b", parent_name="a", placement_id="b"),
            )
        )
        self.assertEqual(
            {
                node.object_name
                for node, _depth in scene_hierarchy.flatten_hierarchy(cycle)
                if node.is_asset
            },
            {"a", "b"},
        )
        with self.assertRaises(ValueError):
            scene_hierarchy.SceneHierarchyNode("bad", "LIGHT")
        with self.assertRaises(ValueError):
            scene_hierarchy.SceneHierarchyRecord("")

        node = scene_hierarchy.SceneHierarchyNode(
            "Visible",
            "ASSET",
            object_name="visible",
            category="Props",
            placement_id="p",
            visible=False,
            selected=True,
        )
        self.assertEqual(
            node.to_dict(),
            {
                "name": "Visible",
                "kind": "ASSET",
                "object_name": "visible",
                "category": "Props",
                "placement_id": "p",
                "visible": False,
                "selected": True,
                "children": [],
            },
        )
        with self.assertRaises(ValueError):
            scene_hierarchy.SceneHierarchyRecord("x" * 257)
        with self.assertRaises(ValueError):
            scene_hierarchy.SceneHierarchyRecord("deep", collection_path=("x",) * 65)
        with self.assertRaises(ValueError):
            scene_hierarchy.build_scene_hierarchy(
                (
                    scene_hierarchy.SceneHierarchyRecord("duplicate"),
                    scene_hierarchy.SceneHierarchyRecord("duplicate"),
                )
            )

class ShadingTest(unittest.TestCase):
    def test_shading_and_dither_patterns(self):
        asset = make_asset(solid_cells(2))
        self.assertEqual(len(shading.surface_cells(asset)), 8)
        self.assertTrue(
            all(
                abs(component + 0.5773502691896258) < 1.0e-12
                for component in shading.surface_normal(asset, (-1, -1, -1))
            )
        )
        report = shading.shade_voxels(asset, ambient=0.2, strength=1.0, levels=4, group_size=2)
        self.assertEqual(report.operation, "shade")
        for pattern in shading.DITHER_PATTERNS:
            value = shading.dither_value((1, 2, 3), pattern=pattern, axis="XY", bayer_size=4, noise_amount=0.2)
            self.assertGreaterEqual(value, 0.0)
            self.assertLess(value, 1.0)
        dither_report = shading.dither_voxels(
            asset, 0, 1, density=0.7, pattern="CIRCUIT", axis="YZ", group_size=1, source_indices=(0, 1)
        )
        self.assertEqual(dither_report.operation, "dither")
        with self.assertRaises(ValueError):
            shading.dither_value((0, 0, 0), pattern="bad")
        with self.assertRaises(ValueError):
            shading.shade_voxels(asset, levels=1)

    def test_shading_selection_protection_and_validation(self):
        asset = make_asset(solid_cells(2))
        self.assertEqual(
            shading.shade_voxels(
                asset,
                cells=[(-1, -1, -1), (99, 99, 99)],
                surface_only=False,
                protected=[(-1, -1, -1)],
            ).skipped_protected,
            1,
        )
        for pattern in ("BAYER2", "BAYER4", "ORDERED", "CIRCUIT", "NOISE", "CHECKER"):
            for axis in shading.DITHER_AXES:
                self.assertGreaterEqual(
                    shading.dither_value((1, 2, 3), pattern=pattern, axis=axis, bayer_size=2, noise_amount=0.0),
                    0.0,
                )
        with self.assertRaises(ValueError):
            shading.dither_value((0, 0, 0), pattern="ORDERED", bayer_size=3)
        with self.assertRaises(ValueError):
            shading.dither_value((0, 0, 0), axis="bad")
        with self.assertRaises(ValueError):
            shading.dither_voxels(asset, 0, 1, density=2)
        with self.assertRaises(ValueError):
            shading.dither_voxels(asset, 0, 1, axis="bad")
        with self.assertRaises(ValueError):
            shading.dither_voxels(asset, 0, 99)
        with self.assertRaises(ValueError):
            shading.shade_voxels(asset, ambient=2)
        with self.assertRaises(ValueError):
            shading.shade_voxels(asset, strength=2)
        with self.assertRaises(ValueError):
            shading.shade_voxels(asset, group_size=0)


class FormatTest(unittest.TestCase):
    def test_vox_qb_roundtrips(self):
        asset = make_asset({(0, 0, 0): 0, (1, 0, 0): 1})
        for encoded, decoder in (
            (voxel_formats.encode_magica_vox(asset), voxel_formats.decode_magica_vox),
            (voxel_formats.encode_qb(asset, compressed=True), voxel_formats.decode_qb),
            (voxel_formats.encode_qb(asset, compressed=False, color_format=1), voxel_formats.decode_qb),
        ):
            restored = decoder(encoded)
            self.assertEqual(len(restored.voxels), len(asset.voxels))
        models = voxel_formats.decode_magica_vox_models(voxel_formats.encode_magica_vox_models((asset, asset)))
        self.assertEqual(len(models), 2)
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox(b"bad")
        with self.assertRaises(ValueError):
            voxel_formats.encode_qb(asset, color_format=3)


class BlockbenchAndMinecraftTest(unittest.TestCase):
    def test_blockbench_variants(self):
        payload = {"elements": [{"from": [0, 0, 0], "to": [2, 1, 1], "color": [255, 0, 0, 255]}]}
        imported = blockbench_formats.decode_blockbench(payload)
        self.assertEqual(len(imported.voxels), 2)
        encoded = blockbench_formats.encode_blockbench(imported)
        self.assertEqual(len(blockbench_formats.decode_blockbench(encoded).voxels), 2)
        bedrock = {"minecraft:geometry": [{"bones": [{"name": "root", "cubes": [{"origin": [0, 0, 0], "size": [2, 2, 2]}]}]}]}
        self.assertEqual(len(blockbench_formats.decode_blockbench(bedrock).voxels), 8)
        mesh = {"elements": [{"type": "mesh", "vertices": {"a": [0, 0, 0], "b": [2, 1, 1]}}]}
        self.assertGreater(len(blockbench_formats.decode_blockbench(mesh).voxels), 0)
        with self.assertRaises(ValueError):
            blockbench_formats.decode_blockbench({})

    def test_nbt_and_minecraft_formats(self):
        root = minecraft_nbt.nbt_compound("root", {
            "byte": minecraft_nbt.nbt_byte("byte", -1),
            "list": minecraft_nbt.nbt_list("list", minecraft_nbt.TAG_INT, (1, 2)),
            "longs": minecraft_nbt.nbt_long_array("longs", (-1, 2)),
        })
        for compression in ("raw", "gzip", "zlib"):
            restored = minecraft_nbt.decode_nbt(minecraft_nbt.encode_nbt(root, compression=compression))
            self.assertEqual(minecraft_nbt.compound_fields(restored)["list"].value.values, (1, 2))
        volume = minecraft_formats.MinecraftVolume(
            2, 2, 2, {(0, 0, 0): "minecraft:stone", (1, 1, 1): "minecraft:gold_block"}, origin=(-2, 0, 4)
        )
        for encoder, decoder in (
            (minecraft_formats.encode_sponge_schematic, minecraft_formats.decode_sponge_schematic),
            (minecraft_formats.encode_legacy_schematic, minecraft_formats.decode_legacy_schematic),
            (minecraft_formats.encode_structure, minecraft_formats.decode_structure),
        ):
            restored = decoder(minecraft_nbt.decode_nbt(encoder(volume)))
            self.assertEqual(len(restored.blocks), 2)
        region = minecraft_formats.encode_anvil_region(volume, region_x=-1, region_z=0)
        self.assertEqual(len(minecraft_formats.decode_anvil_region(region, region_x=-1, region_z=0).blocks), 2)
        asset = volume.to_voxel_asset()
        self.assertEqual(len(minecraft_formats.MinecraftVolume.from_voxel_asset(asset).blocks), 2)


class BloformerTest(unittest.TestCase):
    def test_all_modes_and_shapes(self):
        self.assertEqual(len(bloformer.volume_cells((0, 0, 0), 1, shape="SPHERE")), 7)
        self.assertEqual(len(bloformer.volume_cells((0, 0, 0), 1, shape="BOX")), 27)
        self.assertEqual(len(bloformer.volume_cells((0, 0, 0), 1, shape="CYLINDER", axis="X")), 15)
        for mode in bloformer.BLOFORMER_MODES:
            asset = make_asset(solid_cells(5))
            report = bloformer.apply_bloformer(
                asset,
                center=(0, 0, 0),
                radius=2,
                mode=mode,
                shape="SPHERE",
                thickness=1,
            )
            self.assertTrue(report.operation.startswith("bloformer_"))
        protected = make_asset({(0, 0, 0): 0})
        report = bloformer.apply_bloformer(protected, radius=1, mode="SUBTRACT", protected={(0, 0, 0)})
        self.assertEqual(report.skipped_protected, 1)
        with self.assertRaises(ValueError):
            bloformer.apply_bloformer(make_asset(), mode="bad")


class StackingTest(unittest.TestCase):
    def _layer(self, name="Layer", offset=(0, 0), visible=True):
        return stacking.StackLayer(name, 2, 2, ((1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1), (0, 0, 0, 0)), offset, visible, 1.0)

    def test_stack_documents_manifests_and_ora(self):
        document = stacking.StackDocument.from_layers([self._layer()], source_format="test")
        restored = stacking.StackDocument.decode(document.encode())
        self.assertEqual(restored.to_dict(), document.to_dict())
        inline = {"format": "sprite", "width": 2, "height": 2, "layers": [self._layer().to_dict()]}
        loaded_manifest = stacking.decode_stack_manifest(json.dumps(inline), lambda _name, _data: self._layer())
        self.assertEqual(len(loaded_manifest.layers), 1)
        manifest_files = {"layers": [{"file": "one.png", "name": "File"}]}
        loaded_file_manifest = stacking.decode_stack_manifest(json.dumps(manifest_files), lambda _name, _data: self._layer())
        self.assertEqual(loaded_file_manifest.layers[0].name, "File")
        with self.assertRaises(ValueError):
            stacking.decode_stack_manifest("{}", lambda _name, _data: self._layer())
        ora_buffer = io.BytesIO()
        with ZipFile(ora_buffer, "w", ZIP_DEFLATED) as archive:
            archive.writestr("stack.xml", '<image w="2" h="2"><layer name="A" src="data/a.png"/></image>')
            archive.writestr("data/a.png", b"pixels")
        ora = stacking.decode_openraster(ora_buffer.getvalue(), lambda _name, _data: self._layer())
        self.assertEqual(ora.source_format, "openraster")
        for axis in stacking.STACK_AXIS_ITEMS:
            asset = stacking.stack_to_voxel_asset(document, axis=axis, layer_order="TOP_TO_BOTTOM")
            self.assertEqual(len(asset.voxels), 3)


class RigAndMCPTest(unittest.TestCase):
    def test_rig_planning(self):
        records = [
            {"name": "root", "parent": None, "use_deform": True},
            {"name": "hand.L", "parent": "root", "use_deform": True, "selected": True},
            {"name": "ctrl", "parent": "root", "use_deform": False, "selected": True},
        ]
        self.assertEqual(game_rig.parse_name_list("root; hand.L"), ("root", "hand.L"))
        plan = game_rig.make_extraction_plan(records, mode="DEFORM_AND_SELECTED", hierarchy="PRESERVE")
        self.assertIn("hand.L", plan.names)
        self.assertIn("ctrl", plan.names)
        self.assertEqual(game_rig.choose_action_names(("Walk", "Idle"), requested="Walk"), ("Walk",))
        with self.assertRaises(ValueError):
            game_rig.make_extraction_plan(records, mode="bad")

    def test_rig_modes_and_selection_errors(self):
        records = [
            {"name": "root", "parent": None, "use_deform": False},
            {"name": "deform", "parent": "root", "use_deform": True},
            {"name": "selected", "parent": "deform", "select": True},
        ]
        self.assertEqual(game_rig.parse_name_list(["a", "a", "", "b"]), ("a", "b"))
        self.assertEqual(
            game_rig.make_extraction_plan(records, mode="DEFORM_ONLY").parent_by_name["deform"],
            "root",
        )
        self.assertIn("selected", game_rig.make_extraction_plan(records, mode="ALL_BONES").names)
        flattened = game_rig.make_extraction_plan(
            records,
            mode="DEFORM_ONLY",
            hierarchy="FLATTEN_MISSING",
            include_root=False,
            keep_bones=("selected",),
        )
        self.assertEqual(flattened.parent_by_name["selected"], "deform")
        self.assertEqual(game_rig.choose_action_names(("Walk", "Idle"), all_actions=True), ("Idle", "Walk"))
        self.assertEqual(game_rig.choose_action_names(("Walk", "Idle"), active="Idle"), ("Idle",))
        self.assertEqual(game_rig.choose_action_names(("Walk", "Idle")), ("Idle",))
        with self.assertRaises(ValueError):
            game_rig.choose_action_names(())
        with self.assertRaises(ValueError):
            game_rig.choose_action_names(("Walk",), requested="Missing")
        with self.assertRaises(ValueError):
            game_rig.make_extraction_plan(records, keep_bones=("Missing",))
        with self.assertRaises(ValueError):
            game_rig.make_extraction_plan(records, root_name="Missing")
        with self.assertRaises(ValueError):
            game_rig.make_extraction_plan(
                [{"name": "a", "parent": "b", "use_deform": True}, {"name": "b", "parent": "a"}],
                mode="ALL_BONES",
            )

    def test_mcp_dispatch(self):
        calls = []
        dispatcher = mcp_protocol.MCPDispatcher(lambda name, args: calls.append((name, args)) or {"ok": True}, lambda uri: {"uri": uri})
        self.assertEqual(dispatcher.dispatch({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"], {})
        self.assertIn("tools", dispatcher.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"])
        result = dispatcher.dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": mcp_protocol.TOOL_DEFINITIONS[0]["name"], "arguments": {}}})
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(len(calls), 1)
        self.assertIsNone(dispatcher.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(dispatcher.dispatch({"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "voxtart://test"}})["result"]["contents"][0]["uri"], "voxtart://test")
        self.assertEqual(dispatcher.dispatch({"jsonrpc": "2.0", "id": 5, "method": "prompts/get", "params": {"name": "voxtart_create", "arguments": {"request": "make"}}})["result"]["messages"][0]["content"]["text"], "make")
        invalid = dispatcher.dispatch({"jsonrpc": "1.0", "id": 6, "method": "ping"})
        self.assertEqual(invalid["error"]["code"], -32600)
        with self.assertRaises(mcp_protocol.MCPError):
            dispatcher._dispatch_method("missing", {})


class BoundaryAndFailureTest(unittest.TestCase):
    def test_placement_boundaries(self):
        self.assertIsNone(asset_placer.nearest_point((), (0, 0, 0)))
        self.assertEqual(asset_placer.polyline_samples([(1, 2, 3)], spacing=1), ((1.0, 2.0, 3.0),))
        self.assertEqual(
            len(asset_placer.polyline_samples([(0, 0, 0), (1, 0, 0), (0, 0, 0)], spacing=0.5, closed=True)),
            4,
        )
        self.assertEqual(asset_placer.disc_samples((0, 0, 0), 1, 0), ())
        with self.assertRaises(ValueError):
            asset_placer.snap_to_grid((0, 0, 0), 0)
        with self.assertRaises(ValueError):
            asset_placer.nearest_point([(0, 0)], (0, 0, 0))
        with self.assertRaises(ValueError):
            asset_placer.polyline_samples([(0, 0, 0), (1, 0, 0)], spacing=0)
        with self.assertRaises(ValueError):
            asset_placer.disc_samples((0, 0, 0), -1, 1)
        with self.assertRaises(ValueError):
            asset_placer.disc_samples((0, 0, 0), 1, 1.5)
        with self.assertRaises(ValueError):
            asset_placer.randomized_transform(
                (0, 0, 0),
                (0, 0, 0),
                (1, 1, 1),
                index=0,
                settings=asset_placer.PlacementSettings(random_position=(-1, 0, 0)),
            )
        with self.assertRaises(ValueError):
            asset_placer.randomized_transform(
                (0, 0, 0),
                (0, 0, 0),
                (0, 1, 1),
                index=0,
                settings=asset_placer.PlacementSettings(),
            )
        self.assertEqual(asset_placer.thumbnail_pixels(make_asset({}), 8, 8), (0.0,) * (8 * 8 * 4))
        with self.assertRaises(ValueError):
            asset_placer.thumbnail_pixels(make_asset(), 7, 8)
        with self.assertRaises(ValueError):
            asset_placer.thumbnail_pixels(make_asset(), 8.5, 8)

    def test_voxel_picking(self):
        asset = make_asset({(0, 0, 0): 0, (1, 0, 0): 1})
        hit = pick.raycast_voxel(asset, (-2.0, 0.5, 0.5), (1.0, 0.0, 0.0))
        self.assertEqual(hit.cell, (0, 0, 0))
        self.assertEqual(hit.normal, (-1, 0, 0))
        self.assertIsNone(pick.raycast_voxel(asset, (-2, 2, 2), (1, 0, 0)))
        self.assertIsNone(pick.raycast_voxel(asset, (-2, 0.5, 0.5), (1, 0, 0), max_distance=0.1))
        self.assertIsNone(pick.raycast_voxel(make_asset({}), (0, 0, 0), (1, 0, 0)))
        with self.assertRaises(ValueError):
            pick.raycast_voxel(asset, (0, 0, 0), (0, 0, 0))
        with self.assertRaises(ValueError):
            pick.raycast_voxel(asset, (0, 0, 0), (1, 0, 0), max_distance=-1)

    def test_lasso_validation_and_empty_selection(self):
        for polygon in (((0, 0), (1, 0)), ((0, 0), (1, 0), (2, 0))):
            with self.assertRaises(ValueError):
                blob.rounded_lasso_voxels(polygon)
        with self.assertRaises(ValueError):
            blob.rounded_lasso_voxels(((0, 0), (1, 0), (0, 1)), depth_voxels=True)
        with self.assertRaises(ValueError):
            blob.rounded_lasso_voxels(((0, 0), (1, 0), (0, 1)), symmetry_axis=3)
        with self.assertRaises(ValueError):
            blob.rounded_lasso_voxels(((0, 0), (1, 0), (0, 1)), palette_index=-1)
        with self.assertRaises(ValueError):
            blob.rounded_lasso_voxels(((0, 0), (1, 0), (0, 1)), depth_voxels=0)
        with self.assertRaises(ValueError):
            blob.rounded_lasso_voxels(((0.1, 0.1), (0.2, 0.1), (0.1, 0.2)), depth_voxels=1)

    def test_transaction_closed_and_boundary_operations(self):
        asset = make_asset({(0, 0, 0): 0})
        transaction = edit.VoxelEditTransaction(asset)
        transaction.paint((4, 4, 4), 1)
        transaction.remove((4, 4, 4))
        transaction.commit()
        self.assertFalse(transaction.report.to_dict()["changed"])
        with self.assertRaises(RuntimeError):
            transaction.add((1, 1, 1))
        with self.assertRaises(ValueError):
            with edit.VoxelEditTransaction(asset):
                transaction = edit.VoxelEditTransaction(asset)
                transaction.fill_box((1, 1, 1), (0, 0, 0))
        with self.assertRaises(ValueError):
            with edit.VoxelEditTransaction(asset) as active:
                active.flood_fill((3, 0, 0), (0, 0, 0), minimum=(0, 0, 0))
        with self.assertRaises(RuntimeError):
            edit.edit_asset(asset, "failing", lambda _transaction: (_ for _ in ()).throw(RuntimeError("fail")))

    def test_layer_and_asset_validation(self):
        with self.assertRaises(ValueError):
            layers.Layer(blend_mode="unknown")
        with self.assertRaises(ValueError):
            layers.Layer(surface_backend="unknown")
        with self.assertRaises(ValueError):
            layers.Layer(voxels={(0, 0, 0): -1})
        with self.assertRaises(ValueError):
            layers.Layer.from_dict({"voxels": [[0, 0, 0]]})
        with self.assertRaises(ValueError):
            layers.Layer(opacity=float("nan"))
        with self.assertRaises(ValueError):
            layers.LayerStack(active_index=1.5)
        stack = layers.LayerStack()
        with self.assertRaises(IndexError):
            stack.set_active(2)
        with self.assertRaises(ValueError):
            stack.remove_active()
        with self.assertRaises(ValueError):
            stack.merge_down(PALETTE)
        stack.active.visible = False
        self.assertEqual(stack.composite(PALETTE), {})
        stack.active.visible = True
        stack.active.opacity = 0.0
        self.assertEqual(stack.composite(PALETTE), {})
        with self.assertRaises(ValueError):
            stack.composite([])
        with self.assertRaises(ValueError):
            layers.LayerStack.from_dict({"schema_version": 99})
        with self.assertRaises(ValueError):
            voxel.VoxelAsset.box(0)
        with self.assertRaises(ValueError):
            voxel.VoxelAsset(palette=[])
        with self.assertRaises(ValueError):
            voxel.VoxelAsset.from_dict({"schema_version": 1, "grid_size": 1, "palette": PALETTE, "voxels": [[0, 0, 0]]})

    def test_gallery_metadata_and_filesystem_limits(self):
        socket = voxel.VoxelSocket((0, 0, 0), (0, 0, 1), "head")
        asset = make_asset(sockets=[socket])
        joints = blob_gallery.joints_from_asset(
            asset,
            json.dumps([{"socket_index": 0, "name": "Head", "role": "head", "bone": "Head"}]),
        )
        self.assertEqual(joints[0].bone, "Head")
        self.assertEqual(blob_gallery.BlobJoint.from_dict(joints[0].to_dict()).name, "Head")
        with self.assertRaises(ValueError):
            blob_gallery.BlobJoint("", socket)
        with self.assertRaises(TypeError):
            blob_gallery.BlobJoint("bad", object())
        with self.assertRaises(ValueError):
            blob_gallery.make_blob_entry(asset, "", blob_id="bad/id")
        entry = blob_gallery.make_blob_entry(asset, "Valid", blob_id="valid", tags="A, a, B")
        with self.assertRaises(ValueError):
            blob_gallery.decode_blob("not json")
        with self.assertRaises(ValueError):
            blob_gallery.decode_blob(json.dumps({"format": blob_gallery.BLOB_FORMAT, "format_version": 999}))
        with tempfile.TemporaryDirectory() as directory:
            gallery = blob_gallery.BlobGallery(Path(directory), max_file_bytes=1)
            self.assertEqual(gallery.scan(), ((), ()))
            gallery.ensure()
            (Path(directory) / blob_gallery.FAVORITES_FILENAME).write_text("{}", encoding="utf-8")
            self.assertFalse(gallery.is_favorite("valid"))
            with self.assertRaises(ValueError):
                gallery.path_for("../bad")
            with self.assertRaises(ValueError):
                gallery.set_favorite("../bad")
            with self.assertRaises(ValueError):
                blob_gallery.save_blob(entry, Path(directory))

    def test_geometry_and_export_boundaries(self):
        empty = make_asset({})
        self.assertEqual(meshing.greedy_mesh(empty).faces, [])
        self.assertEqual(meshing.marching_voxel_mesh(empty).faces, [])
        with self.assertRaises(ValueError):
            mesh_optimize.optimize_mesh_data(
                meshing.MeshData([(0, 0, 0)], [(0, 1, 2)], [0])
            )
        with self.assertRaises(ValueError):
            mesh_optimize.optimize_mesh_data(
                meshing.MeshData([(0, 0, 0)], [(1,)], [0])
            )
        with self.assertRaises(ValueError):
            voxel_formats.encode_magica_vox(empty)
        with self.assertRaises(ValueError):
            voxel_formats.encode_qb(empty)
        with self.assertRaises(ValueError):
            blockbench_formats.decode_blockbench(b"{")
        with self.assertRaises(ValueError):
            blockbench_formats.decode_blockbench({"elements": []})
        with self.assertRaises(ValueError):
            blockbench_formats.encode_blockbench(make_asset(), units_per_voxel=0)
        with self.assertRaises(ValueError):
            blockbench_formats.decode_blockbench(
                {"elements": [{"from": [0, 0, 0], "to": [1, 1, 1]}]}, grid_size=0
            )

    def test_nbt_palette_and_mcp_errors(self):
        all_tags = minecraft_nbt.nbt_compound(
            "root",
            {
                "short": minecraft_nbt.nbt_short("short", -2),
                "int": minecraft_nbt.nbt_int("int", 3),
                "long": minecraft_nbt.nbt_long("long", 4),
                "float": minecraft_nbt.nbt_float("float", 0.5),
                "double": minecraft_nbt.nbt_double("double", 0.25),
                "bytes": minecraft_nbt.nbt_byte_array("bytes", (-1, 2)),
                "text": minecraft_nbt.nbt_string("text", "hello"),
                "ints": minecraft_nbt.nbt_int_array("ints", (1, 2)),
            },
        )
        restored = minecraft_nbt.decode_nbt(minecraft_nbt.encode_nbt(all_tags), compression="auto")
        self.assertEqual(minecraft_nbt.field(restored, "text").value, "hello")
        with self.assertRaises(ValueError):
            minecraft_nbt.decode_nbt(b"bad", compression="bad")
        with self.assertRaises(ValueError):
            minecraft_nbt.decode_nbt(b"\x0a\x00\x00", compression="raw")
        with self.assertRaises(ValueError):
            palette.parse_json_palette("[]")
        with self.assertRaises(ValueError):
            palette.parse_palette_bytes(b"x" * (palette.MAX_PALETTE_BYTES + 1))
        dispatcher = mcp_protocol.MCPDispatcher(
            lambda _name, _arguments: (_ for _ in ()).throw(mcp_protocol.MCPToolError("tool failed"))
        )
        for request in (
            {"jsonrpc": "2.0", "id": 1},
            {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": []},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "missing"}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": mcp_protocol.TOOL_DEFINITIONS[0]["name"], "arguments": []}},
            {"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "x"}},
            {"jsonrpc": "2.0", "id": 6, "method": "prompts/get", "params": {"name": "missing"}},
        ):
            self.assertIn("error", dispatcher.dispatch(request))
        tool_error = dispatcher.dispatch(
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": mcp_protocol.TOOL_DEFINITIONS[0]["name"], "arguments": {}}}
        )
        self.assertTrue(tool_error["result"]["isError"])
        self.assertIn("protocolVersion", dispatcher.dispatch({"jsonrpc": "2.0", "id": 8, "method": "initialize"})["result"])
        malformed_initialize = dispatcher.dispatch(
            {"jsonrpc": "2.0", "id": 11, "method": "initialize", "params": {"protocolVersion": []}}
        )
        self.assertEqual(malformed_initialize["result"]["protocolVersion"], mcp_protocol.MCP_PROTOCOL_VERSION)
        self.assertIn("resourceTemplates", dispatcher.dispatch({"jsonrpc": "2.0", "id": 9, "method": "resources/templates/list"})["result"])
        self.assertIn("prompts", dispatcher.dispatch({"jsonrpc": "2.0", "id": 10, "method": "prompts/list"})["result"])

    def test_bloformer_boundaries_and_deformation_targets(self):
        with self.assertRaises(ValueError):
            bloformer.volume_cells((0, 0, 0), 0)
        with self.assertRaises(ValueError):
            bloformer.volume_cells((0, 0, 0), float("nan"))
        with self.assertRaises(ValueError):
            bloformer.volume_cells((0, 0, 0), 1, shape="bad")
        with self.assertRaises(ValueError):
            bloformer.volume_cells((0, 0, 0), 1, axis="bad")
        with self.assertRaises(ValueError):
            bloformer.apply_bloformer(make_asset(), thickness=0)
        with self.assertRaises(ValueError):
            bloformer.apply_bloformer(make_asset(), palette_index=True)
        protected = make_asset({})
        skipped = bloformer.apply_bloformer(protected, radius=1, protected={(0, 0, 0)})
        self.assertEqual(skipped.skipped_protected, 1)
        allowed = bloformer.apply_bloformer(
            protected,
            radius=1,
            protected={(0, 0, 0)},
            allow_protected=True,
        )
        self.assertGreater(allowed.added, 0)
        inflated = make_asset({(0, 0, 0): 0})
        self.assertGreater(
            bloformer.apply_bloformer(inflated, radius=1, mode="INFLATE").added,
            0,
        )
        eroded = make_asset(solid_cells(3))
        self.assertGreater(
            bloformer.apply_bloformer(eroded, radius=1, mode="ERODE").removed,
            0,
        )
        carved = make_asset(solid_cells(3))
        self.assertGreater(
            bloformer.apply_bloformer(carved, radius=2, mode="CARVE", thickness=2).removed,
            0,
        )

    def test_assembly_and_kitbash_boundaries(self):
        self.assertTrue(scene_assembly.new_placement_id())
        with self.assertRaises(ValueError):
            scene_assembly.ScenePlacement("p", "", "Name")
        with self.assertRaises(ValueError):
            scene_assembly.ScenePlacement("p", "blob", "Name", location=(float("nan"), 0, 0))
        with self.assertRaises(TypeError):
            scene_assembly.SceneAssembly(settings=[])
        placement = scene_assembly.ScenePlacement("p1", "blob", "One")
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly([placement, placement])
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly.decode("bad")
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly.from_dict({"format": "bad", "format_version": 1})
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly.from_dict(
                {"format": scene_assembly.ASSEMBLY_FORMAT, "format_version": 1.5, "placements": []}
            )
        source = make_asset(sockets=[voxel.VoxelSocket((0, 0, 0), (1, 0, 0), "*")])
        target = make_asset(sockets=[voxel.VoxelSocket((2, 0, 0), (-1, 0, 0), "hand")])
        self.assertEqual(kitbash.socket_compatibility(source.sockets[0], target.sockets[0]), 1)
        self.assertIsNone(
            kitbash.socket_compatibility(
                source.sockets[0],
                voxel.VoxelSocket((2, 0, 0), (0, 1, 0), "hand"),
            )
        )
        with self.assertRaises(IndexError):
            kitbash.grid_translation(source, target, kitbash.SocketMatch(5, 0, 0, 0))

    def test_minecraft_volume_and_file_dispatch(self):
        self.assertEqual(minecraft_formats.minecraft_block_for_color(minecraft_formats.minecraft_block_color("stone")), "minecraft:stone")
        volume = minecraft_formats.MinecraftVolume.from_world_blocks(
            {(-1, 2, 3): "stone", (1, 2, 3): "air", (0, 2, 3): "minecraft:gold_block"},
            data_version=12,
            metadata={"source": "test"},
        )
        self.assertEqual(volume.origin, (-1, 2, 3))
        self.assertEqual(volume.get((1, 0, 0)), "minecraft:gold_block")
        volume.set((0, 0, 0), "air")
        volume.set((0, 0, 0), "minecraft:oak_log[facing=east]")
        self.assertIn("minecraft:oak_log", volume.get((0, 0, 0)))
        with self.assertRaises(ValueError):
            volume.set((99, 0, 0), "stone")
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume(0, 1, 1)
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume(1.5, 1, 1)
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume(1, 1, 1, {(0.5, 0, 0): "stone"})
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume(1, 1, 1, origin=(0.0, 0, 0))
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume.from_world_blocks({(0.5, 0, 0): "air"})
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume(True, 1, 1)
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume("invalid", 1, 1)
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume(1, 1, 1, {None: "stone"})
        self.assertEqual(len(minecraft_formats.MinecraftVolume.from_world_blocks({}).blocks), 0)
        for hint, encoder in (
            ("SCHEM", minecraft_formats.encode_sponge_schematic),
            ("SCHEMATIC", minecraft_formats.encode_legacy_schematic),
            ("STRUCTURE", minecraft_formats.encode_structure),
        ):
            data = encoder(volume)
            self.assertGreater(len(minecraft_formats.decode_minecraft_nbt(data, format_hint=hint).blocks), 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schem = root / "test.schem"
            self.assertEqual(minecraft_formats.encode_minecraft_file(volume, schem, "AUTO"), (schem,))
            self.assertGreater(len(minecraft_formats.decode_minecraft_file(schem).blocks), 0)
            mca = root / "test.mca"
            small_volume = minecraft_formats.MinecraftVolume(2, 1, 1, {(0, 0, 0): "stone"}, origin=(0, 0, 0))
            self.assertEqual(minecraft_formats.encode_minecraft_file(small_volume, mca, "MCA"), (mca,))
            self.assertGreater(len(minecraft_formats.decode_minecraft_file(mca).blocks), 0)
            world = root / "world"
            written = minecraft_formats.encode_minecraft_file(volume, world, "MCA")
            self.assertTrue(written)
            self.assertGreater(len(minecraft_formats.decode_minecraft_file(world).blocks), 0)
            with self.assertRaises(OSError):
                minecraft_formats.decode_minecraft_file(root / "missing.nbt")
        with self.assertRaises(ValueError):
            minecraft_formats.decode_minecraft_nbt(minecraft_nbt.encode_nbt(minecraft_nbt.nbt_compound("x")), format_hint="bad")

    def test_stack_encodings_psd_and_limits(self):
        pixels = ((1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 0.5))
        layer = stacking.StackLayer("Pixels", 2, 1, pixels)
        encoded = layer.to_dict()
        self.assertEqual(stacking.StackLayer.from_dict(encoded).pixel(1, 0)[1], 1.0)
        self.assertEqual(layer.pixel(-1, 0), (0.0, 0.0, 0.0, 0.0))
        raw = base64.b64encode(bytes((255, 0, 0, 255, 0, 0, 255, 128))).decode("ascii")
        self.assertEqual(len(stacking.StackLayer.from_dict({"width": 2, "height": 1, "pixels": raw}).pixels), 2)
        self.assertEqual(
            len(stacking.StackLayer.from_dict({"width": 2, "height": 1, "pixels": [1, 0, 0, 1, 0, 1, 0, 1]}).pixels),
            2,
        )
        with self.assertRaises(ValueError):
            stacking.StackLayer("bad", 1, 1, ())
        with self.assertRaises(ValueError):
            stacking.StackLayer("bad", 1.5, 1, ((1.0, 0.0, 0.0, 1.0),))
        with self.assertRaises(ValueError):
            stacking.StackLayer("bad", 1, 1, ((1.0, 0.0, 0.0, 1.0),), offset=(1.5, 0))
        with self.assertRaises(ValueError):
            stacking.StackLayer("bad", "invalid", 1, ((1.0, 0.0, 0.0, 1.0),))
        with self.assertRaises(ValueError):
            stacking.StackLayer.from_dict({"width": 1, "height": 1, "pixels": {"encoding": "bad", "data": ""}})
        with self.assertRaises(ValueError):
            stacking.StackLayer("bad", 0, 1, ())
        with self.assertRaises(ValueError):
            stacking.StackLayer("bad", 1, 1, ((1.0, 0.0, 0.0, 1.0),), offset=None)
        document = stacking.StackDocument.from_layers([layer], canvas_size=(2, 1))
        with self.assertRaises(ValueError):
            stacking.decode_stack_manifest("[]", lambda _name, _data: layer)
        with self.assertRaises(ValueError):
            stacking.decode_openraster(b"bad", lambda _name, _data: layer)
        with self.assertRaises(ValueError):
            stacking.decode_psd(b"bad")
        for axis in ("X", "Y", "Z"):
            self.assertTrue(stacking.stack_to_voxel_asset(document, axis=axis).voxels)
        with self.assertRaises(ValueError):
            stacking.stack_to_voxel_asset(document, axis="bad")
        with self.assertRaises(ValueError):
            stacking.stack_to_voxel_asset(document, alpha_threshold=2)
        with self.assertRaises(ValueError):
            stacking.stack_to_voxel_asset(document, voxel_scale=0)
        with self.assertRaises(ValueError):
            stacking.stack_to_voxel_asset(document, palette_limit=0)

        channel_values = ((0, 255), (1, 0), (2, 0), (-1, 255))
        channel_payloads = [struct.pack(">H", 0) + bytes((value,)) for _channel, value in channel_values]
        extra = b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00" + b"\x01A\x00\x00"
        record = struct.pack(">iiiiH", 0, 0, 1, 1, len(channel_values))
        record += b"".join(struct.pack(">hI", channel_id, len(payload)) for (channel_id, _value), payload in zip(channel_values, channel_payloads))
        record += b"8BIMnorm" + bytes((255, 0, 0, 0)) + struct.pack(">I", len(extra)) + extra
        layer_info = struct.pack(">h", 1) + record + b"".join(channel_payloads)
        layer_mask = struct.pack(">I", len(layer_info)) + layer_info
        psd = (
            b"8BPS"
            + struct.pack(">H", 1)
            + b"\x00" * 6
            + struct.pack(">HIIHH", 4, 1, 1, 8, 3)
            + struct.pack(">I", 0)
            + struct.pack(">I", 0)
            + struct.pack(">I", len(layer_mask))
            + layer_mask
        )
        decoded_psd = stacking.decode_psd(psd)
        self.assertEqual(decoded_psd.layers[0].name, "A")

    def test_placement_lasso_and_bloformer_remaining_edges(self):
        self.assertEqual(
            asset_placer.polyline_samples([(0, 0, 0), (0, 0, 0)], spacing=1),
            ((0.0, 0.0, 0.0),),
        )
        self.assertEqual(
            len(asset_placer.polyline_samples([(0, 0, 0), (1, 0, 0)], spacing=1, closed=True)),
            2,
        )
        malformed = SimpleNamespace(voxels={(0, 0): 0, (1, 1, 1): 99}, palette=[(1.0, 0.5)])
        self.assertEqual(asset_placer.thumbnail_pixels(malformed, 8, 8), (0.0,) * (8 * 8 * 4))
        with self.assertRaises(ValueError):
            blob.rounded_lasso_voxels(((0, 0), (1, 0), (0, 1)), palette_index=True)
        with self.assertRaises(ValueError):
            blob.rounded_lasso_voxels(((0, 0), (1, 0), (float("nan"), 1)))
        with self.assertRaises(ValueError):
            bloformer.volume_cells((0, 0, 0), 1000)
        carved = make_asset({(0, 0, 0): 0})
        self.assertGreater(
            bloformer.apply_bloformer(carved, radius=1, mode="CARVE", thickness=2).removed,
            0,
        )
        smooth_remove = make_asset({(0, 0, 0): 0})
        self.assertGreater(
            bloformer.apply_bloformer(smooth_remove, radius=1, mode="SMOOTH").removed,
            0,
        )
        smooth_add = make_asset({
            (-1, 0, 0): 0,
            (1, 0, 0): 0,
            (0, -1, 0): 0,
            (0, 1, 0): 0,
        })
        self.assertGreater(
            bloformer.apply_bloformer(smooth_add, radius=1, mode="SMOOTH").added,
            0,
        )

    def test_edit_counts_and_flood_empty_region(self):
        asset = make_asset({(0, 0, 0): 0})
        with edit.VoxelEditTransaction(asset) as transaction:
            transaction.add((0, 0, 0), 1, overwrite=False)
            transaction.add((0, 0, 0), 1)
            transaction.paint((0, 0, 0), 1)
            transaction.flood_fill((2, 0, 0), (2, 0, 0), 1)
            report = transaction.commit()
        self.assertEqual(report.skipped_existing, 1)
        self.assertEqual(report.painted, 1)
        self.assertEqual(report.added, 1)
        with self.assertRaises(ValueError):
            edit.VoxelEditTransaction(asset).fill_box((1, 0, 0), (0, 0, 0))

    def test_kitbash_tag_mismatch_and_pick_traversal(self):
        source_socket = voxel.VoxelSocket((0, 0, 0), (1, 0, 0), "source")
        target_socket = voxel.VoxelSocket((1, 0, 0), (-1, 0, 0), "target")
        source = make_asset(sockets=[source_socket])
        target = make_asset(sockets=[target_socket])
        self.assertIsNone(kitbash.socket_compatibility(source_socket, target_socket))
        self.assertIsNone(kitbash.find_best_match(source, target))
        asset = make_asset({(1, 0, 0): 0})
        hit = pick.raycast_voxel(asset, (-2.0, 0.5, 0.5), (1, 0, 0))
        self.assertEqual(hit.cell, (1, 0, 0))
        self.assertEqual(hit.normal, (-1, 0, 0))
        self.assertIsNone(pick.raycast_voxel(asset, (0.5, 0.5, 0.5), (-1, 0, 0), max_distance=0.1))

    def test_layer_and_shading_helper_edges(self):
        with self.assertRaises(ValueError):
            layers.Layer.from_dict({"voxels": [[0, 0, 0, True]]})
        stack = layers.LayerStack()
        stack.add("Top")
        stack.move_active(99)
        stack.move_active(-99)
        with self.assertRaises(ValueError):
            stack.merge_down([])
        stack.active.locked = True
        self.assertEqual(stack.active.copy().locked, True)
        stack.active.voxels = {(0, 0, 0): 0}
        with self.assertRaises(ValueError):
            stack.remap_palette_indices({})
        with self.assertRaises(ValueError):
            stack.remap_palette_indices({0: -1})
        interior_asset = make_asset(solid_cells(3))
        report = shading.shade_voxels(interior_asset, surface_only=False, light_direction=(0, 0, 0))
        self.assertEqual(report.considered, 27)
        protected = make_asset({(0, 0, 0): 0})
        self.assertEqual(
            shading.dither_voxels(
                protected,
                0,
                1,
                cells=[(0, 0, 0)],
                source_indices=(99,),
                protected=[(0, 0, 0)],
                allow_protected=True,
            ).skipped_protected,
            0,
        )

    def test_protocol_io_layers_and_optimizer_edges(self):
        dispatcher = mcp_protocol.MCPDispatcher(lambda _name, _arguments: object())
        self.assertIn(
            "protocolVersions",
            dispatcher.dispatch({"jsonrpc": "2.0", "id": 1, "method": "server/discover"})["result"],
        )
        self.assertEqual(
            dispatcher.dispatch(
                {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "old"}}
            )["result"]["protocolVersion"],
            mcp_protocol.MCP_PROTOCOL_VERSION,
        )
        internal = dispatcher.dispatch({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": mcp_protocol.TOOL_DEFINITIONS[0]["name"], "arguments": {}},
        })
        self.assertEqual(internal["error"]["code"], -32603)
        self.assertEqual(dispatcher.dispatch({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})["result"], {"resources": []})
        self.assertEqual(
            dispatcher.dispatch(
                {"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "missing"}}
            )["error"]["code"],
            -32601,
        )
        self.assertEqual(dispatcher.dispatch({"jsonrpc": "2.0", "id": 6, "method": "logging/setLevel"})["result"], {})
        self.assertIn(
            "animate",
            dispatcher.dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "prompts/get",
                    "params": {"name": "voxtart_animate_character"},
                }
            )["result"]["description"],
        )

        asset = make_asset()
        with self.assertRaises(ValueError):
            asset_io.decode_asset("[]")
        with self.assertRaises(ValueError):
            asset_io.decode_asset(json.dumps({"format": asset_io.FORMAT_NAME, "format_version": 2}))
        with self.assertRaises(ValueError):
            asset_io.decode_asset(json.dumps({"format": asset_io.FORMAT_NAME, "format_version": 1.5}))
        payload = json.loads(asset_io.encode_asset(asset))
        payload["layers"] = "bad"
        with self.assertRaises(ValueError):
            asset_io.decode_asset_with_layers(json.dumps(payload))

        with self.assertRaises(ValueError):
            optimize.DecimationPolicy(threshold="invalid")
        with self.assertRaises(ValueError):
            optimize.DecimationPolicy(protected_palette_index=True)
        self.assertEqual(optimize.DecimationPolicy(threshold="any").required_occupancy(8), 1)
        self.assertEqual(optimize.DecimationPolicy(threshold="full").required_occupancy(8), 8)
        socket = voxel.VoxelSocket((1, 1, 1), (1, 0, 0))
        decimated, _report = optimize.decimate(
            make_asset({(1, 1, 1): 0}, sockets=[socket, socket]),
            optimize.DecimationPolicy(2),
        )
        self.assertEqual(len(decimated.sockets), 1)

        quad = meshing.MeshData(
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [(0, 1, 2, 3)],
            [5],
        )
        optimized, report = mesh_optimize.optimize_mesh_data(
            quad,
            weld_vertices=False,
            remove_degenerate=False,
            remove_duplicate=False,
            triangulate=True,
            compact_materials=False,
        )
        self.assertEqual(len(optimized.faces), 2)
        self.assertEqual(optimized.materials, [5, 5])
        self.assertEqual(report.material_remap, ())
        scaled = meshing.scaled_mesh(make_asset({(0, 0, 0): 0}), backend="greedy")
        self.assertTrue(scaled.vertices)
        scaled_asset = make_asset({(0, 0, 0): 0})
        scaled_asset.grid_size = 2.0
        self.assertTrue(meshing.scaled_mesh(scaled_asset).vertices)

        with self.assertRaises(ValueError):
            layers.Layer(voxels={(True, 0, 0): 0})
        with self.assertRaises(ValueError):
            layers.LayerStack(schema_version=99)
        with self.assertRaises(ValueError):
            layers.LayerStack(schema_version=True)
        with self.assertRaises(ValueError):
            layers.LayerStack(layers=[layers.Layer() for _ in range(layers.MAX_LAYERS + 1)])
        lower = layers.Layer(name="Lower", voxels={(0, 0, 0): 0})
        upper = layers.Layer(
            name="Upper",
            voxels={(1, 0, 0): 0},
            locked=True,
            surface_backend="MARCHING",
        )
        merge_stack = layers.LayerStack(layers=[lower, upper], active_index=1)
        merge_stack.merge_down(PALETTE)
        self.assertEqual(merge_stack.layers[0].surface_backend, "MARCHING")
        self.assertEqual(len(merge_stack.layers), 1)
        merge_stack.layers[0].voxels[(2, 0, 0)] = 99
        with self.assertRaises(ValueError):
            merge_stack.composite(PALETTE)
        with self.assertRaises(ValueError):
            layers._blend((0, 0, 0, 1), (1, 1, 1, 1), 1.0, "invalid")

        with self.assertRaises(ValueError):
            shading.shade_voxels(asset, light_direction=(1, 2))
        with self.assertRaises(ValueError):
            shading.shade_voxels(asset, cells=[(True, 0, 0)])
        self.assertEqual(shading.shade_voxels(make_asset({}), surface_only=False).considered, 0)
        self.assertGreaterEqual(shading.dither_value((1, 2, 3), pattern="CIRCUIT", bayer_size=8), 0.0)

    def test_voxel_blockbench_and_nbt_codec_edges(self):
        xyzi = struct.pack("<I", 1) + bytes((0, 0, 0, 1))
        vox = (
            b"VOX "
            + struct.pack("<I", voxel_formats.MAGICA_VOX_VERSION)
            + voxel_formats._vox_chunk(
                b"MAIN",
                children=voxel_formats._vox_chunk(b"XYZI", xyzi),
            )
        )
        fallback_models = voxel_formats.decode_magica_vox_models(vox)
        self.assertEqual(len(fallback_models), 1)
        self.assertEqual(len(fallback_models[0].asset.voxels), 1)
        qb = struct.pack("<IIIIII", 0x101, 0, 1, 0, 0, 1)
        qb += bytes((0,)) + struct.pack("<IIIiiiI", 1, 1, 1, 0, 0, 0, 0)
        self.assertEqual(len(voxel_formats.decode_qb_models(qb)[0].asset.voxels), 0)
        with self.assertRaises((ValueError, struct.error)):
            voxel_formats.decode_qb_models(b"\x00" * 24)
        with self.assertRaises((ValueError, struct.error)):
            voxel_formats.decode_magica_vox_models(b"VOX " + struct.pack("<I", 150) + b"MAIN")
        with self.assertRaises(ValueError):
            voxel_formats._color((0, 0, 0))

        payload = {
            "voxtart_palette": [[255, 0, 0, 255], ["bad"]],
            "elements": [
                {"visibility": False, "from": [0, 0, 0], "to": [1, 1, 1]},
                {"type": "ignored", "from": [0, 0, 0], "to": [1, 1, 1]},
                {
                    "type": "cube",
                    "from": [0, 0, 0],
                    "to": [1, 1, 1],
                    "rotation": {"angle": 90, "axis": "x", "origin": [0, 0, 0]},
                    "color": 0,
                },
                {
                    "type": "cube",
                    "from": [1, 0, 0],
                    "to": [2, 1, 1],
                    "rotation": [10, 20, 30],
                    "color": "#00ff00",
                },
                {
                    "type": "cube",
                    "from": [2, 0, 0],
                    "to": [3, 1, 1],
                    "color": 99,
                },
                {
                    "type": "cube",
                    "from": [3, 0, 0],
                    "to": [4, 1, 1],
                    "color": "not-hex",
                },
                {"type": "mesh", "vertices": []},
                {
                    "type": "mesh",
                    "vertices": [[0, 0, 0], [1, 1, 1]],
                    "rotation": {"angle": 45, "axis": "z"},
                    "color": [0.1, 0.2, 0.3],
                },
            ],
        }
        imported = blockbench_formats.decode_blockbench(payload)
        self.assertGreater(len(imported.voxels), 0)
        self.assertEqual(blockbench_formats.BlockbenchModel(imported).source_format, "bbmodel")
        bedrock = {
            "minecraft:geometry": [
                None,
                {
                    "bones": [
                        None,
                        {"name": "root", "cubes": [None, {}, {"origin": [0, 0, 0], "size": [1, 1, 1]}]},
                    ]
                },
            ]
        }
        self.assertEqual(len(blockbench_formats.decode_blockbench(bedrock).voxels), 1)
        with self.assertRaises(ValueError):
            blockbench_formats.decode_blockbench(b"[]")

        root = minecraft_nbt.nbt_compound("root", {
            "byte": minecraft_nbt.nbt_byte("byte", -1),
            "short": minecraft_nbt.nbt_short("short", -2),
            "int": minecraft_nbt.nbt_int("int", 3),
            "long": minecraft_nbt.nbt_long("long", 4),
            "float": minecraft_nbt.nbt_float("float", 0.5),
            "double": minecraft_nbt.nbt_double("double", 0.25),
            "bytes": minecraft_nbt.nbt_byte_array("bytes", (-1, 2)),
            "text": minecraft_nbt.nbt_string("text", "hello"),
            "ints": minecraft_nbt.nbt_int_array("ints", (1, 2)),
            "longs": minecraft_nbt.nbt_long_array("longs", (-1, 2)),
        })
        for compression in ("gzip", "zlib"):
            encoded = minecraft_nbt.encode_nbt(root, compression=compression)
            self.assertEqual(
                minecraft_nbt.decode_nbt(encoded, compression=compression).tag_type,
                minecraft_nbt.TAG_COMPOUND,
            )
        with self.assertRaises(TypeError):
            minecraft_nbt.nbt_compound("bad", {"value": 1})
        with self.assertRaises(ValueError):
            minecraft_nbt.compound_fields(minecraft_nbt.nbt_int("value", 1))
        with self.assertRaises(TypeError):
            minecraft_nbt.encode_nbt(minecraft_nbt.nbt_int("root", 1))
        with self.assertRaises(ValueError):
            minecraft_nbt.encode_nbt(root, compression="bad")
        with self.assertRaises((ValueError, EOFError)):
            minecraft_nbt.decode_nbt(b"\x1f\x8b", compression="auto")
        with self.assertRaises(ValueError):
            minecraft_nbt.decode_nbt(bytes((13, 0, 0)), compression="raw")
        negative_count = bytes((minecraft_nbt.TAG_COMPOUND, 0, 1, 114, minecraft_nbt.TAG_BYTE_ARRAY, 0, 1, 98)) + struct.pack(">i", -1) + b"\x00"
        with self.assertRaises(ValueError):
            minecraft_nbt.decode_nbt(negative_count, compression="raw")
        bad_list = minecraft_nbt.NBTTag("list", minecraft_nbt.TAG_LIST, ())
        with self.assertRaises(TypeError):
            minecraft_nbt.encode_nbt(minecraft_nbt.nbt_compound("root", {"list": bad_list}))

    def test_minecraft_and_stacking_codec_edges(self):
        self.assertNotEqual(
            minecraft_formats.minecraft_block_color("custom:unknown"),
            minecraft_formats.minecraft_block_color("minecraft:stone"),
        )
        volume = minecraft_formats.MinecraftVolume(
            2,
            1,
            1,
            {(0, 0, 0): "minecraft:stone", (1, 0, 0): "minecraft:gold_block"},
        )
        self.assertEqual(len(volume.to_voxel_asset(palette_limit=1).palette), 1)
        transparent = make_asset({(0, 0, 0): 0}, palette_values=[(1.0, 0.0, 0.0, 0.0)])
        self.assertEqual(
            minecraft_formats.MinecraftVolume.from_voxel_asset(transparent).get((0, 0, 0)),
            "minecraft:stone",
        )
        self.assertEqual(minecraft_formats._decode_varints((0x81, 0x01), 1), [129])
        with self.assertRaises(ValueError):
            minecraft_formats._decode_varints((1,), 2)
        with self.assertRaises(ValueError):
            minecraft_formats._encode_varints((-1,))
        legacy_section = minecraft_nbt.nbt_compound("", {
            "Y": minecraft_nbt.nbt_byte("Y", 0),
            "Blocks": minecraft_nbt.nbt_byte_array("Blocks", (1,)),
        }).value
        legacy_root = minecraft_nbt.nbt_compound("", {
            "sections": minecraft_nbt.nbt_list("sections", minecraft_nbt.TAG_COMPOUND, (legacy_section,)),
        })
        self.assertEqual(
            minecraft_formats._decode_chunk(legacy_root, 0, 0)[(0, 0, 0)],
            "minecraft:stone",
        )
        bad_region = bytearray(minecraft_formats.MINECRAFT_REGION_HEADER + minecraft_formats.MINECRAFT_REGION_SECTOR)
        bad_region[0:4] = (2 << 8 | 1).to_bytes(4, "big")
        bad_region[minecraft_formats.MINECRAFT_REGION_HEADER:minecraft_formats.MINECRAFT_REGION_HEADER + 4] = struct.pack(">I", 1)
        bad_region[minecraft_formats.MINECRAFT_REGION_HEADER + 4] = 9
        with self.assertRaises(ValueError):
            minecraft_formats.decode_anvil_region(bytes(bad_region))
        with tempfile.TemporaryDirectory() as directory:
            empty_world = Path(directory) / "empty"
            empty_world.mkdir()
            with self.assertRaises(ValueError):
                minecraft_formats.decode_minecraft_world(empty_world)
            with self.assertRaises(ValueError):
                minecraft_formats.encode_minecraft_file(volume, Path(directory) / "x.bad", "bad")
        empty_region = minecraft_formats.encode_anvil_region(minecraft_formats.MinecraftVolume(1, 1, 1))
        self.assertEqual(len(empty_region), minecraft_formats.MINECRAFT_REGION_HEADER)

        layer = stacking.StackLayer("Layer", 1, 1, ((1.0, 0.0, 0.0, 1.0),))
        raw_pixels = bytes((255, 0, 0, 255))
        compressed_pixels = base64.b64encode(zlib.compress(raw_pixels)).decode("ascii")
        decoded = stacking.StackLayer.from_dict({
            "width": 1,
            "height": 1,
            "pixels": {"encoding": "rgba8+zlib+base64", "data": compressed_pixels},
        })
        self.assertEqual(decoded.pixel(0, 0), layer.pixel(0, 0))
        with self.assertRaises(ValueError):
            stacking.StackLayer.from_dict({
                "width": 1,
                "height": 1,
                "pixels": {"encoding": "rgba8+zlib+base64", "data": "bad"},
            })
        with self.assertRaises(ValueError):
            stacking.StackLayer.from_dict({"width": True, "height": 1, "pixels": []})
        with self.assertRaises(ValueError):
            stacking.StackLayer("Layer", 1, 1, ((1.0, 0.0, 0.0),))
        document = stacking.StackDocument.from_layers([layer])
        with self.assertRaises(ValueError):
            stacking.StackDocument.decode("{")
        invalid_document = document.to_dict()
        invalid_document["format"] = "bad"
        with self.assertRaises(ValueError):
            stacking.StackDocument.from_dict(invalid_document)
        nested_manifest = {"stack": {"slices": [{"file": "slice.png", "visible": "hidden", "x": 1, "y": -1}]}}
        nested = stacking.decode_stack_manifest(
            json.dumps(nested_manifest),
            lambda _name, _data: layer,
        )
        self.assertFalse(nested.layers[0].visible)
        with self.assertRaises(ValueError):
            stacking.decode_openraster(
                io.BytesIO().getvalue(),
                lambda _name, _data: layer,
            )
        with self.assertRaises(ValueError):
            stacking.stack_to_voxel_asset(document, layer_order="bad")
        multi = stacking.StackDocument.from_layers([
            layer,
            stacking.StackLayer("Other", 1, 1, ((0.0, 1.0, 0.0, 1.0),)),
        ])
        self.assertTrue(stacking.stack_to_voxel_asset(multi, palette_limit=1).voxels)
        self.assertEqual(stacking._psd_channel(struct.pack(">HH", 0, 65535), 1, 1, 16), (1.0,))
        rle = struct.pack(">HH", 1, 2) + bytes((0, 127))
        self.assertEqual(stacking._psd_channel(rle, 1, 1, 8), (127 / 255.0,))
        compressed = struct.pack(">H", 2) + zlib.compress(bytes((127,)))
        self.assertEqual(stacking._psd_channel(compressed, 1, 1, 8), (127 / 255.0,))
        with self.assertRaises(ValueError):
            stacking._psd_channel(struct.pack(">H", 3), 1, 1, 8)

    def test_remaining_math_edit_model_and_parser_boundaries(self):
        thumbnail_asset = SimpleNamespace(voxels={(0, 0, 0): 0}, palette=[(1.0, 0.5)])
        self.assertEqual(
            len(asset_placer.thumbnail_pixels(thumbnail_asset, 8, 8)),
            8 * 8 * 4,
        )
        with self.assertRaises(ValueError):
            blob.rounded_lasso_voxels(((0, 0), (5000, 0), (5000, 5000), (0, 5000)))
        with self.assertRaises(ValueError):
            bloformer.apply_bloformer(make_asset(), center=(float("nan"), 0, 0))
        with self.assertRaises(ValueError):
            bloformer.apply_bloformer(make_asset(), radius=0)

        asset = make_asset({(0, 0, 0): 0})
        transaction = edit.VoxelEditTransaction(asset, protected={(0, 0, 0)})
        transaction.paint((0, 0, 0), 1)
        transaction.rollback()
        transaction.rollback()
        with self.assertRaises(ValueError):
            transaction = edit.VoxelEditTransaction(asset)
            transaction.add((True, 0, 0))
        with self.assertRaises(ValueError):
            pick.raycast_voxel(asset, (float("nan"), 0, 0), (1, 0, 0))
        traversed_asset = make_asset({(1, 1, 0): 0, (3, 0, 0): 0})
        traversed = pick.raycast_voxel(traversed_asset, (-2.0, 0.5, 0.5), (1, 0, 0))
        self.assertEqual(traversed.cell, (3, 0, 0))
        self.assertIsNone(pick.raycast_voxel(asset, (-2, -2, 0.5), (1, 2, 0)))

        with self.assertRaises(ValueError):
            voxel.VoxelAsset(voxels={(True, 0, 0): 0})
        with self.assertRaises(ValueError):
            voxel.VoxelAsset(palette=[(2.0, 0.0, 0.0, 1.0)])
        with self.assertRaises(ValueError):
            voxel.VoxelSocket((0, 0, 0), (1, 0, 0), tag="")

        object_bones = [
            SimpleNamespace(name="root", parent=None, use_deform=True, selected=False),
            SimpleNamespace(name="child", parent=SimpleNamespace(name="root"), use_deform=False, selected=False),
        ]
        self.assertEqual(
            game_rig.make_extraction_plan(object_bones, include_root=False).names,
            ("root",),
        )
        with self.assertRaises(ValueError):
            game_rig.make_extraction_plan(object_bones, hierarchy="bad")
        with self.assertRaises(ValueError):
            game_rig.make_extraction_plan([], mode="ALL_BONES")
        with self.assertRaises(ValueError):
            game_rig.make_extraction_plan(
                [{"name": "a", "use_deform": True}, {"name": "a", "use_deform": True}],
            )
        with self.assertRaises(ValueError):
            game_rig.make_extraction_plan([{"name": "a"}], mode="DEFORM_ONLY")

        with self.assertRaises(ValueError):
            palette.coerce_color(1)
        with self.assertRaises(ValueError):
            palette.coerce_color((float("nan"), 0, 0))
        with self.assertRaises(ValueError):
            palette.coerce_color("#12345")
        with self.assertRaises(ValueError):
            palette.normalize_palette([(index / 256.0, 0, 0, 1) for index in range(257)])
        self.assertEqual(
            len(palette.parse_hex_palette("; comment\nbad\n1 2 nope\n1 2 3")),
            2,
        )
        self.assertEqual(
            len(palette.parse_gpl("GIMP PALETTE\nbad\n1 2 nope\n1 2 3")),
            1,
        )
        self.assertEqual(
            len(palette.parse_jasc_pal("JASC-PAL\n0100\n2\nbad\n1 2 3")),
            1,
        )
        self.assertEqual(
            len(palette.parse_palette_bytes(b"GIMP PALETTE\n1 2 3", ".gpl")),
            1,
        )
        self.assertEqual(
            len(palette.parse_palette_bytes(b"JASC-PAL\n0100\n1\n1 2 3", ".pal")),
            1,
        )
        with self.assertRaises(ValueError):
            palette.parse_ase(b"ASEF" + struct.pack(">HHI", 1, 0, 1))

        with self.assertRaises(ValueError):
            scene_assembly.ScenePlacement("p", "blob", "Name", scale=(0, 1, 1))
        with self.assertRaises(ValueError):
            scene_assembly.ScenePlacement("p", "blob", "Name", location="bad")
        with self.assertRaises(ValueError):
            scene_assembly.ScenePlacement.from_dict(None)
        with self.assertRaises(TypeError):
            scene_assembly.SceneAssembly().upsert(object())
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly(settings={"bad": object()})
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly.from_dict({
                "format": scene_assembly.ASSEMBLY_FORMAT,
                "format_version": 1,
                "placements": {},
            })
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly.from_dict({
                "format": scene_assembly.ASSEMBLY_FORMAT,
                "format_version": 1,
                "settings": [],
            })

    def test_stacking_validation_and_psd_helpers(self):
        with self.assertRaises(ValueError):
            stacking.StackLayer("bad", 1, 1, ((2.0, 0.0, 0.0, 1.0),))
        with self.assertRaises(ValueError):
            stacking.StackLayer("bad", 1, 1, ((1.0, 0.0, 0.0, 1.0),), offset=(0,))
        with self.assertRaises(ValueError):
            stacking.StackLayer("bad", 0, 1, ())
        with self.assertRaises(ValueError):
            stacking.StackLayer("bad", 1, 1, ((1.0, 0.0, 0.0, 1.0),), opacity=float("nan"))
        layer = stacking.StackLayer("Layer", 1, 1, ((1.0, 0.0, 0.0, 1.0),), offset=(-2, -3))
        normalized = stacking.StackDocument.from_layers([layer])
        self.assertEqual(normalized.layers[0].offset, (0, 0))
        with self.assertRaises(ValueError):
            stacking.StackDocument.from_layers([])
        with self.assertRaises(TypeError):
            stacking.StackDocument(1, 1, (object(),))
        with self.assertRaises(ValueError):
            stacking.StackDocument(1, 1, (layer,) * (stacking.MAX_STACK_LAYERS + 1))

        with self.assertRaises(ValueError):
            stacking.decode_stack_manifest(
                json.dumps({"layers": [1]}),
                lambda _name, _data: layer,
            )
        with self.assertRaises(ValueError):
            stacking.decode_stack_manifest(
                json.dumps({"layers": [{}]}),
                lambda _name, _data: layer,
            )
        frames = stacking.decode_stack_manifest(
            json.dumps({"frames": [layer.to_dict()]}),
            lambda _name, _data: layer,
        )
        self.assertEqual(len(frames.layers), 1)

        missing_stack = io.BytesIO()
        with ZipFile(missing_stack, "w", ZIP_DEFLATED) as archive:
            archive.writestr("data/a.png", b"pixels")
        with self.assertRaises(ValueError):
            stacking.decode_openraster(missing_stack.getvalue(), lambda _name, _data: layer)
        no_layers = io.BytesIO()
        with ZipFile(no_layers, "w", ZIP_DEFLATED) as archive:
            archive.writestr("stack.xml", '<image w="1" h="1"/>')
        with self.assertRaises(ValueError):
            stacking.decode_openraster(no_layers.getvalue(), lambda _name, _data: layer)
        missing_layer = io.BytesIO()
        with ZipFile(missing_layer, "w", ZIP_DEFLATED) as archive:
            archive.writestr("stack.xml", '<image w="1" h="1"><layer src="missing.png"/></image>')
        with self.assertRaises(ValueError):
            stacking.decode_openraster(missing_layer.getvalue(), lambda _name, _data: layer)

        extra = (
            struct.pack(">II", 0, 0)
            + b"\x01A\x00\x00"
            + b"8BIMluni"
            + struct.pack(">I", 6)
            + struct.pack(">I", 1)
            + "B".encode("utf-16-be")
        )
        self.assertEqual(stacking._psd_extra_name(extra, 0, len(extra), "fallback"), "B")
        self.assertEqual(stacking._psd_extra_name(b"bad", 0, 3, "fallback"), "fallback")
        self.assertEqual(stacking._pascal_name(b"\x01A\x00\x00", 0), ("A", 4))
        with self.assertRaises(ValueError):
            stacking._pascal_name(b"", 0)
        self.assertEqual(stacking._packbits(bytes((0xFF, 7)), 2), bytes((7, 7)))
        self.assertEqual(stacking._psd_channel(b"", 0, 0, 8), ())
        with self.assertRaises(ValueError):
            stacking._psd_channel(struct.pack(">H", 0) + b"\x00", 1, 1, 32)
        record = stacking._PSDRecord(
            "Gray",
            0,
            0,
            1,
            1,
            (stacking._PSDChannel(0, 3),),
            1.0,
            True,
        )
        pixels = stacking._psd_layer_pixels(record, {0: struct.pack(">H", 0) + b"\x7f"}, 8, 1)
        self.assertEqual(pixels[0][0], 127 / 255.0)

        invisible = stacking.StackLayer("Invisible", 1, 1, ((1.0, 0.0, 0.0, 1.0),), visible=False)
        transparent = stacking.StackLayer("Transparent", 1, 1, ((1.0, 0.0, 0.0, 0.0),))
        outside = stacking.StackLayer("Outside", 1, 1, ((0.0, 1.0, 0.0, 1.0),), offset=(2, 2))
        empty_document = stacking.StackDocument.from_layers(
            [invisible, transparent, outside],
            canvas_size=(1, 1),
        )
        empty_asset = stacking.stack_to_voxel_asset(empty_document)
        self.assertEqual(empty_asset.palette, [(0.8, 0.8, 0.8, 1.0)])

    def test_gallery_metadata_and_scan_boundaries(self):
        asset = make_asset(sockets=[voxel.VoxelSocket((0, 0, 0), (0, 0, 1), "head")])
        with self.assertRaises(ValueError):
            blob_gallery.BlobJoint("x" * 97, asset.sockets[0])
        with self.assertRaises(ValueError):
            blob_gallery.BlobJoint.from_dict(None)
        joints = blob_gallery.joints_from_asset(
            asset,
            [
                {"socket_index": "bad"},
                {"socket_index": 0, "name": "Head"},
                {"socket_index": 0, "name": "Duplicate"},
                {"socket_index": 99},
                object(),
            ],
        )
        self.assertEqual(len(joints), 1)
        self.assertEqual(
            len(blob_gallery.joints_from_asset(asset, "not-json")),
            1,
        )
        entry = blob_gallery.make_blob_entry(
            asset,
            "Hero",
            blob_id="hero",
            category="Characters",
            description="A test hero",
            author="Tester",
            license="MIT",
            source_url="https://example.test/hero",
            provenance={"source": 1},
        )
        with self.assertRaises(ValueError):
            blob_gallery.BlobGalleryEntry("hero", "Hero", asset, schema_version=99)
        invalid_version = entry.to_dict()
        invalid_version["format_version"] = 1.5
        with self.assertRaises(ValueError):
            blob_gallery.BlobGalleryEntry.from_dict(invalid_version)
        with self.assertRaises(TypeError):
            blob_gallery.BlobGalleryEntry("hero", "Hero", object())
        with self.assertRaises(TypeError):
            blob_gallery.BlobGalleryEntry("hero", "Hero", asset, joints=(object(),))
        entry_data = entry.to_dict()
        entry_data["joints"] = None
        self.assertEqual(len(blob_gallery.BlobGalleryEntry.from_dict(entry_data).joints), 1)
        with self.assertRaises(ValueError):
            blob_gallery.decode_blob(blob_gallery.encode_blob(entry), max_bytes=1)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gallery = blob_gallery.BlobGallery(root)
            with self.assertRaises(ValueError):
                blob_gallery.BlobGallery(root, max_file_bytes=0)
            gallery.save(entry)
            (root / blob_gallery.FAVORITES_FILENAME).write_text(
                json.dumps(["hero", "../bad", 1]),
                encoding="utf-8",
            )
            self.assertEqual(gallery.favorites(), frozenset({"hero"}))
            self.assertTrue(gallery.is_favorite("hero"))
            self.assertEqual(len(gallery.scan(category="Other")[0]), 0)
            self.assertEqual(len(gallery.scan(query="missing")[0]), 0)
            gallery.set_favorite("hero", False)
            self.assertFalse(gallery.is_favorite("hero"))
            with self.assertRaises(ValueError):
                blob_gallery.load_blob(gallery.path_for("hero"), max_bytes=1)
            gallery.remove("missing")
            self.assertEqual(gallery.entries(), (entry,))

    def test_io_and_mcp_transport_boundaries(self):
        asset = make_asset()
        with self.assertRaises(ValueError):
            asset_io.decode_asset_with_layers("[]")
        with self.assertRaises(ValueError):
            asset_io.save_asset(asset, Path(""))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing"
            target.mkdir()
            with self.assertRaises(OSError):
                asset_io.save_asset(asset, target)

        dispatcher = mcp_protocol.MCPDispatcher(lambda _name, _arguments: object())
        self.assertIsNone(
            dispatcher.dispatch({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": mcp_protocol.TOOL_DEFINITIONS[0]["name"], "arguments": {}},
            })
        )
        self.assertEqual(
            dispatcher.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}})["error"]["code"],
            -32602,
        )
        self.assertEqual(
            dispatcher.dispatch(
                {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {}}
            )["error"]["code"],
            -32602,
        )

    def test_low_coverage_branch_contracts(self):
        lasso = blob.rounded_lasso_voxels(((0, 0), (1, 0), (1, 0), (0, 1)))
        self.assertTrue(lasso)
        self.assertGreater(
            bloformer.apply_bloformer(
                make_asset(),
                radius=1,
                mode="CARVE",
                thickness=3,
            ).removed,
            0,
        )
        self.assertEqual(
            game_rig.make_extraction_plan(
                [{"name": "", "use_deform": True}, {"name": "root", "use_deform": True}],
                root_name="root",
            ).names,
            ("root",),
        )
        with self.assertRaises(ValueError):
            game_rig.make_extraction_plan([{"name": "root"}], root_name="root", mode="DEFORM_ONLY")
        notification_error = mcp_protocol.MCPDispatcher(lambda _name, _arguments: {"ok": True}).dispatch(
            {"jsonrpc": "1.0", "method": "ping"}
        )
        self.assertIsNone(notification_error)

        with self.assertRaises(ValueError):
            layers._color((1.0, 0.0, 0.0))
        stack = layers.LayerStack()
        self.assertIs(stack.set_active(0), stack.active)
        full_stack = layers.LayerStack(layers=[layers.Layer() for _ in range(layers.MAX_LAYERS)])
        with self.assertRaises(ValueError):
            full_stack.add()
        with self.assertRaises(ValueError):
            full_stack.duplicate_active()
        transparent = layers.Layer(
            voxels={(0, 0, 0): 0},
        )
        self.assertEqual(
            layers.LayerStack(layers=[transparent]).composite([(1.0, 0.0, 0.0, 0.0)]),
            {},
        )

        with self.assertRaises(ValueError):
            mesh_optimize.optimize_mesh_data(
                meshing.MeshData([(0, 0, 0)], [], [0, 0]),
            )
        self.assertEqual(meshing._material_at_point((0.0, 0.0, 0.0), {}), 0)
        self.assertEqual(optimize.DecimationReport(0, 0, 1, 2, 2, "any", 0, 0, 0, 0, 0).to_dict()["factor"], 2)
        self.assertEqual(optimize._material_for_block([], [], 7), 7)
        mixed = make_asset({(0, 0, 0): 0, (1, 0, 0): 1})
        self.assertGreater(
            optimize.decimate(mixed, optimize.DecimationPolicy(2, threshold="any"))[1].mixed_material_blocks,
            0,
        )

        with self.assertRaises(ValueError):
            palette.coerce_color((1.0, 0.0))
        self.assertEqual(
            len(palette.parse_palette_bytes(b'{"colors": ["#fff"]}', ".json")),
            1,
        )
        self.assertEqual(palette._ase_rgb("LAB ", (0.1, 0.2, 0.3)), None)
        with self.assertRaises(ValueError):
            palette.parse_ase(b"ASEF" + struct.pack(">HHI", 1, 0, 1) + struct.pack(">HI", 1, 10) + b"")
        with self.assertRaises(ValueError):
            palette.parse_jasc_pal("JASC-PAL\n0100\nbad")

        self.assertEqual(shading.ShadingReport("x", 0, 0, 0, 0).to_dict()["operation"], "x")
        with self.assertRaises(ValueError):
            shading.surface_normal(make_asset({}), (0, 0, 0))
        self.assertEqual(shading._center_normal(make_asset({}), (0, 0, 0)), (0.0, 0.0, 1.0))
        with self.assertRaises(ValueError):
            shading._clamp_color((1.0, 0.0, 0.0))
        changed_asset = make_asset(
            {(0, 0, 0): 0},
            palette_values=[(0.6, 0.6, 0.6, 1.0), (1.0, 1.0, 1.0, 1.0)],
        )
        self.assertEqual(
            shading.shade_voxels(changed_asset, ambient=1.0, strength=1.0, levels=2).changed,
            1,
        )
        with self.assertRaises(ValueError):
            shading.dither_value((0, 0, 0), noise_amount=2)
        with self.assertRaises(ValueError):
            shading.dither_value((0, 0, 0), threshold_bias=2)

        placement = scene_assembly.ScenePlacement("p", "blob", "Name")
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly(
                placements=[placement] * (scene_assembly.MAX_PLACEMENTS + 1),
            )
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly(schema_version=99)

        with self.assertRaises(ValueError):
            voxel.VoxelAsset(schema_version=99)

    def test_voxel_format_failure_contracts(self):
        def vox_file(*children):
            return (
                b"VOX "
                + struct.pack("<I", voxel_formats.MAGICA_VOX_VERSION)
                + voxel_formats._vox_chunk(b"MAIN", children=b"".join(children))
            )

        self.assertEqual(
            list(voxel_formats._vox_chunks(
                voxel_formats._vox_chunk(b"ROOT", children=voxel_formats._vox_chunk(b"LEAF")),
                0,
                24,
            )),
            [(b"ROOT", b""), (b"LEAF", b"")],
        )
        with self.assertRaises(ValueError):
            list(voxel_formats._vox_chunks(b"", 1, 0))
        with self.assertRaises(ValueError):
            list(voxel_formats._vox_chunks(b"\x00" * 12 + b"x", 0, 13))
        with self.assertRaises(TypeError):
            voxel_formats.decode_magica_vox_models("bad")
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox_models(b"VOX " + struct.pack("<I", 0) + b"MAIN")
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox_models(b"VOX " + struct.pack("<I", 150) + b"ABCD")
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox_models(
                b"VOX " + struct.pack("<I", 150) + b"MAIN" + struct.pack("<II", 0, 999)
            )
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox_models(vox_file(voxel_formats._vox_chunk(b"RGBA", b"short")))
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox_models(vox_file(voxel_formats._vox_chunk(b"SIZE", b"short")))
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox_models(
                vox_file(voxel_formats._vox_chunk(b"SIZE", struct.pack("<III", 0, 1, 1)))
            )
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox_models(vox_file(voxel_formats._vox_chunk(b"XYZI", b"\x00")))
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox_models(
                vox_file(voxel_formats._vox_chunk(b"XYZI", struct.pack("<I", voxel_formats.MAX_FORMAT_VOXELS + 1)))
            )
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox_models(
                vox_file(
                    voxel_formats._vox_chunk(b"SIZE", struct.pack("<III", 1, 1, 1)),
                    voxel_formats._vox_chunk(b"XYZI", struct.pack("<I", 1) + bytes((1, 0, 0, 1))),
                )
            )
        color_zero = voxel_formats._vox_chunk(
            b"XYZI",
            struct.pack("<I", 2) + bytes((0, 0, 0, 0, 0, 0, 0, 1)),
        )
        self.assertEqual(len(voxel_formats.decode_magica_vox_models(vox_file(color_zero))[0].asset.voxels), 1)
        with self.assertRaises(ValueError):
            voxel_formats._Reader(b"").read(1)
        with self.assertRaises(ValueError):
            voxel_formats._qb_color((1, 0, 0, 1), 3)
        self.assertTrue(voxel_formats._qb_voxel_stream((voxel_formats.QB_CODEFLAG,), 1, 1, 1, True))
        with self.assertRaises(ValueError):
            voxel_formats.encode_qb_models(())
        with self.assertRaises(ValueError):
            voxel_formats.encode_magica_vox_models((make_asset({}),))
        wide = make_asset({(0, 0, 0): 0, (256, 0, 0): 0})
        with self.assertRaises(ValueError):
            voxel_formats.encode_magica_vox(wide)

        bad_color = struct.pack("<IIIIII", 0x101, 3, 1, 0, 0, 1)
        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(bad_color)
        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(struct.pack("<IIIIII", 0x101, 0, 1, 0, 0, 0))
        zero_size = struct.pack("<IIIIII", 0x101, 0, 1, 0, 0, 1) + bytes((0,)) + struct.pack("<IIIiii", 0, 1, 1, 0, 0, 0)
        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(zero_size)
        compressed_header = struct.pack("<IIIIII", 0x101, 0, 1, 1, 0, 1) + bytes((0,))
        matrix = struct.pack("<IIIiii", 2, 1, 1, 0, 0, 0)
        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(
                compressed_header + matrix + struct.pack("<II", 1, voxel_formats.QB_NEXTSLICEFLAG)
            )
        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(
                compressed_header + struct.pack("<IIIiii", 1, 1, 1, 0, 0, 0)
                + struct.pack("<III", voxel_formats.QB_CODEFLAG, 0, 1)
            )
        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(
                compressed_header + struct.pack("<IIIiii", 1, 1, 1, 0, 0, 0)
                + struct.pack("<II", 1, 2)
            )
        visible_header = struct.pack("<IIIIII", 0x101, 0, 1, 0, 1, 1) + bytes((0,))
        visible_data = struct.pack("<IIIiiiI", 1, 1, 1, 0, 0, 0, 0x01020304)
        self.assertEqual(len(voxel_formats.decode_qb_models(visible_header + visible_data)[0].asset.voxels), 1)

    def test_minecraft_private_codec_boundaries(self):
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume(1, 1, 1, {(1, 0, 0): "stone"})
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume(1, 1, 1, origin=(0, 0))
        with self.assertRaises(ValueError):
            minecraft_formats.MinecraftVolume(1, 1, 1, {(0, 0, 0): "stone"}).to_voxel_asset(palette_limit=0)
        self.assertEqual(len(minecraft_formats.MinecraftVolume.from_voxel_asset(make_asset({})).blocks), 0)
        self.assertEqual(minecraft_formats._list_values(minecraft_nbt.nbt_int("value", 1)), ())
        self.assertEqual(minecraft_formats._payload_fields(1), {})
        with self.assertRaises(ValueError):
            minecraft_formats._decode_varints((0x80,) * 6, 1)
        self.assertEqual(minecraft_formats._decode_packed_indices((), 2, 1), [0, 0])
        packed = minecraft_formats._encode_packed_indices((15,) * 20, 20)
        self.assertEqual(len(minecraft_formats._decode_packed_indices(packed, 20, 20)), 20)
        self.assertEqual(minecraft_formats._encode_packed_indices((1,), 1), ())
        root = minecraft_nbt.nbt_compound("", {})
        self.assertEqual(minecraft_formats._read_chunk_payload(bytes((3,)) + minecraft_nbt.encode_nbt(root, compression="raw")[0:]), root)
        with self.assertRaises(ValueError):
            minecraft_formats._read_chunk_payload(b"")
        with self.assertRaises(ValueError):
            minecraft_formats._read_chunk_payload(bytes((9,)))
        with self.assertRaises(ValueError):
            minecraft_formats.decode_anvil_region(b"")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-a-world"
            path.write_bytes(b"data")
            with self.assertRaises(ValueError):
                minecraft_formats.decode_minecraft_world(path)
            written = minecraft_formats.write_anvil_world(
                minecraft_formats.MinecraftVolume(1, 1, 1),
                Path(directory) / "empty-world",
            )
            self.assertEqual(len(written), 1)
        spanning = minecraft_formats.MinecraftVolume(
            513,
            1,
            1,
            {(0, 0, 0): "stone", (512, 0, 0): "stone"},
        )
        with self.assertRaises(ValueError):
            minecraft_formats.encode_anvil_region(spanning, region_x=0, region_z=0)


    def test_minecraft_format_error_and_legacy_branches(self):
        empty_volume = minecraft_formats.MinecraftVolume(1, 1, 1)
        self.assertEqual(empty_volume.to_voxel_asset().palette, [minecraft_formats.minecraft_block_color("minecraft:stone")])
        with self.assertRaises(ValueError):
            minecraft_formats._encode_varints((-1,))
        self.assertEqual(minecraft_formats._decode_packed_indices((), 2, 2), [0, 0])
        root = minecraft_nbt.nbt_compound("", {})
        for compression in ("gzip", "zlib"):
            encoded = minecraft_nbt.encode_nbt(root, compression=compression)
            self.assertEqual(
                minecraft_formats._read_chunk_payload(bytes((1 if compression == "gzip" else 2,)) + encoded),
                root,
            )
        legacy_fields = {
            "Width": minecraft_nbt.nbt_short("Width", 2),
            "Height": minecraft_nbt.nbt_short("Height", 1),
            "Length": minecraft_nbt.nbt_short("Length", 1),
            "Blocks": minecraft_nbt.nbt_byte_array("Blocks", (0,)),
        }
        with self.assertRaises(ValueError):
            minecraft_formats.decode_legacy_schematic(minecraft_nbt.nbt_compound("", legacy_fields))
        legacy_fields["Blocks"] = minecraft_nbt.nbt_byte_array("Blocks", (0, 0))
        legacy_fields["AddBlocks"] = minecraft_nbt.nbt_byte_array("AddBlocks", (0x01,))
        self.assertEqual(
            len(minecraft_formats.decode_legacy_schematic(minecraft_nbt.nbt_compound("", legacy_fields)).blocks),
            1,
        )
        invalid_structure = minecraft_nbt.nbt_compound("", {
            "size": minecraft_nbt.nbt_list("size", minecraft_nbt.TAG_INT, (1, 2)),
        })
        with self.assertRaises(ValueError):
            minecraft_formats.decode_structure(invalid_structure)
        structure = minecraft_nbt.nbt_compound("", {
            "size": minecraft_nbt.nbt_list("size", minecraft_nbt.TAG_INT, (1, 1, 1)),
            "palette": minecraft_nbt.nbt_list(
                "palette",
                minecraft_nbt.TAG_COMPOUND,
                (minecraft_nbt.nbt_compound("", {"Name": minecraft_nbt.nbt_string("Name", "minecraft:stone")}).value,),
            ),
            "blocks": minecraft_nbt.nbt_list(
                "blocks",
                minecraft_nbt.TAG_COMPOUND,
                (minecraft_nbt.nbt_compound("", {
                    "pos": minecraft_nbt.nbt_list("pos", minecraft_nbt.TAG_INT, (0, 0)),
                    "state": minecraft_nbt.nbt_int("state", 0),
                }).value,),
            ),
        })
        self.assertEqual(minecraft_formats.decode_structure(structure).blocks, {})
        empty_section = minecraft_nbt.nbt_compound("", {
            "Y": minecraft_nbt.nbt_byte("Y", 0),
            "block_states": minecraft_nbt.nbt_compound("block_states", {
                "palette": minecraft_nbt.nbt_list("palette", minecraft_nbt.TAG_COMPOUND, ()),
            }),
        }).value
        empty_chunk = minecraft_nbt.nbt_compound("", {
            "sections": minecraft_nbt.nbt_list("sections", minecraft_nbt.TAG_COMPOUND, (empty_section,)),
        })
        self.assertEqual(minecraft_formats._decode_chunk(empty_chunk, 0, 0), {})
        legacy_section = minecraft_nbt.nbt_compound("", {
            "Y": minecraft_nbt.nbt_byte("Y", 0),
            "Blocks": minecraft_nbt.nbt_byte_array("Blocks", (1, 1)),
            "Add": minecraft_nbt.nbt_byte_array("Add", (0x10,)),
        }).value
        legacy_chunk = minecraft_nbt.nbt_compound("", {
            "sections": minecraft_nbt.nbt_list("sections", minecraft_nbt.TAG_COMPOUND, (legacy_section,)),
        })
        self.assertGreaterEqual(len(minecraft_formats._decode_chunk(legacy_chunk, 0, 0)), 1)
        old_limit = minecraft_formats.MAX_MINECRAFT_BLOCKS
        minecraft_formats.MAX_MINECRAFT_BLOCKS = 0
        try:
            with self.assertRaises(ValueError):
                minecraft_formats.MinecraftVolume(1, 1, 1, {(0, 0, 0): "stone"})
        finally:
            minecraft_formats.MAX_MINECRAFT_BLOCKS = old_limit
        old_chunks = minecraft_formats.MAX_MINECRAFT_CHUNKS
        minecraft_formats.MAX_MINECRAFT_CHUNKS = 0
        try:
            region = bytearray(minecraft_formats.MINECRAFT_REGION_HEADER + minecraft_formats.MINECRAFT_REGION_SECTOR)
            region[0:4] = (2 << 8 | 1).to_bytes(4, "big")
            with self.assertRaises(ValueError):
                minecraft_formats.decode_anvil_region(bytes(region))
        finally:
            minecraft_formats.MAX_MINECRAFT_CHUNKS = old_chunks
        old_encode_chunk = minecraft_formats._encode_chunk
        minecraft_formats._encode_chunk = lambda *args, **kwargs: b"x" * (minecraft_formats.MINECRAFT_REGION_SECTOR * 256)
        try:
            with self.assertRaises(ValueError):
                minecraft_formats.encode_anvil_region(
                    minecraft_formats.MinecraftVolume(1, 1, 1, {(0, 0, 0): "stone"}),
                )
        finally:
            minecraft_formats._encode_chunk = old_encode_chunk
        with tempfile.TemporaryDirectory() as directory:
            bad_region_name = Path(directory) / "r.bad.name.mca"
            bad_region_name.write_bytes(b"")
            self.assertEqual(len(minecraft_formats.decode_minecraft_world(Path(directory)).blocks), 0)
    def test_nbt_depth_and_writer_boundaries(self):
        with self.assertRaises(ValueError):
            minecraft_nbt._read_payload(minecraft_nbt._Reader(b""), minecraft_nbt.TAG_BYTE, minecraft_nbt.MAX_NBT_DEPTH + 1)
        with self.assertRaises(ValueError):
            minecraft_nbt.decode_nbt(bytes((minecraft_nbt.TAG_END,)), compression="raw")
        corrupted_gzip = bytearray(minecraft_nbt.encode_nbt(minecraft_nbt.nbt_compound("x"), compression="gzip"))
        corrupted_gzip[-1] ^= 0xFF
        with self.assertRaises(ValueError):
            minecraft_nbt.decode_nbt(bytes(corrupted_gzip), compression="auto")
        with self.assertRaises(ValueError):
            minecraft_nbt.encode_nbt(
                minecraft_nbt.nbt_compound(
                    "root",
                    {"long": minecraft_nbt.nbt_string("long", "x" * 65536)},
                ),
                compression="raw",
            )
        with self.assertRaises(ValueError):
            minecraft_nbt._write_payload(minecraft_nbt.TAG_BYTE, 1, minecraft_nbt.MAX_NBT_DEPTH + 1)
        with self.assertRaises(TypeError):
            minecraft_nbt._write_payload(minecraft_nbt.TAG_COMPOUND, ())
        with self.assertRaises(ValueError):
            minecraft_nbt._write_payload(13, None)
        self.assertEqual(minecraft_nbt._write_named(minecraft_nbt.NBTTag("", minecraft_nbt.TAG_END, None)), b"\x00")

    def test_last_small_public_branch_contracts(self):
        self.assertIsNone(
            pick.raycast_voxel(
                make_asset({(1, 1, 0): 0, (3, 0, 0): 0}),
                (1.5, 0.5, 0.5),
                (1, 0, 0),
                max_distance=0.2,
            )
        )
        self.assertIsNone(
            pick.raycast_voxel(make_asset({(0, 0, 0): 0}), (2, 0.5, 0.5), (-1, 0, 0), max_distance=0.2)
        )
        dispatcher = mcp_protocol.MCPDispatcher(lambda _name, _arguments: {})
        self.assertEqual(
            dispatcher.dispatch({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": None})["result"],
            {},
        )
        self.assertEqual(
            len(palette.parse_gpl("GIMP PALETTE\n# comment\n1 2 3")),
            1,
        )
        self.assertEqual(
            len(palette.parse_jasc_pal("JASC-PAL\n0100\n1\nx y z\n1 2 3")),
            1,
        )
        with self.assertRaises(ValueError):
            palette.parse_json_palette("{}")

        with self.assertRaises(ValueError):
            scene_assembly.ScenePlacement("p", "blob", "x" * 257)
        with self.assertRaises(TypeError):
            scene_assembly.SceneAssembly(placements=[object()])
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly.from_dict([])
        with self.assertRaises(ValueError):
            scene_assembly.SceneAssembly.from_dict({
                "format": scene_assembly.ASSEMBLY_FORMAT,
                "format_version": 2,
                "placements": [],
            })
        placements = [
            scene_assembly.ScenePlacement(f"p{index}", "blob", "Name")
            for index in range(scene_assembly.MAX_PLACEMENTS)
        ]
        full_assembly = scene_assembly.SceneAssembly(placements=placements)
        with self.assertRaises(ValueError):
            full_assembly.upsert(scene_assembly.ScenePlacement("new", "blob", "New"))

        asset = make_asset({(0, 0, 0): 0})
        with self.assertRaises(ValueError):
            shading.dither_voxels(asset, 0, 1, group_size=0)
        with self.assertRaises(ValueError):
            shading.dither_voxels(asset, 0, 1, pattern="bad")
        with self.assertRaises(ValueError):
            shading.dither_voxels(asset, 0, 1, noise_amount=2)
        with self.assertRaises(ValueError):
            shading.dither_voxels(asset, 0, 1, threshold_bias=2)
        report = shading.dither_voxels(
            asset,
            0,
            1,
            density=1.0,
            protected=[(0, 0, 0)],
        )
        self.assertEqual(report.skipped_protected, 1)

    def test_blockbench_helper_boundaries(self):
        with self.assertRaises(ValueError):
            blockbench_formats._vec3((0, 1), "test")
        with self.assertRaises(ValueError):
            blockbench_formats._vec3((0, 1, float("nan")), "test")
        with self.assertRaises(ValueError):
            blockbench_formats._color((2.0, -1.0, 0.0))
        with self.assertRaises(ValueError):
            blockbench_formats._color((float("nan"), 0.0, 0.0))
        self.assertEqual(blockbench_formats._palette_from_payload({"palette": 1}), [])
        palette_values = [(1.0, 0.0, 0.0, 1.0)]
        self.assertEqual(blockbench_formats._element_color({"color": 0}, palette_values), palette_values[0])
        self.assertNotEqual(blockbench_formats._element_color({"color": 99}, []), blockbench_formats._element_color({}, []))
        self.assertEqual(blockbench_formats._mesh_bounds({"vertices": []}), None)
        self.assertEqual(blockbench_formats._mesh_bounds({"vertices": "bad"}), None)
        payload = {
            "elements": [
                {"type": "cube"},
                {"type": "mesh", "vertices": [[0, 0, 0], [1, 1, 1]], "color": [1, 0, 0]},
            ]
        }
        self.assertGreater(len(blockbench_formats.decode_blockbench(payload).voxels), 0)
        with self.assertRaises(ValueError):
            blockbench_formats.decode_blockbench(payload, units_per_voxel=0)
        with self.assertRaises(ValueError):
            blockbench_formats._color((1.0, 2.0))
        self.assertEqual(blockbench_formats._palette_from_payload({"palette": [None]}), [])
        self.assertEqual(blockbench_formats._element_color({"color": [None]}, []), blockbench_formats._indexed_color(0))
        self.assertEqual(blockbench_formats._element_color({"color": "#bad"}, []), blockbench_formats._indexed_color(0))
        self.assertEqual(
            blockbench_formats._mesh_bounds({"vertices": {"a": [0, 0, 0], "b": [1, 1, 1]}}),
            ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
        )
        with self.assertRaises(ValueError):
            blockbench_formats.decode_blockbench(b"not-json")
        with self.assertRaises(ValueError):
            blockbench_formats.decode_blockbench(payload, units_per_voxel=float("nan"))
        hidden = {"elements": [{"from": [0, 0, 0], "to": [1, 1, 1], "visibility": False}]}
        self.assertEqual(len(blockbench_formats.decode_blockbench(hidden).palette), 1)
        old_limit = blockbench_formats.MAX_BLOCKBENCH_ELEMENTS
        blockbench_formats.MAX_BLOCKBENCH_ELEMENTS = 0
        try:
            with self.assertRaises(ValueError):
                blockbench_formats.decode_blockbench(payload)
        finally:
            blockbench_formats.MAX_BLOCKBENCH_ELEMENTS = old_limit

    def test_gallery_error_cleanup_paths(self):
        asset = make_asset()
        entry = blob_gallery.make_blob_entry(asset, "Entry", blob_id="entry")
        with self.assertRaises(ValueError):
            blob_gallery.BlobGalleryEntry.from_dict([])
        missing_asset = entry.to_dict()
        missing_asset.pop("asset")
        with self.assertRaises(ValueError):
            blob_gallery.BlobGalleryEntry.from_dict(missing_asset)
        bad_joints = entry.to_dict()
        bad_joints["joints"] = {}
        with self.assertRaises(ValueError):
            blob_gallery.BlobGalleryEntry.from_dict(bad_joints)
        version_one = entry.to_dict()
        version_one["format_version"] = 1
        self.assertEqual(blob_gallery.BlobGalleryEntry.from_dict(version_one).blob_id, "entry")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gallery = blob_gallery.BlobGallery(root)
            gallery.save(entry)
            target = root / "bad.voxtart.blob.json"
            target.mkdir()
            with self.assertRaises(OSError):
                blob_gallery.save_blob(entry, target)
            self.assertEqual(gallery.load("entry").blob_id, "entry")
            gallery.set_favorite("entry", True)
            gallery.set_favorite("entry", False)
            self.assertEqual(gallery.scan(favorites_only=True)[0], ())
            favorites_target = root / blob_gallery.FAVORITES_FILENAME
            favorites_target.unlink()
            favorites_target.mkdir()
            with self.assertRaises(OSError):
                gallery.set_favorite("entry", True)
        missing_gallery = blob_gallery.BlobGallery(Path(directory) / "missing")
        self.assertEqual(missing_gallery.scan(), ((), ()))

    def test_stacking_decoder_error_contracts(self):
        layer = stacking.StackLayer("Layer", 1, 1, ((1.0, 0.0, 0.0, 1.0),))
        encoded = layer.to_dict()
        with self.assertRaises(ValueError):
            stacking._decode_rgba8({"encoding": "bad", "data": ""}, 1)
        with self.assertRaises(ValueError):
            stacking._decode_rgba8({"encoding": "rgba8+zlib+base64", "data": "bad"}, 1)
        with self.assertRaises(ValueError):
            stacking._decode_rgba8(
                {"encoding": "rgba8+zlib+base64", "data": base64.b64encode(zlib.compress(b"\0")).decode()},
                1,
            )
        with self.assertRaises(ValueError):
            stacking._decode_rgba8("bad", 1)
        with self.assertRaises(ValueError):
            stacking._decode_rgba8(base64.b64encode(b"\0").decode(), 1)
        with self.assertRaises(ValueError):
            stacking._decode_rgba8(1, 1)
        with self.assertRaises(ValueError):
            stacking._decode_rgba8([1, 2, 3, 4], 2)
        with self.assertRaises(ValueError):
            stacking._decode_rgba8([(1.0, 0.0, 0.0, 1.0)], 2)
        with self.assertRaises(ValueError):
            stacking.StackLayer.from_dict([])
        with self.assertRaises(ValueError):
            stacking.StackDocument(1, 1, (layer,) * (stacking.MAX_STACK_LAYERS + 1))
        with self.assertRaises(TypeError):
            stacking.StackDocument(1, 1, (object(),))
        with self.assertRaises(ValueError):
            stacking.StackDocument.from_dict({"width": 1, "height": 1, "layers": {}})
        self.assertEqual(stacking.StackLayer.from_dict(encoded).pixel(0, 0), layer.pixel(0, 0))
        reader = stacking._Reader(b"\x7f\x01\x02\xff\xff\x00\x00\x00\x02")
        self.assertEqual(reader.u8(), 127)
        self.assertEqual(reader.u16(), 258)
        self.assertEqual(reader.i16(), -1)
        self.assertEqual(reader.u32(), 2)
        with self.assertRaises(ValueError):
            stacking._Reader(b"").u8()
        with self.assertRaises(ValueError):
            stacking._pascal_name(b"", 0)
        self.assertEqual(stacking._psd_extra_name(b"", 0, 0, "fallback"), "fallback")
        with self.assertRaises(ValueError):
            stacking._packbits(b"\x00", 1)
        with self.assertRaises(ValueError):
            stacking._psd_channel(b"\x00\x01\x00", 1, 1, 8)
        with self.assertRaises(ValueError):
            stacking._psd_channel(b"\x00\x03", 1, 1, 8)
        with self.assertRaises(ValueError):
            stacking._psd_channel(b"\x00\x00", 2, 1, 8)
        with self.assertRaises(ValueError):
            stacking._psd_channel(b"\x00\x00", 1, 1, 24)
        record = stacking._PSDRecord("Gray", 0, 0, 1, 1, (), 1.0, True)
        self.assertEqual(
            stacking._psd_layer_pixels(record, {0: b"\x00\x00\xff"}, 8, 1),
            ((1.0, 1.0, 1.0, 1.0),),
        )

        def psd(layer_mask: bytes, *, version: int = 1, channels: int = 1, mode: int = 3) -> bytes:
            return (
                b"8BPS"
                + struct.pack(">H", version)
                + b"\0" * 6
                + struct.pack(">HIIHH", channels, 1, 1, 8, mode)
                + struct.pack(">I", 0)
                + struct.pack(">I", 0)
                + struct.pack(">I", len(layer_mask))
                + layer_mask
            )

        with self.assertRaises(ValueError):
            stacking.decode_psd(b"bad")
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(b"", version=2))
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(struct.pack(">I", 0), mode=2))
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(struct.pack(">I", 0), channels=0))
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(b"\0\0\0"))
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(struct.pack(">Ih", 1, 0) + b"\0"))
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(struct.pack(">Ih", 2, 1)))
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(struct.pack(">Ih", 20, 1) + b"\0" * 16 + struct.pack(">H", 1)))
        base_record = b"\0" * 16 + struct.pack(">H", 0)
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(struct.pack(">Ih", 24, 1) + base_record + b"xxxx"))
        blend = b"8BIMnorm" + b"\xff\0\0\0"
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(struct.pack(">Ih", 36, 1) + base_record + blend + struct.pack(">I", 4)))
        channel_record = struct.pack(">hI", 0, 3)
        extra = struct.pack(">II", 0, 0) + b"\0" * 4
        pixel_record = (
            struct.pack(">h", 1)
            + b"\0" * 16
            + struct.pack(">H", 1)
            + channel_record
            + blend
            + struct.pack(">I", len(extra))
            + extra
        )
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(struct.pack(">I", len(pixel_record)) + pixel_record))
        empty_layers = struct.pack(">Ih", 2, 0)
        with self.assertRaises(ValueError):
            stacking.decode_psd(psd(empty_layers))


    def test_stacking_manifest_and_raster_boundaries(self):
        layer = stacking.StackLayer("Layer", 1, 1, ((1.0, 0.0, 0.0, 1.0),))
        with self.assertRaises(ValueError):
            stacking._dimension(stacking.MAX_STACK_DIMENSION + 1, "dimension")
        with self.assertRaises(ValueError):
            stacking.StackDocument(4096, 4097, (layer,))
        with self.assertRaises(ValueError):
            stacking.StackDocument.from_dict([])
        with self.assertRaises(ValueError):
            stacking.StackDocument.from_dict({"format": "bad", "schema_version": 1, "layers": []})
        with self.assertRaises(ValueError):
            stacking.StackDocument.from_dict({"format": stacking.STACK_FORMAT, "schema_version": 2, "layers": []})
        with self.assertRaises(ValueError):
            stacking.StackDocument.from_dict({"format": stacking.STACK_FORMAT, "schema_version": 1, "layers": {}})
        with self.assertRaises(ValueError):
            stacking.StackDocument.from_dict({"format": stacking.STACK_FORMAT, "schema_version": True, "layers": []})
        with self.assertRaises(ValueError):
            stacking._manifest_layers(1)
        with self.assertRaises(ValueError):
            stacking.decode_stack_manifest("{", lambda _name, _data: layer)
        with self.assertRaises(ValueError):
            stacking.decode_stack_manifest(json.dumps({"layers": [{"file": "x"}, 1]}), lambda _name, _data: layer)
        with self.assertRaises(ValueError):
            stacking.decode_stack_manifest(json.dumps({"layers": [{}]}), lambda _name, _data: layer)
        with self.assertRaises(ValueError):
            stacking.decode_stack_manifest("[]", lambda _name, _data: layer)
        old_document_limit = stacking.MAX_STACK_DOCUMENT_BYTES
        stacking.MAX_STACK_DOCUMENT_BYTES = 1
        try:
            with self.assertRaises(ValueError):
                stacking.StackDocument.decode("too long")
        finally:
            stacking.MAX_STACK_DOCUMENT_BYTES = old_document_limit
        nested_ora = io.BytesIO()
        with ZipFile(nested_ora, "w", ZIP_DEFLATED) as archive:
            archive.writestr("stack.xml", '<root><image w="1" h="1"><layer src="data/a.png"/></image></root>')
            archive.writestr("data/a.png", b"pixels")
        self.assertEqual(
            stacking.decode_openraster(nested_ora.getvalue(), lambda _name, _data: layer).width,
            1,
        )
        x_outside = stacking.StackLayer(
            "Outside X",
            2,
            1,
            ((1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0)),
        )
        document = stacking.StackDocument.from_layers([x_outside], canvas_size=(1, 1))
        self.assertEqual(len(stacking.stack_to_voxel_asset(document).voxels), 1)
        with self.assertRaises(ValueError):
            stacking._pascal_name(b"\x03A", 0)
        with self.assertRaises(ValueError):
            stacking._packbits(b"\xff", 1)
        with self.assertRaises(ValueError):
            stacking._psd_channel(b"", 1, 1, 8)
    def test_voxel_codec_private_boundaries(self):
        self.assertEqual(voxel_formats._color((255, 0, 0, 255), alpha=0.25)[3], 0.25)
        with self.assertRaises(ValueError):
            tuple(voxel_formats._vox_chunks(b"\0" * 11, 0, 11))
        bad_child = b"MAIN" + struct.pack("<II", 0, 1) + b"\0" * 12
        with self.assertRaises(ValueError):
            tuple(voxel_formats._vox_chunks(bad_child, 0, len(bad_child)))
        empty_main = voxel_formats._vox_chunk(b"MAIN", b"", b"")
        with self.assertRaises(ValueError):
            voxel_formats.decode_magica_vox(b"VOX " + struct.pack("<I", 150) + empty_main)
        empty_asset = voxel.VoxelAsset()
        with self.assertRaises(ValueError):
            voxel_formats.encode_magica_vox_models((empty_asset,))
        model = voxel_formats.VoxelFormatModel("Named", make_asset())
        self.assertEqual(voxel_formats.encode_magica_vox_models((model,))[:4], b"VOX ")
        with self.assertRaises(ValueError):
            voxel_formats.encode_magica_vox_models(())
        many_palette = [
            (index / 255.0, ((index * 3) % 256) / 255.0, ((index * 7) % 256) / 255.0, 1.0)
            for index in range(256)
        ]
        many_colors = voxel.VoxelAsset(palette=many_palette, voxels={(index, 0, 0): index for index in range(256)})
        with self.assertRaises(ValueError):
            voxel_formats.encode_magica_vox(many_colors)
        compressed_asset = voxel.VoxelAsset.box(2)
        self.assertGreater(len(voxel_formats.encode_qb(compressed_asset, compressed=True)), 24)
        self.assertGreater(len(voxel_formats.encode_qb_models((model,), compressed=False, color_format=1)), 24)
        with self.assertRaises(ValueError):
            voxel_formats.encode_qb_models((model,), color_format=2)
        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(b"\0" * 23)

        def qb(matrix_count: int, *, compressed: int = 1, body: bytes = b"", color_format: int = 0) -> bytes:
            return struct.pack("<IIIIII", 0x101, color_format, 1, compressed, 0, matrix_count) + body

        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(qb(4097))
        matrix = struct.pack("<B", 0) + struct.pack("<IIIiii", 2, 1, 1, 0, 0, 0)
        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(qb(1, body=matrix + struct.pack("<II", 1, voxel_formats.QB_NEXTSLICEFLAG)))
        one_matrix = struct.pack("<B", 0) + struct.pack("<IIIiii", 1, 1, 1, 0, 0, 0)
        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(qb(1, body=one_matrix + struct.pack("<III", voxel_formats.QB_CODEFLAG, 2, 1)))
        with self.assertRaises(ValueError):
            voxel_formats.decode_qb_models(qb(1, body=one_matrix + struct.pack("<II", 1, 3)))
        two_planes = struct.pack("<B", 0) + struct.pack("<IIIiii", 1, 1, 2, 0, 0, 0)
        self.assertEqual(
            len(voxel_formats.decode_qb_models(qb(1, body=two_planes + struct.pack("<II", voxel_formats.QB_NEXTSLICEFLAG, voxel_formats.QB_NEXTSLICEFLAG)))),
            1,
        )
        original_palette = voxel_formats._fallback_magica_palette
        try:
            voxel_formats._fallback_magica_palette = lambda: ()
            no_palette = (
                b"VOX "
                + struct.pack("<I", 150)
                + voxel_formats._vox_chunk(
                    b"MAIN",
                    children=voxel_formats._vox_chunk(b"SIZE", struct.pack("<III", 1, 1, 1))
                    + voxel_formats._vox_chunk(b"XYZI", struct.pack("<I", 1) + bytes((0, 0, 0, 1))),
                )
            )
            with self.assertRaises(ValueError):
                voxel_formats.decode_magica_vox(no_palette)
        finally:
            voxel_formats._fallback_magica_palette = original_palette

    def test_remaining_codec_cleanup_branches(self):
        self.assertRegex(blob_gallery.new_blob_id(), r"^[0-9a-f]{32}$")
        original_unlink = Path.unlink

        def missing_unlink(path, *args, **kwargs):
            if path.name.endswith(".tmp"):
                raise FileNotFoundError(path)
            return original_unlink(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = blob_gallery.make_blob_entry(make_asset(), "Cleanup", blob_id="cleanup")
            bad_blob = root / "cleanup.voxtart.blob.json"
            bad_blob.mkdir()
            Path.unlink = missing_unlink
            try:
                with self.assertRaises(OSError):
                    blob_gallery.save_blob(entry, bad_blob)
            finally:
                Path.unlink = original_unlink
            favorites = root / blob_gallery.FAVORITES_FILENAME
            favorites.mkdir()
            gallery = blob_gallery.BlobGallery(root)
            Path.unlink = missing_unlink
            try:
                with self.assertRaises(OSError):
                    gallery.set_favorite("cleanup", True)
            finally:
                Path.unlink = original_unlink
            bad_asset = root / "asset.json"
            bad_asset.mkdir()
            Path.unlink = missing_unlink
            try:
                with self.assertRaises(OSError):
                    asset_io.save_asset(make_asset(), bad_asset)
            finally:
                Path.unlink = original_unlink
        self.assertEqual(
            blockbench_formats._element_color({"color": "#gggggg"}, []),
            blockbench_formats._indexed_color(0),
        )

        self.assertEqual(minecraft_formats._encode_varints((128,)), b"\x80\x01")
        empty_legacy_section = minecraft_nbt.nbt_compound("", {
            "Blocks": minecraft_nbt.nbt_byte_array("Blocks", ()),
        }).value
        empty_legacy_chunk = minecraft_nbt.nbt_compound("", {
            "sections": minecraft_nbt.nbt_list("sections", minecraft_nbt.TAG_COMPOUND, (empty_legacy_section,)),
        })
        self.assertEqual(minecraft_formats._decode_chunk(empty_legacy_chunk, 0, 0), {})
        air_section = minecraft_nbt.nbt_compound("", {
            "Blocks": minecraft_nbt.nbt_byte_array("Blocks", (0,)),
        }).value
        air_chunk = minecraft_nbt.nbt_compound("", {
            "sections": minecraft_nbt.nbt_list("sections", minecraft_nbt.TAG_COMPOUND, (air_section,)),
        })
        self.assertEqual(minecraft_formats._decode_chunk(air_chunk, 0, 0), {})
        old_legacy = dict(minecraft_formats._LEGACY_BLOCKS)
        minecraft_formats._LEGACY_BLOCKS[300] = "minecraft:high_test_block"
        try:
            encoded_legacy = minecraft_formats.encode_legacy_schematic(
                minecraft_formats.MinecraftVolume(1, 1, 1, {(0, 0, 0): "minecraft:high_test_block"}),
            )
            self.assertTrue(encoded_legacy)
        finally:
            minecraft_formats._LEGACY_BLOCKS = old_legacy
        pointer_region = bytearray(
            minecraft_formats.MINECRAFT_REGION_HEADER + minecraft_formats.MINECRAFT_REGION_SECTOR
        )
        pointer_region[0:4] = (3 << 8 | 1).to_bytes(4, "big")
        with self.assertRaises(ValueError):
            minecraft_formats.decode_anvil_region(bytes(pointer_region))

        self.assertEqual(stacking._decode_rgba8([(1.0, 0.0, 0.0, 1.0)], 1), ((1.0, 0.0, 0.0, 1.0),))
        reader = stacking._Reader(b"\x00\x00\x00\x01")
        self.assertEqual(reader.i32(), 1)
        self.assertEqual(
            stacking.decode_stack_manifest(
                json.dumps({"layers": ["one.png"]}),
                lambda _name, _data: stacking.StackLayer("Loaded", 1, 1, ((1.0, 0.0, 0.0, 1.0),)),
            ).layers[0].name,
            "Loaded",
        )
        no_source = io.BytesIO()
        with ZipFile(no_source, "w", ZIP_DEFLATED) as archive:
            archive.writestr("stack.xml", '<image w="1" h="1"><layer name="No source"/></image>')
        with self.assertRaises(ValueError):
            stacking.decode_openraster(no_source.getvalue(), lambda _name, _data: stacking.StackLayer("x", 1, 1, ((1, 0, 0, 1),)))
        with self.assertRaises(ValueError):
            stacking.decode_psd(b"NOPE")
        with self.assertRaises(ValueError):
            stacking.decode_psd(
                b"8BPS"
                + struct.pack(">H", 1)
                + b"\0" * 6
                + struct.pack(">HIIHH", 1, 1, 1, 8, 3)
                + struct.pack(">III", 0, 0, 4)
                + struct.pack(">I", 0),
            )
        invisible_record = (
            struct.pack(">h", 1)
            + b"\0" * 16
            + struct.pack(">H", 0)
            + b"8BIMnorm"
            + b"\xff\0\x02\0"
            + struct.pack(">I", 12)
            + struct.pack(">II", 0, 0)
            + b"\0" * 4
        )
        invisible_mask = struct.pack(">I", len(invisible_record)) + invisible_record
        with self.assertRaises(ValueError):
            stacking.decode_psd(
                b"8BPS"
                + struct.pack(">H", 1)
                + b"\0" * 6
                + struct.pack(">HIIHH", 1, 1, 1, 8, 3)
                + struct.pack(">III", 0, 0, len(invisible_mask))
                + invisible_mask,
            )

        bad_chunk = b"SIZE" + struct.pack("<II", 4, 0) + b"\0"
        with self.assertRaises(ValueError):
            tuple(voxel_formats._vox_chunks(bad_chunk, 0, len(bad_chunk)))
        old_voxel_limit = voxel_formats.MAX_FORMAT_VOXELS
        voxel_formats.MAX_FORMAT_VOXELS = 0
        try:
            with self.assertRaises(ValueError):
                voxel_formats.encode_magica_vox(make_asset())
            with self.assertRaises(ValueError):
                voxel_formats.encode_qb(make_asset())
        finally:
            voxel_formats.MAX_FORMAT_VOXELS = old_voxel_limit
        compressed = voxel_formats.encode_qb(voxel.VoxelAsset.box(2), compressed=True)
        self.assertTrue(voxel_formats.decode_qb_models(compressed)[0].asset.voxels)
        original_qb_color = voxel_formats._decode_qb_color
        voxel_formats._decode_qb_color = lambda _value, _format: (1.0, 0.0, 0.0, 1.0)
        try:
            masked_header = struct.pack("<IIIIII", 0x101, 0, 1, 0, 1, 1)
            masked_data = struct.pack("<BIIIiiiI", 0, 1, 1, 1, 0, 0, 0, 0)
            self.assertEqual(
                voxel_formats.decode_qb_models(masked_header + masked_data)[0].asset.voxels,
                {},
            )
        finally:
            voxel_formats._decode_qb_color = original_qb_color
        reader = meshing._lattice_value

        class EqualValue:
            def __init__(self, inside):
                self.inside = inside

            def __lt__(self, _other):
                return not self.inside

            def __ge__(self, _other):
                return self.inside

            def __sub__(self, _other):
                return 0.0

            def __rsub__(self, _other):
                return 0.0

        meshing._lattice_value = lambda _occupied, point: EqualValue(sum(point) % 2 == 0)
        try:
            meshing.marching_voxel_mesh(make_asset())
        finally:
            meshing._lattice_value = reader
        tetrahedra = meshing._TETRAHEDRA
        lattice = meshing._lattice_value
        meshing._TETRAHEDRA = ((0, 1, 1, 2),)
        meshing._lattice_value = lambda _occupied, point: 1.0 if point == (0, 0, 0) else 0.0
        try:
            meshing.marching_voxel_mesh(make_asset())
        finally:
            meshing._TETRAHEDRA = tetrahedra
            meshing._lattice_value = lattice


class ImageIntakeTest(unittest.TestCase):
    def test_palette_extraction_is_deterministic_and_alpha_aware(self):
        frame = image_intake.ImageFrame.from_rows(
            "paint",
            [
                [(0.0, 0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 1.0), (1.0, 0.0, 0.0, 1.0)],
                [(0.0, 1.0, 0.0, 1.0), (1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0)],
            ],
        )
        indexed = image_intake.index_image(frame)

        self.assertEqual(indexed.palette, ((1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0)))
        self.assertEqual(indexed.pixels, ((None, 0, 0), (1, 0, 1)))
        limited = image_intake.index_image(
            frame,
            image_intake.PaletteIntakeSettings(max_colors=1),
        )
        self.assertEqual(limited.palette, ((1.0, 0.0, 0.0, 1.0),))
        self.assertEqual(limited.pixels, ((None, 0, 0), (0, 0, 0)))
        other = image_intake.ImageFrame.from_rows(
            "other",
            [[(0.0, 0.0, 1.0, 1.0)]],
        )
        shared = image_intake.extract_palette_frames(
            (frame, other),
            image_intake.PaletteIntakeSettings(max_colors=3),
        )
        self.assertEqual(
            shared,
            (
                (1.0, 0.0, 0.0, 1.0),
                (0.0, 1.0, 0.0, 1.0),
                (0.0, 0.0, 1.0, 1.0),
            ),
        )

    def test_indexed_image_coarsen_reduces_cells_without_blurring(self):
        frame = image_intake.ImageFrame.from_rows(
            "macro",
            [
                [
                    (1.0, 0.0, 0.0, 1.0),
                    (1.0, 0.0, 0.0, 1.0),
                    (0.0, 0.0, 0.0, 0.0),
                    (0.0, 0.0, 1.0, 1.0),
                ],
                [
                    (1.0, 0.0, 0.0, 1.0),
                    (0.0, 1.0, 0.0, 1.0),
                    (0.0, 0.0, 0.0, 0.0),
                    (0.0, 0.0, 1.0, 1.0),
                ],
            ],
        )
        indexed = image_intake.index_image(frame)
        coarse = indexed.coarsen(2)
        self.assertEqual(coarse.pixels, ((0, 1),))
        self.assertEqual((coarse.width, coarse.height), (2, 1))
        self.assertEqual(coarse.pixels_per_voxel, 2)
        self.assertEqual(coarse.source_digest, frame.source_digest)
        self.assertEqual(coarse.provenance()["pixels_per_voxel"], "2")
        with self.assertRaises(ValueError):
            indexed.coarsen(0)
        with self.assertRaises(ValueError):
            indexed.coarsen(image_intake.MAX_PIXELS_PER_VOXEL + 1)

    def test_background_modes_remove_only_configured_pixels(self):
        frame = image_intake.ImageFrame.from_rows(
            "background",
            [[(0.0, 0.0, 0.0, 1.0), (1.0, 0.0, 0.0, 1.0)]],
        )
        corner = image_intake.index_image(
            frame,
            image_intake.PaletteIntakeSettings(background_mode="CORNER"),
        )
        explicit = image_intake.index_image(
            frame,
            image_intake.PaletteIntakeSettings(
                background_mode="EXPLICIT",
                background_color=(0.0, 0.0, 0.0, 1.0),
            ),
        )
        self.assertEqual(corner.pixels, ((None, 0),))
        self.assertEqual(explicit.pixels, ((None, 0),))

        transparent = image_intake.ImageFrame.from_rows(
            "transparent-foreground",
            [[(0.0, 0.0, 1.0, 0.0)]],
        )
        retained = image_intake.index_image(
            transparent,
            image_intake.PaletteIntakeSettings(background_mode="NONE"),
        )
        self.assertEqual(retained.pixels, ((0,),))

    def test_calibration_and_provenance_feed_reconstruction(self):
        frame = image_intake.ImageFrame.from_rows(
            "calibrated",
            [[(1.0, 0.0, 0.0, 1.0)]],
        )
        indexed = image_intake.index_image(
            frame,
            image_intake.PaletteIntakeSettings(background_mode="NONE"),
        )
        view = indexed.to_view(axis="Z", origin=(4, 5, 6), depth=1)
        result = reconstruct.reconstruct_views(
            (view,),
            indexed.palette,
            source_provenance=indexed.provenance(),
        )

        self.assertEqual(result.asset.voxels, {(4, 5, 6): 0})
        self.assertEqual(result.report.missing_pixels, 0)
        self.assertEqual(result.report.extra_pixels, 0)
        self.assertEqual(result.asset.provenance["image_name"], "calibrated")
        self.assertEqual(result.asset.provenance["image_width"], "1")
        self.assertEqual(result.asset.provenance["image_height"], "1")
        self.assertEqual(result.asset.provenance["image_sha256"], frame.source_digest)

class ReconstructionTest(unittest.TestCase):
    def test_calibration_roundtrips_for_all_view_axes(self):
        for axis in ("X", "Y", "Z"):
            view = reconstruct.OrthographicView.from_rows(
                axis,
                [[0]],
                axis=axis,
                origin=(3, 4, 5),
                depth=2,
            )
            cell = view.cell_at(0, 0, 1)
            self.assertEqual(view.project_cell(cell), (0, 0, 1))

    def test_surface_mode_preserves_silhouette_and_marks_unknown_depth(self):
        view = reconstruct.OrthographicView.from_rows(
            "front",
            [[0, None], [0, 0]],
            axis="Z",
            origin=(0, 1, 0),
            depth=3,
        )
        result = reconstruct.reconstruct_views((view,), PALETTE[:1])

        self.assertEqual(
            set(result.asset.voxels),
            {(0, 1, 0), (0, 0, 0), (1, 0, 0)},
        )
        self.assertEqual(result.report.unresolved_voxels, 6)
        self.assertEqual(result.report.missing_pixels, 0)
        self.assertEqual(result.report.extra_pixels, 0)
        self.assertEqual(result.report.color_mismatches, 0)
        self.assertEqual(result.render_back(view), view.pixels)
        volume = reconstruct.reconstruct_views(
            (view,),
            PALETTE[:1],
            reconstruct.ReconstructionSettings(mode="VOLUME"),
        )
        self.assertEqual(len(volume.asset.voxels), 9)
        self.assertEqual(volume.report.unresolved_voxels, 0)

    def test_opposing_views_resolve_depth_and_background_conflict(self):
        front = reconstruct.OrthographicView.from_rows(
            "front",
            [[0]],
            axis="Z",
            direction=1,
            origin=(0, 0, 0),
            depth=2,
        )
        back = reconstruct.OrthographicView.from_rows(
            "back",
            [[0]],
            axis="Z",
            direction=-1,
            origin=(0, 0, 1),
            depth=2,
        )
        resolved = reconstruct.reconstruct_views((front, back), PALETTE[:1])
        self.assertEqual(set(resolved.asset.voxels), {(0, 0, 0), (0, 0, 1)})
        self.assertEqual(resolved.report.unresolved_voxels, 0)
        self.assertEqual(resolved.asset.provenance["view_names"], "front,back")

        empty = reconstruct.OrthographicView.from_rows(
            "empty",
            [[None]],
            axis="Z",
            origin=(0, 0, 0),
            depth=2,
        )
        conflict = reconstruct.reconstruct_views((front, empty), PALETTE[:1])
        self.assertEqual(conflict.report.conflicting_rays, 1)
        self.assertEqual(conflict.report.missing_pixels, 1)
        self.assertFalse(conflict.asset.voxels)

    def test_material_conflicts_are_reported_deterministically(self):
        side = reconstruct.OrthographicView.from_rows(
            "side",
            [[1]],
            axis="X",
            origin=(0, 0, 0),
            depth=1,
        )
        front = reconstruct.OrthographicView.from_rows(
            "front",
            [[0]],
            axis="Z",
            origin=(0, 0, 0),
            depth=1,
        )
        result = reconstruct.reconstruct_views((side, front), PALETTE[:2])

        self.assertEqual(result.asset.voxels, {(0, 0, 0): 0})
        self.assertEqual(result.report.material_conflicts, 1)
        self.assertEqual(result.report.color_mismatches, 1)
        self.assertEqual(result.report.visible_material_conflicts, 1)

class SpriteformMigrationTest(unittest.TestCase):
    def test_source_identity_and_evidence_are_canonical(self):
        digest = provenance.sha256_json({"b": 2, "a": 1})
        self.assertEqual(digest, provenance.sha256_json({"a": 1, "b": 2}))
        source = provenance.SourceIdentity(
            "frame",
            digest,
            frame_id="f1",
            pose_id="p1",
            camera_id="c1",
            revision="r1",
        )
        self.assertEqual(provenance.SourceIdentity.from_dict(source.to_dict()), source)
        self.assertEqual(source.key[0], "frame")
        with self.assertRaises(ValueError):
            provenance.SourceIdentity("", digest)
        with self.assertRaises(ValueError):
            provenance.SourceIdentity("frame", "not-a-digest")
        with self.assertRaises(TypeError):
            provenance.SourceIdentity.from_dict([])
        evidence = provenance.EvidenceMap.from_mapping(
            {
                (1, 0, 0): provenance.EvidenceCell("OCCUPIED", 1, 0.75, 0.25, ("frame",)),
                (0, 0, 0): provenance.EvidenceCell("FREE", 1, 1.0, sources=("frame",)),
            },
            (source,),
        )
        self.assertEqual(evidence.get((0, 0, 0)).state, "FREE")
        self.assertEqual(evidence.to_dict()["cells"][0]["cell"], [0, 0, 0])
        self.assertEqual(evidence.digest, provenance.sha256_json(evidence.to_dict()))
        with self.assertRaises(ValueError):
            provenance.EvidenceCell("INVALID")
        with self.assertRaises(ValueError):
            provenance.EvidenceCell("OCCUPIED", confidence=2.0)
        with self.assertRaises(ValueError):
            provenance.EvidenceMap.from_mapping({(0, 0): provenance.EvidenceCell("FREE")})
        with self.assertRaises(ValueError):
            provenance.sha256_json(float("nan"))
        with self.assertRaises(ValueError):
            provenance.SourceIdentity("x" * 257, digest)
        with self.assertRaises(ValueError):
            provenance.SourceIdentity("frame", "x" * 65)
        with self.assertRaises(ValueError):
            provenance.EvidenceCell("FREE", source_mask=-1)
        with self.assertRaises(ValueError):
            provenance.EvidenceCell("FREE", confidence=float("nan"))
        with self.assertRaises(ValueError):
            provenance.EvidenceCell("FREE", error=-1.0)
        with self.assertRaises(ValueError):
            provenance.EvidenceCell("FREE", sources=("",))
        with self.assertRaises(ValueError):
            provenance.EvidenceMap(
                cells=(
                    ((0, 0, 0), provenance.EvidenceCell("FREE")),
                    ((0, 0, 0), provenance.EvidenceCell("FREE")),
                )
            )
        with self.assertRaises(TypeError):
            provenance.EvidenceMap(cells=(((0, 0, 0), "invalid"),))
        with self.assertRaises(TypeError):
            provenance.EvidenceMap(sources=("invalid",))
        with self.assertRaises(ValueError):
            provenance.EvidenceMap(sources=(source, source))
        with self.assertRaises(ValueError):
            evidence.get(None)
        self.assertIsNone(evidence.get((9, 9, 9)))

    def test_comparison_metrics_cover_pixel_depth_and_surface_contracts(self):
        pixels = metrics.compare_indexed_views(
            ((0, None), (1, 1)),
            ((0, 2), (None, 1)),
        )
        self.assertEqual(pixels.to_dict()["iou"], 0.5)
        self.assertEqual((pixels.missing_pixels, pixels.extra_pixels, pixels.color_mismatches), (1, 1, 0))
        self.assertEqual(metrics.compare_depths((), ()).samples, 0)
        self.assertEqual(metrics.compare_depths((), ()).to_dict()["mae"], None)
        depth = metrics.compare_depths((0.0, 2.0), (1.0, 1.0))
        self.assertEqual((depth.mae, depth.rmse, depth.max_error), (1.0, 1.0, 1.0))
        self.assertEqual(metrics.compare_depths((0.0, 2.0), (1.0, 1.0)).to_dict()["samples"], 2)
        surface = metrics.compare_cell_surfaces(
            ((0, 0, 0), (2, 0, 0)),
            ((1, 0, 0),),
            cell_scale=(2.0, 1.0, 1.0),
        )
        self.assertEqual(surface.reference_to_predicted_max, 2.0)
        self.assertEqual(surface.to_dict()["predicted_count"], 1)
        self.assertIsNone(metrics.compare_cell_surfaces((), ((0, 0, 0),)).reference_to_predicted_mean)
        with self.assertRaises(ValueError):
            metrics.compare_indexed_views((), ())
        with self.assertRaises(ValueError):
            metrics.compare_indexed_views(((0,), (0, 1)), ((0,), (0, 1)))
        with self.assertRaises(ValueError):
            metrics.compare_indexed_views(((0,),), ())
        with self.assertRaises(ValueError):
            metrics.compare_indexed_views(((0,),), ((),))
        with self.assertRaises(ValueError):
            metrics.compare_indexed_views(((0,), (0, 1)), ((0,), (0, 1)))
        with self.assertRaises(ValueError):
            metrics.compare_indexed_views(((0,), (0,)), ((0,), (0, 1)))
        with self.assertRaises(ValueError):
            metrics.compare_indexed_views(((0,),), ((0, 1),))
        with self.assertRaises(ValueError):
            metrics.compare_depths((0.0,), ())
        with self.assertRaises(ValueError):
            metrics.compare_depths((float("inf"),), (0.0,))
        with self.assertRaises(ValueError):
            metrics.compare_cell_surfaces(((0, 0),), ())
        with self.assertRaises(ValueError):
            metrics.compare_cell_surfaces((), (), cell_scale=(0.0, 1.0, 1.0))

    def test_finite_segment_traversal_is_corner_safe_and_endpoint_bounded(self):
        clipped = pick.finite_segment((-1.0, -1.0, -1.0), (3.0, 3.0, 3.0), (0.0, 0.0, 0.0), (3.0, 3.0, 3.0))
        self.assertEqual(clipped, ((0.0, 0.0, 0.0), (3.0, 3.0, 3.0)))
        records = pick.traverse_segment(
            (-1.0, -1.0, -1.0),
            (3.0, 3.0, 3.0),
            (0.0, 0.0, 0.0),
            (3.0, 3.0, 3.0),
        )
        self.assertEqual(tuple(record.cell for record in records), ((0, 0, 0), (1, 1, 1), (2, 2, 2)))
        self.assertEqual(records[1].normal, (-1, -1, -1))
        endpoint_records = pick.traverse_segment(
            (0.1, 0.1, 0.1),
            (1.0, 0.1, 0.1),
            (0.0, 0.0, 0.0),
            (2.0, 1.0, 1.0),
        )
        self.assertEqual(tuple(record.cell for record in endpoint_records), ((0, 0, 0),))
        self.assertEqual(pick.traverse_segment((0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (0, 0, 0), (1, 1, 1)), ())
        self.assertEqual(len(pick.traverse_segment((0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (0, 0, 0), (1, 1, 1), include_endpoint=True)), 1)
        self.assertIsNone(pick.finite_segment((-2, 0, 0), (-1, 0, 0), (0, 0, 0), (1, 1, 1)))
        with self.assertRaises(ValueError):
            pick.finite_segment((0, 0, 0), (1.0e10, 0, 0), (0, 0, 0), (1, 1, 1))
        with self.assertRaises(ValueError):
            pick.finite_segment((0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 1, 1))
        self.assertEqual(
            pick.traverse_segment((-2, 0, 0), (-1, 0, 0), (0, 0, 0), (1, 1, 1)),
            (),
        )
        with self.assertRaises(ValueError):
            pick.finite_segment((-1.0e9, 0, 0), (1.0e9, 0, 0), (0, 0, 0), (1, 1, 1))
        self.assertEqual(
            pick.traverse_segment(
                (1.0e9, 0.5, 0.5),
                (1.0e9 - 1.0, 0.5, 0.5),
                (0, 0, 0),
                (2.0e9, 1, 1),
            ),
            (),
        )

    def test_reconstruction_evidence_and_acceptance_gate_require_sources(self):
        frame = image_intake.ImageFrame.from_rows("review", [[(1.0, 0.0, 0.0, 1.0)]])
        self.assertEqual(frame.source_identity().digest, frame.source_digest)
        indexed = image_intake.index_image(frame, image_intake.PaletteIntakeSettings(background_mode="NONE"))
        source = indexed.source_identity(camera_id="cam")
        view = indexed.to_view(source_identity=source, depth=1)
        result = reconstruct.reconstruct_views((view,), indexed.palette, source_identities=(source,))
        self.assertEqual(result.evidence.get((0, 0, 0)).state, "OCCUPIED")
        self.assertEqual(result.report.silhouette_iou, 1.0)
        self.assertTrue(result.assess().accepted)
        self.assertEqual(result.asset.content_digest, result.assess().asset_digest)
        without_source = reconstruct.reconstruct_views((indexed.to_view(),), indexed.palette)
        self.assertFalse(without_source.assess().accepted)
        self.assertIn("source identities are required", without_source.assess().reasons)
        with self.assertRaises(ValueError):
            reconstruct.AcceptancePolicy(minimum_silhouette_iou=2.0)

class Sprite2DEndToEndTest(unittest.TestCase):
    def test_depth_and_part_observations_round_trip_as_json(self):
        source = provenance.SourceIdentity("depth-json", provenance.sha256_json({"depth": 1}))
        depth = depth_observations.DepthObservation.from_rows(
            "depth",
            [[1, None]],
            back=[[2, None]],
            confidence=[[0.75, None]],
            source_identity=source,
            method="USER",
        )
        parts = depth_observations.PartMask.from_rows(
            "parts",
            [["body", None]],
            source_identity=source,
        )
        with tempfile.TemporaryDirectory() as temporary:
            depth_path = Path(temporary) / "depth.json"
            parts_path = Path(temporary) / "parts.json"
            depth_path.write_text(json.dumps(depth.to_dict()), encoding="utf-8")
            parts_path.write_text(json.dumps(parts.to_dict()), encoding="utf-8")
            self.assertEqual(depth_observations.load_observation(depth_path), depth)
            self.assertEqual(depth_observations.load_part_mask(parts_path), parts)
        self.assertEqual(depth.interval(0, 0, 4), (1, 2))
        self.assertIsNone(depth.interval(1, 0, 4))
        self.assertEqual(parts.parts, ("body",))
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows("bad", [[2]], back=[[1]])
        with self.assertRaises(ValueError):
            depth_observations.PartMask.from_rows("bad", [[""]])

    def test_layered_shell_and_projection_honor_per_pixel_depth(self):
        observation = depth_observations.DepthObservation.from_rows(
            "layers",
            [[1]],
            back=[[2]],
        )
        view = reconstruct.OrthographicView.from_rows(
            "layered",
            [[0]],
            axis="Z",
            depth=3,
            depth_observation=observation,
        )
        self.assertEqual(tuple(view.depth_indices(0, 0)), (1, 2))
        self.assertIsNone(view.project_cell((0, 0, 0)))
        layered = reconstruct.reconstruct_views(
            (view,),
            PALETTE[:1],
            reconstruct.ReconstructionSettings(mode="LAYERED"),
        )
        shell = reconstruct.reconstruct_views(
            (view,),
            PALETTE[:1],
            reconstruct.ReconstructionSettings(mode="SHELL", shell_thickness=1),
        )
        self.assertEqual(set(layered.asset.voxels), {(0, 0, 1), (0, 0, 2)})
        self.assertEqual(set(shell.asset.voxels), {(0, 0, 1)})
        self.assertEqual(layered.render_back(view), ((0,),))
        self.assertEqual(layered.asset.provenance["depth_observation_digests"], observation.digest)

    def test_part_labels_are_persisted_in_voxel_provenance(self):
        source = provenance.SourceIdentity("parts", provenance.sha256_json({"parts": 1}))
        part_mask = depth_observations.PartMask.from_rows("parts", [["arm"]], source_identity=source)
        view = reconstruct.OrthographicView.from_rows(
            "parts-view",
            [[0]],
            source_identity=source,
            part_mask=part_mask,
        )
        result = reconstruct.reconstruct_views((view,), PALETTE[:1])
        self.assertEqual(
            json.loads(result.asset.provenance["part_assignments"]),
            [[[0, 0, 0], "arm"]],
        )
        self.assertEqual(result.asset.part_assignments, {(0, 0, 0): "arm"})
        self.assertEqual(result.source_identities, (source,))
        self.assertEqual(result.asset.provenance["part_mask_digests"], part_mask.digest)

    def test_symmetry_is_missing_only_and_guide_respects_free_evidence(self):
        symmetric = symmetry.apply_missing_only(make_asset({(1, 0, 0): 0}), "X")
        self.assertEqual(symmetric.asset.voxels, {(1, 0, 0): 0, (-1, 0, 0): 0})
        blocked = symmetry.apply_missing_only(
            make_asset({(1, 0, 0): 0}),
            "X",
            forbidden={(-1, 0, 0)},
        )
        self.assertEqual(blocked.added_cells, ())
        self.assertEqual(blocked.skipped_free_cells, ((-1, 0, 0),))
        conflict = symmetry.apply_missing_only(
            make_asset({(1, 0, 0): 0, (-1, 0, 0): 1}),
            "X",
        )
        self.assertEqual(conflict.asset.voxels[(-1, 0, 0)], 1)
        self.assertEqual(conflict.conflicts, ((-1, 0, 0), (1, 0, 0)))

        view = reconstruct.OrthographicView.from_rows("guide-view", [[None, 0]])
        result = reconstruct.reconstruct_views((view,), PALETTE[:1])
        guide = make_asset({(0, 0, 0): 0, (2, 0, 0): 0})
        guided = guide_prior.apply_guide_prior(result, guide)
        self.assertEqual(guided.rejected_free_cells, ((0, 0, 0),))
        self.assertEqual(guided.added_cells, ((2, 0, 0),))

    def test_mesh_guide_voxelization_and_semantic_roles_are_typed(self):
        vertices = (
            (0.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (2.0, 2.0, 0.0),
            (0.0, 2.0, 0.0),
            (0.0, 0.0, 2.0),
            (2.0, 0.0, 2.0),
            (2.0, 2.0, 2.0),
            (0.0, 2.0, 2.0),
        )
        faces = (
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (4, 0, 3, 7),
        )
        mesh = guide_prior.MeshGuide(vertices, faces)
        guide = guide_prior.voxelize_mesh_guide(mesh, grid_size=1.0)
        self.assertEqual(len(guide.voxels), 8)
        self.assertEqual(guide.provenance["guide_source"], "MESH")
        semantic = make_asset({(x, 0, 0): 0 for x in range(5)})
        semantic.set_part((0, 0, 0), "HEAD")
        semantic.set_part((4, 0, 0), "LEFT_ARM")
        skeleton = geometry_skeleton.derive_skeleton(semantic)
        self.assertEqual(skeleton.semantic_source, "PART_ASSIGNMENTS")
        self.assertEqual(skeleton.role_by_name["HEAD"], (0, 0, 0))
        self.assertEqual(skeleton.role_by_name["ARM_L"], (4, 0, 0))

    def test_depth_observation_limits_are_enforced_before_reconstruction(self):
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows(
                "too-deep",
                [[depth_observations.MAX_DEPTH_VALUE + 1]],
            )

    def test_candidate_review_and_skeleton_are_deterministic(self):
        observation = depth_observations.DepthObservation.from_rows("layers", [[0]], back=[[1]])
        view = reconstruct.OrthographicView.from_rows(
            "candidate-view",
            [[0]],
            depth=2,
            depth_observation=observation,
        )
        candidates = sprite_pipeline.generate_candidates((view,), PALETTE[:1])
        self.assertEqual(
            tuple(candidate.name for candidate in candidates),
            ("SURFACE", "LAYERED"),
        )
        self.assertEqual(sprite_pipeline.select_candidate(candidates).name, "LAYERED")
        asset = make_asset({(x, 0, 0): 0 for x in range(5)})
        first = geometry_skeleton.derive_skeleton(asset)
        second = geometry_skeleton.derive_skeleton(asset)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertGreaterEqual(len(first.nodes), 2)
        self.assertIsNone(first.nodes[0].parent)

    def test_sprite_sequence_reports_added_removed_and_recolored_cells(self):
        red = (1.0, 0.0, 0.0, 1.0)
        first_frame = image_intake.ImageFrame.from_rows("first", [[red, (0.0, 0.0, 0.0, 0.0)]])
        second_frame = image_intake.ImageFrame.from_rows("second", [[red, red]])
        intake = image_intake.PaletteIntakeSettings(background_mode="ALPHA")
        shared_palette = image_intake.extract_palette_frames((first_frame, second_frame), intake)
        first = image_intake.index_image(first_frame, intake, palette=shared_palette)
        second = image_intake.index_image(second_frame, intake, palette=shared_palette)
        sequence = sprite_sequence.SpriteSequence(
            (
                sprite_sequence.SequenceFrame("first", first),
                sprite_sequence.SequenceFrame("second", second),
            ),
            name="walk",
        )
        result = sprite_sequence.reconstruct_sequence(sequence, shared_palette)
        self.assertEqual(result.deltas[0].added, ((1, 0, 0),))
        self.assertEqual(result.deltas[0].removed, ())
        self.assertEqual(result.deltas[0].recolored, ())

    def test_sprite_pipeline_wires_depth_parts_guide_and_skeleton(self):
        frame = image_intake.ImageFrame.from_rows(
            "pipeline",
            [[(1.0, 0.0, 0.0, 1.0)]],
        )
        intake = image_intake.PaletteIntakeSettings(background_mode="NONE")
        indexed = image_intake.index_image(frame, intake)
        observation = depth_observations.DepthObservation.from_rows("depth", [[0]], back=[[1]])
        parts = depth_observations.PartMask.from_rows("parts", [["body"]])
        guide = make_asset({(0, 0, 2): 0})
        conversion = sprite_pipeline.convert_sprite(
            indexed,
            settings=sprite_pipeline.SpriteConversionSettings(
                depth=2,
                mode="LAYERED",
                max_skeleton_nodes=16,
            ),
            depth_observation=observation,
            part_mask=parts,
            guide=guide,
        )
        self.assertEqual(set(conversion.reconstruction.asset.voxels), {(0, 0, 0), (0, 0, 1)})
        self.assertEqual(conversion.guide.added_cells, ((0, 0, 2),))
        self.assertTrue(conversion.skeleton.nodes)
        self.assertEqual(len(conversion.candidates), 2)

    def test_sprite_contracts_reject_invalid_sequence_and_limits(self):
        indexed = image_intake.IndexedImage(
            "one",
            ((0,),),
            (PALETTE[0],),
            "0" * 64,
            None,
            image_intake.PaletteIntakeSettings(background_mode="NONE"),
        )
        with self.assertRaises(ValueError):
            sprite_sequence.SpriteSequence(
                (
                    sprite_sequence.SequenceFrame("same", indexed),
                    sprite_sequence.SequenceFrame("same", indexed),
                )
            )
        with self.assertRaises(ValueError):
            geometry_skeleton.derive_skeleton(make_asset(), max_nodes=0)
        with self.assertRaises(ValueError):
            sprite_pipeline.SpriteConversionSettings(mode="INVALID").reconstruction()

    def test_completion_contract_edges(self):
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows("", [[0]])
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows("empty", [])
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows("ragged", [[0], [0, 1]])
        with self.assertRaises(ValueError):
            depth_observations._rows(([None] * (depth_observations.MAX_OBSERVATION_PIXELS + 1),), "large")
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows("bad-confidence", [[0]], confidence=[[2.0]])
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows("bad-back-shape", [[0]], back=[[0, 1]])
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows(
                "bad-back-value",
                [[0]],
                back=[[depth_observations.MAX_DEPTH_VALUE + 1]],
            )
        asset_none = voxel.VoxelAsset(part_assignments=None, voxels={(0, 0, 0): 0}, palette=PALETTE[:1])
        self.assertEqual(asset_none.part_assignments, {})
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows("back-without-front", [[None]], back=[[0]])
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows("confidence-shape", [[0]], confidence=[[0.5, 0.5]])
        with self.assertRaises(TypeError):
            depth_observations.DepthObservation.from_rows("bad-source", [[0]], source_identity=object())
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_rows("bad-method", [[0]], method=" ")
        observation = depth_observations.DepthObservation.from_rows("bounds", [[0]], confidence=[[None]])
        with self.assertRaises(IndexError):
            observation.interval(1, 0, 1)
        with self.assertRaises(IndexError):
            observation.confidence_at(0, 1)
        self.assertIsNone(observation.confidence_at(0, 0))
        with self.assertRaises(ValueError):
            depth_observations.DepthObservation.from_dict({"schema": 2, "front": [[0]]})
        with self.assertRaises(ValueError):
            depth_observations.PartMask.from_rows("", [["body"]])
        with self.assertRaises(ValueError):
            depth_observations.PartMask.from_rows("ragged", [["body"], ["body", None]])
        with self.assertRaises(ValueError):
            depth_observations.PartMask.from_rows("empty-label", [[""]])
        with self.assertRaises(TypeError):
            depth_observations.PartMask.from_rows("bad-source", [["body"]], source_identity=object())
        parts = depth_observations.PartMask.from_rows("bounds", [["body"]])
        with self.assertRaises(IndexError):
            parts.part_at(1, 0)
        with self.assertRaises(ValueError):
            depth_observations.PartMask.from_dict({"schema": 2, "labels": [["body"]]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_json = root / "invalid.json"
            invalid_json.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                depth_observations.load_observation(invalid_json)
            too_large = root / "large.json"
            too_large.write_text("{}", encoding="utf-8")
            previous_limit = depth_observations.MAX_OBSERVATION_BYTES
            depth_observations.MAX_OBSERVATION_BYTES = 0
            try:
                with self.assertRaises(ValueError):
                    depth_observations.load_part_mask(too_large)
            finally:
                depth_observations.MAX_OBSERVATION_BYTES = previous_limit

        asset_none = voxel.VoxelAsset(part_assignments=None, voxels={(0, 0, 0): 0}, palette=PALETTE[:1])
        self.assertEqual(asset_none.part_assignments, {})
        with self.assertRaises(ValueError):
            voxel.VoxelAsset(part_assignments=[object()], voxels={(0, 0, 0): 0}, palette=PALETTE[:1])
        mapped = voxel.VoxelAsset(
            part_assignments=[[[0, 0, 0], "HEAD"]],
            voxels={(0, 0, 0): 0},
            palette=PALETTE[:1],
        )
        self.assertEqual(mapped.part_assignments, {(0, 0, 0): "HEAD"})
        with self.assertRaises(ValueError):
            voxel.VoxelAsset(part_assignments=[[0, 0, 0]], voxels={(0, 0, 0): 0}, palette=PALETTE[:1])
        with self.assertRaises(ValueError):
            voxel.VoxelAsset(part_assignments=[[0, 0, 0, ""]], voxels={(0, 0, 0): 0}, palette=PALETTE[:1])
        with self.assertRaises(ValueError):
            voxel.VoxelAsset(
                voxels={(0, 0, 0): 0},
                palette=PALETTE[:1],
                provenance={"part_assignments": "["},
            )
        with self.assertRaises(ValueError):
            make_asset().set_part((0, 0, 0), " ")

        cube_vertices = (
            (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 2.0, 0.0), (0.0, 2.0, 0.0),
            (0.0, 0.0, 2.0), (2.0, 0.0, 2.0), (2.0, 2.0, 2.0), (0.0, 2.0, 2.0),
        )
        cube_faces = (
            (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
            (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7),
        )
        with self.assertRaises(ValueError):
            guide_prior.MeshGuide((), cube_faces)
        with self.assertRaises(ValueError):
            guide_prior.MeshGuide(((float("nan"), 0.0, 0.0),), ((0, 0, 0),))
        with self.assertRaises(ValueError):
            guide_prior.MeshGuide(cube_vertices, ((0, 1),))
        with self.assertRaises(ValueError):
            guide_prior.MeshGuide(cube_vertices, ((0, 1, "2"),))
        with self.assertRaises(ValueError):
            guide_prior.MeshGuide(cube_vertices, ((0, 1, 99),))
        with self.assertRaises(ValueError):
            guide_prior.MeshGuide(cube_vertices, ())
        with self.assertRaises(ValueError):
            guide_prior.MeshGuide(cube_vertices, cube_faces, material_indices=(0,))
        with self.assertRaises(ValueError):
            guide_prior.MeshGuide(cube_vertices, cube_faces, palette=PALETTE[:1], material_indices=(1,) * 6)
        mesh = guide_prior.MeshGuide(cube_vertices, cube_faces)
        with self.assertRaises(TypeError):
            guide_prior.voxelize_mesh_guide(object())
        with self.assertRaises(ValueError):
            guide_prior.voxelize_mesh_guide(mesh, grid_size=object())
        with self.assertRaises(ValueError):
            guide_prior.voxelize_mesh_guide(mesh, grid_size=0)
        with self.assertRaises(ValueError):
            guide_prior.voxelize_mesh_guide(mesh, max_cells=0)
        with self.assertRaises(ValueError):
            guide_prior.voxelize_mesh_guide(mesh, max_cells=1)
        open_mesh = guide_prior.MeshGuide(
            ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 1.0, 1.0)),
            ((0, 1, 2),),
        )
        with self.assertRaises(ValueError):
            guide_prior.voxelize_mesh_guide(open_mesh)

        view = reconstruct.OrthographicView.from_rows("edge-guide", [[0]])
        result = reconstruct.reconstruct_views((view,), PALETTE[:1])
        with self.assertRaises(TypeError):
            guide_prior.apply_guide_prior(object(), make_asset())
        with self.assertRaises(TypeError):
            guide_prior.apply_guide_prior(result, object())
        with self.assertRaises(ValueError):
            guide_prior.apply_guide_prior(result, make_asset(), mode="INVALID")
        hidden_guide = make_asset({(1, 0, 0): 0})
        observed = guide_prior.apply_guide_prior(result, hidden_guide, mode="OBSERVED_ONLY")
        self.assertEqual(observed.added_cells, ())
        recolor_guide = make_asset({(0, 0, 0): 1}, palette_values=PALETTE)
        recolor_guide.set_part((0, 0, 0), "HEAD")
        recolored = guide_prior.apply_guide_prior(result, recolor_guide)
        self.assertEqual(recolored.recolored_cells, ((0, 0, 0),))

        with self.assertRaises(ValueError):
            sprite_pipeline.generate_candidates((), PALETTE)
        with self.assertRaises(ValueError):
            sprite_pipeline.select_candidate(())
        self.assertTrue(sprite_pipeline.generate_candidates((view,), PALETTE[:1]))
        self.assertEqual(sprite_pipeline.generate_candidates((view,), PALETTE[:1])[0].to_dict()["name"], "SURFACE")
        with self.assertRaises(TypeError):
            sprite_pipeline.convert_sprite(object())
        indexed = image_intake.index_image(
            image_intake.ImageFrame.from_rows("edge", [[PALETTE[0]]]),
            image_intake.PaletteIntakeSettings(background_mode="NONE"),
        )
        with self.assertRaises(TypeError):
            sprite_pipeline.convert_sprite(indexed, guide=object())
        mesh_conversion = sprite_pipeline.convert_sprite(
            indexed,
            settings=sprite_pipeline.SpriteConversionSettings(
                depth=1,
                symmetry_axis="X",
                guide_grid_size=1.0,
            ),
            guide=mesh,
        )
        self.assertIsNotNone(mesh_conversion.guide)
        solid_skeleton = geometry_skeleton.derive_skeleton(voxel.VoxelAsset.box(3))
        self.assertTrue(solid_skeleton.nodes)
        self.assertTrue(solid_skeleton.to_dict()["nodes"])
        self.assertEqual(geometry_skeleton.derive_skeleton(voxel.VoxelAsset()).nodes, ())
        self.assertEqual(
            len(geometry_skeleton.derive_skeleton(voxel.VoxelAsset.box(3), max_nodes=1).nodes),
            1,
        )
        with self.assertRaises(TypeError):
            geometry_skeleton.derive_skeleton(object())
        self.assertEqual(geometry_skeleton._semantic_roles(voxel.VoxelAsset.box(1), set()), ())
        empty_role_asset = voxel.VoxelAsset.box(1)
        empty_role_asset.set_part((0, 0, 0), "HEAD")
        empty_role_graph = geometry_skeleton.derive_skeleton(empty_role_asset)
        self.assertEqual(empty_role_graph.to_dict()["semantic_roles"], [{"role": "HEAD", "cell": [0, 0, 0]}])
        self.assertEqual(geometry_skeleton._semantic_roles(empty_role_asset, set()), ())

        with self.assertRaises(ValueError):
            sprite_sequence.SequenceFrame("", indexed)
        with self.assertRaises(TypeError):
            sprite_sequence.SequenceFrame("bad-image", object())
        frame = sprite_sequence.SequenceFrame("frame", indexed)
        with self.assertRaises(ValueError):
            frame.to_view(palette=PALETTE[1:])
        with self.assertRaises(ValueError):
            sprite_sequence.SpriteSequence(())
        self.assertEqual(
            sprite_sequence.PoseDelta("frame", ((0, 0, 0),), (), ()).to_dict()["added"],
            [[0, 0, 0]],
        )

        with self.assertRaises(ValueError):
            symmetry.mirror_cell((0, 0, 0), "Q")
        with self.assertRaises(ValueError):
            symmetry.mirror_cell((0, 0, 0), "X", plane=True)
        with self.assertRaises(TypeError):
            symmetry.apply_missing_only(object(), "X")
        labeled = make_asset({(1, 0, 0): 0})
        labeled.set_part((1, 0, 0), "ARM_L")
        mirrored = symmetry.apply_missing_only(labeled, "X")
        self.assertEqual(mirrored.asset.part_assignments[(-1, 0, 0)], "ARM_L")
        self.assertEqual(
            symmetry.apply_missing_only(make_asset({(0, 0, 0): 0}), "X").added_cells,
            (),
        )
        evidence = reconstruct.reconstruct_views(
            (
                reconstruct.OrthographicView.from_rows(
                    "free",
                    [[None]],
                    origin=(-1, 0, 0),
                ),
            ),
            PALETTE[:1],
        ).evidence
        blocked_by_evidence = symmetry.apply_missing_only(
            make_asset({(1, 0, 0): 0}),
            "X",
            evidence=evidence,
        )
        self.assertEqual(blocked_by_evidence.skipped_free_cells, ((-1, 0, 0),))

if __name__ == "__main__":
    program = unittest.main(exit=False)
    if program.result.testsRun != EXPECTED_TESTS:
        raise SystemExit(
            f"VOXTART_CORE_TEST_COUNT {program.result.testsRun} != {EXPECTED_TESTS}"
        )
    raise SystemExit(not program.result.wasSuccessful())
