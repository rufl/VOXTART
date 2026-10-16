# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender operators and sidebar controls for voxel mesh optimization."""


import json

import bpy
from bpy.props import BoolProperty, EnumProperty

from . import operators
from .optimizer import OPTIMIZER_BACKENDS, OptimizationSettings, optimize_asset


OPTIMIZER_REPORT_PROPERTY = "voxtart_optimizer_report"
_BACKEND_ITEMS = (
    ("greedy", "Greedy", "Merge adjacent same-material voxel faces into quads"),
    ("marching", "Marching Cubes", "Generate a smooth triangle surface from the voxel field"),
)


def _active_asset(context: bpy.types.Context):
    obj = context.active_object
    if obj is None or obj.type != "MESH" or operators.ASSET_PROPERTY not in obj:
        return None
    return obj


def _palette_indices_for_result(asset, report):
    if report.material_remap:
        return tuple(report.material_remap)
    return tuple(range(len(asset.palette)))


def _replace_mesh(obj: bpy.types.Object, asset, mesh_data, report) -> None:
    old_mesh = obj.data
    mesh = bpy.data.meshes.new(f"{obj.name} Optimized Mesh")
    mesh.from_pydata(mesh_data.vertices, [], mesh_data.faces)
    mesh.update()
    for polygon, material_index in zip(mesh.polygons, mesh_data.materials):
        polygon.material_index = int(material_index)
    obj.data = mesh

    palette_indices = _palette_indices_for_result(asset, report)
    material_specs = asset.get_palette_materials()
    for slot_index, palette_index in enumerate(palette_indices):
        material = bpy.data.materials.get(f"VOXTART {obj.name} Palette {palette_index}")
        if material is None:
            material = bpy.data.materials.new(f"VOXTART {obj.name} Palette {palette_index}")
        operators._configure_material(
            material,
            asset.palette[palette_index],
            material_specs[palette_index],
        )
        obj.data.materials.append(material)

    obj[operators.MESH_BACKEND_PROPERTY] = report.backend
    obj[OPTIMIZER_REPORT_PROPERTY] = json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"))
    operators._update_visual_overlays(obj, asset)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)


def _report_from_object(obj):
    if obj is None:
        return None
    encoded = obj.get(OPTIMIZER_REPORT_PROPERTY)
    if encoded is None:
        return None
    if not isinstance(encoded, str):
        raise ValueError("saved optimization report is not text")
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"saved optimization report is invalid: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("saved optimization report must be an object")
    return value


class VOXTART_OT_optimize_mesh(bpy.types.Operator):
    bl_idname = "voxtart.optimize_mesh"
    bl_label = "Apply Optimized Mesh"
    bl_description = "Generate a compact game-ready mesh from the active VOXTART voxel asset"
    bl_options = {"REGISTER", "UNDO"}

    backend: EnumProperty(name="Backend", items=_BACKEND_ITEMS, default="greedy")
    weld_vertices: BoolProperty(name="Weld Vertices", default=True)
    remove_degenerate: BoolProperty(name="Remove Degenerate", default=True)
    remove_duplicate: BoolProperty(name="Remove Duplicate", default=True)
    triangulate: BoolProperty(name="Triangulate", default=False)
    compact_materials: BoolProperty(name="Compact Materials", default=True)

    @classmethod
    def poll(cls, context):
        return _active_asset(context) is not None

    def execute(self, context):
        obj = _active_asset(context)
        if obj is None:
            self.report({"ERROR"}, "select a VOXTART voxel asset first")
            return {"CANCELLED"}
        try:
            asset = operators._load_asset(obj)
            settings = OptimizationSettings(
                backend=self.backend,
                weld_vertices=self.weld_vertices,
                remove_degenerate=self.remove_degenerate,
                remove_duplicate=self.remove_duplicate,
                triangulate=self.triangulate,
                compact_materials=self.compact_materials,
            )
            mesh_data, report = optimize_asset(asset, settings)
            _replace_mesh(obj, asset, mesh_data, report)
        except (TypeError, ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Optimized {report.source_vertices}->{report.target_vertices} vertices, "
            f"{report.source_faces}->{report.target_faces} faces",
        )
        return {"FINISHED"}


class VOXTART_PT_optimizer(bpy.types.Panel):
    bl_label = "Voxel Optimizer"
    bl_idname = "VOXTART_PT_optimizer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 19

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "voxtart_optimizer_backend", text="Mesher")
        box = layout.box()
        box.label(text="Cleanup", icon="MODIFIER")
        box.prop(scene, "voxtart_optimizer_weld_vertices")
        box.prop(scene, "voxtart_optimizer_remove_degenerate")
        box.prop(scene, "voxtart_optimizer_remove_duplicate")
        box.prop(scene, "voxtart_optimizer_triangulate")
        box.prop(scene, "voxtart_optimizer_compact_materials")
        operator = layout.operator(VOXTART_OT_optimize_mesh.bl_idname, icon="MOD_DECIM")
        operator.backend = scene.voxtart_optimizer_backend
        operator.weld_vertices = scene.voxtart_optimizer_weld_vertices
        operator.remove_degenerate = scene.voxtart_optimizer_remove_degenerate
        operator.remove_duplicate = scene.voxtart_optimizer_remove_duplicate
        operator.triangulate = scene.voxtart_optimizer_triangulate
        operator.compact_materials = scene.voxtart_optimizer_compact_materials

        obj = _active_asset(context)
        try:
            report = _report_from_object(obj) if obj is not None else None
        except ValueError as error:
            layout.label(text=str(error), icon="ERROR")
            return
        if report is None:
            layout.label(text="No optimization report yet", icon="INFO")
            return
        report_box = layout.box()
        report_box.label(text="Last Result", icon="CHECKMARK")
        report_box.label(text=f"Vertices: {report.get('source_vertices', 0)} -> {report.get('target_vertices', 0)}")
        report_box.label(text=f"Faces: {report.get('source_faces', 0)} -> {report.get('target_faces', 0)}")
        report_box.label(text=f"Materials: {report.get('materials_before', 0)} -> {report.get('materials_after', 0)}")
        report_box.label(text=f"Vertex reduction: {float(report.get('vertex_reduction_ratio', 0.0)):.1%}")
        report_box.label(text=f"Face reduction: {float(report.get('face_reduction_ratio', 0.0)):.1%}")


_CLASSES = (
    VOXTART_OT_optimize_mesh,
    VOXTART_PT_optimizer,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_optimizer_backend = EnumProperty(
        name="Optimizer Mesher",
        items=_BACKEND_ITEMS,
        default=OPTIMIZER_BACKENDS[0],
    )
    bpy.types.Scene.voxtart_optimizer_weld_vertices = BoolProperty(
        name="Weld Vertices",
        description="Share equal vertex positions after meshing",
        default=True,
    )
    bpy.types.Scene.voxtart_optimizer_remove_degenerate = BoolProperty(
        name="Remove Degenerate",
        description="Drop faces with fewer than three unique vertices",
        default=True,
    )
    bpy.types.Scene.voxtart_optimizer_remove_duplicate = BoolProperty(
        name="Remove Duplicate",
        description="Drop duplicate faces with the same material",
        default=True,
    )
    bpy.types.Scene.voxtart_optimizer_triangulate = BoolProperty(
        name="Triangulate",
        description="Split quads into triangles for downstream tools",
        default=False,
    )
    bpy.types.Scene.voxtart_optimizer_compact_materials = BoolProperty(
        name="Compact Materials",
        description="Keep only palette entries used by the optimized mesh",
        default=True,
    )


def unregister() -> None:
    for name in (
        "voxtart_optimizer_compact_materials",
        "voxtart_optimizer_triangulate",
        "voxtart_optimizer_remove_duplicate",
        "voxtart_optimizer_remove_degenerate",
        "voxtart_optimizer_weld_vertices",
        "voxtart_optimizer_backend",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = ["VOXTART_OT_optimize_mesh", "VOXTART_PT_optimizer", "register", "unregister"]
