# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Qubicle-style optimized voxel mesh export for VOXTART assets."""


import json
from pathlib import Path
from typing import Any

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import operators
from .mesh_optimize import MeshOptimizationReport, optimize_mesh_data
from .meshing import scaled_mesh
from .optimize import DecimationPolicy, decimate


_FORMAT_ITEMS = (
    ("AUTO", "Auto", "Infer the exporter from the chosen file extension"),
    ("OBJ", "Wavefront OBJ", "Export an optimized material-aware OBJ mesh"),
    ("GLB", "glTF Binary", "Export a self-contained glTF binary"),
    ("GLTF", "glTF", "Export a glTF scene"),
    ("FBX", "FBX", "Export an FBX mesh"),
    ("STL", "STL", "Export a triangle STL mesh"),
)
_BACKEND_ITEMS = (
    ("GREEDY", "Greedy Blocks", "Cull hidden faces and merge coplanar same-material voxel faces"),
    ("MARCHING", "Marching Surface", "Export a smooth triangular marching surface"),
)
_THRESHOLD_ITEMS = (
    ("majority", "Majority", "Keep coarse blocks with at least half their source voxels"),
    ("any", "Any", "Keep coarse blocks when any source voxel is occupied"),
    ("full", "Full", "Keep coarse blocks only when every source voxel is occupied"),
)


def _asset_objects(context: bpy.types.Context, export_selected: bool) -> tuple[bpy.types.Object, ...]:
    active = context.active_object
    if export_selected:
        objects = tuple(obj for obj in context.selected_objects if operators.ASSET_PROPERTY in obj)
        if not objects and active is not None and operators.ASSET_PROPERTY in active:
            objects = (active,)
    else:
        objects = (active,) if active is not None and operators.ASSET_PROPERTY in active else ()
    return tuple(sorted(objects, key=lambda obj: obj.name.casefold()))


def _format_for_path(filepath: str, requested: str) -> str:
    requested = str(requested or "AUTO").upper()
    if requested != "AUTO":
        return requested
    extension = Path(filepath).suffix.lower()
    mapping = {
        ".obj": "OBJ",
        ".glb": "GLB",
        ".gltf": "GLTF",
        ".fbx": "FBX",
        ".stl": "STL",
    }
    try:
        return mapping[extension]
    except KeyError as error:
        raise ValueError("choose an OBJ, GLB, GLTF, FBX, or STL export filename") from error


def _mesh_from_data(name: str, mesh_data, asset, material_remap: tuple[int, ...]) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(mesh_data.vertices, [], mesh_data.faces)
    mesh.update()
    palette_indices = material_remap or tuple(range(len(asset.palette)))
    material_specs = asset.get_palette_materials()
    for slot_index, palette_index in enumerate(palette_indices):
        material = bpy.data.materials.get(f"VOXTART Export {name} Palette {palette_index}")
        if material is None:
            material = bpy.data.materials.new(f"VOXTART Export {name} Palette {palette_index}")
        operators._configure_material(
            material,
            asset.palette[palette_index],
            material_specs[palette_index],
        )
        mesh.materials.append(material)
    for polygon, palette_index in zip(mesh.polygons, mesh_data.materials):
        if 0 <= palette_index < len(mesh.materials):
            polygon.material_index = palette_index
    return mesh


def _export_blender_file(filepath: str, format_name: str) -> None:
    if format_name == "OBJ":
        exporter = getattr(bpy.ops.wm, "obj_export", None)
        if exporter is None:
            raise RuntimeError("Blender OBJ exporter is unavailable")
        try:
            exporter(filepath=filepath, export_selected_objects=True, export_materials=True)
        except TypeError:
            exporter(filepath=filepath, export_selected_objects=True)
        return
    if format_name in {"GLB", "GLTF"}:
        exporter = getattr(getattr(bpy.ops, "export_scene", None), "gltf", None)
        if exporter is None:
            raise RuntimeError("Blender glTF exporter is unavailable")
        exporter(
            filepath=filepath,
            export_format="GLB" if format_name == "GLB" else "GLTF_SEPARATE",
            use_selection=True,
        )
        return
    if format_name == "FBX":
        exporter = getattr(getattr(bpy.ops, "export_scene", None), "fbx", None)
        if exporter is None:
            raise RuntimeError("Blender FBX exporter is unavailable")
        exporter(filepath=filepath, use_selection=True)
        return
    if format_name == "STL":
        exporter = getattr(bpy.ops.wm, "stl_export", None)
        if exporter is not None:
            try:
                exporter(filepath=filepath, export_selected_objects=True)
            except TypeError:
                exporter(filepath=filepath, ascii_format=False, export_selected_objects=True)
            return
        exporter = getattr(getattr(bpy.ops, "export_mesh", None), "stl", None)
        if exporter is None:
            raise RuntimeError("Blender STL exporter is unavailable")
        exporter(filepath=filepath, use_selection=True)
        return
    raise ValueError(f"unsupported mesh export format: {format_name!r}")


def _restore_selection(context, selected_before, active_before) -> None:
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except (AttributeError, RuntimeError):
        pass
    for obj in selected_before:
        try:
            obj.select_set(True)
        except (AttributeError, ReferenceError, RuntimeError):
            pass
    try:
        context.view_layer.objects.active = active_before
    except (AttributeError, ReferenceError, RuntimeError):
        pass


class VOXTART_OT_export_optimized_mesh(bpy.types.Operator, ExportHelper):
    bl_idname = "voxtart.export_optimized_mesh"
    bl_label = "Export Optimized Mesh"
    bl_description = "Export selected voxel assets with hidden-face culling, greedy merging, and mesh cleanup"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".obj"
    check_extension = False
    filter_glob: StringProperty(default="*.obj;*.glb;*.gltf;*.fbx;*.stl", options={"HIDDEN"})

    format: EnumProperty(name="Format", items=_FORMAT_ITEMS, default="AUTO")
    backend: EnumProperty(name="Surface", items=_BACKEND_ITEMS, default="GREEDY")
    export_selected: BoolProperty(name="Export Selected Blobs", default=True)
    decimation_factor: IntProperty(name="Voxel Reduction", min=1, max=16, default=1)
    threshold: EnumProperty(name="Reduction Threshold", items=_THRESHOLD_ITEMS, default="majority")
    preserve_protected: BoolProperty(name="Preserve Protected Voxels", default=True)
    weld_vertices: BoolProperty(name="Weld Vertices", default=True)
    remove_degenerate: BoolProperty(name="Remove Degenerate Faces", default=True)
    remove_duplicate: BoolProperty(name="Remove Duplicate Faces", default=True)
    triangulate: BoolProperty(name="Triangulate", default=False)
    compact_materials: BoolProperty(name="Compact Materials", default=True)

    @classmethod
    def poll(cls, context):
        return bool(_asset_objects(context, True)) or (
            context.active_object is not None and operators.ASSET_PROPERTY in context.active_object
        )

    def execute(self, context):
        objects = _asset_objects(context, self.export_selected)
        if not objects:
            self.report({"ERROR"}, "select at least one VOXTART asset")
            return {"CANCELLED"}
        try:
            format_name = _format_for_path(self.filepath, self.format)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        selected_before = tuple(context.selected_objects)
        active_before = context.view_layer.objects.active
        temporary_objects: list[bpy.types.Object] = []
        aggregate = {
            "source_vertices": 0,
            "target_vertices": 0,
            "source_faces": 0,
            "target_faces": 0,
            "source_voxels": 0,
            "target_voxels": 0,
            "removed_degenerate_faces": 0,
            "removed_duplicate_faces": 0,
            "triangulated_faces": 0,
            "objects": len(objects),
        }
        try:
            collection = context.collection or context.scene.collection
            for source_obj in objects:
                asset = operators._load_asset(source_obj)
                aggregate["source_voxels"] += len(asset.voxels)
                if self.decimation_factor > 1:
                    policy = DecimationPolicy(
                        factor=self.decimation_factor,
                        threshold=self.threshold,
                        protected=(
                            operators._protected_cells(source_obj)
                            if self.preserve_protected
                            else frozenset()
                        ),
                    )
                    asset, reduction_report = decimate(asset, policy)
                    aggregate["target_voxels"] += len(asset.voxels)
                else:
                    aggregate["target_voxels"] += len(asset.voxels)
                raw_mesh = scaled_mesh(asset, backend=self.backend.lower())
                optimized_mesh, report = optimize_mesh_data(
                    raw_mesh,
                    weld_vertices=self.weld_vertices,
                    remove_degenerate=self.remove_degenerate,
                    remove_duplicate=self.remove_duplicate,
                    triangulate=self.triangulate,
                    compact_materials=self.compact_materials,
                )
                for key in (
                    "source_vertices",
                    "target_vertices",
                    "source_faces",
                    "target_faces",
                    "removed_degenerate_faces",
                    "removed_duplicate_faces",
                    "triangulated_faces",
                ):
                    aggregate[key] += getattr(report, key)
                mesh = _mesh_from_data(
                    f"{source_obj.name} Export Mesh",
                    optimized_mesh,
                    asset,
                    report.material_remap,
                )
                export_obj = bpy.data.objects.new(f"{source_obj.name} Export", mesh)
                collection.objects.link(export_obj)
                export_obj.matrix_world = source_obj.matrix_world.copy()
                temporary_objects.append(export_obj)

            bpy.ops.object.select_all(action="DESELECT")
            for export_obj in temporary_objects:
                export_obj.select_set(True)
            context.view_layer.objects.active = temporary_objects[0]
            _export_blender_file(self.filepath, format_name)
            for source_obj in objects:
                source_obj["voxtart_last_mesh_export"] = json.dumps(
                    {**aggregate, "format": format_name, "backend": self.backend},
                    sort_keys=True,
                )
        except (AttributeError, OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        finally:
            _restore_selection(context, selected_before, active_before)
            for export_obj in temporary_objects:
                mesh = getattr(export_obj, "data", None)
                try:
                    bpy.data.objects.remove(export_obj, do_unlink=True)
                except (AttributeError, ReferenceError, RuntimeError):
                    pass
                if mesh is not None and getattr(mesh, "users", 0) == 0:
                    try:
                        bpy.data.meshes.remove(mesh)
                    except (AttributeError, ReferenceError, RuntimeError):
                        pass

        self.report(
            {"INFO"},
            f"exported {aggregate['objects']} object(s): "
            f"{aggregate['source_faces']}→{aggregate['target_faces']} faces, "
            f"{aggregate['source_vertices']}→{aggregate['target_vertices']} vertices",
        )
        return {"FINISHED"}


class VOXTART_PT_export_optimization(bpy.types.Panel):
    bl_label = "Optimized Mesh Export"
    bl_idname = "VOXTART_PT_export_optimization"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 30

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        if not (
            context.active_object is not None
            and operators.ASSET_PROPERTY in context.active_object
        ):
            layout.label(text="Select a VOXTART asset to export.", icon="INFO")
            return
        layout.label(text="Qubicle-style cleanup: hidden faces + greedy merges.", icon="INFO")
        layout.prop(scene, "voxtart_export_format", text="Format")
        layout.prop(scene, "voxtart_export_backend", text="Surface")
        layout.prop(scene, "voxtart_export_selected", text="Selected Blobs")
        layout.prop(scene, "voxtart_export_decimation_factor", text="Voxel Reduction")
        if scene.voxtart_export_decimation_factor > 1:
            layout.prop(scene, "voxtart_export_threshold", text="Threshold")
            layout.prop(scene, "voxtart_export_preserve_protected", text="Protected")
        box = layout.box()
        box.label(text="Mesh cleanup")
        box.prop(scene, "voxtart_export_weld_vertices", text="Weld Vertices")
        box.prop(scene, "voxtart_export_remove_degenerate", text="Remove Degenerate")
        box.prop(scene, "voxtart_export_remove_duplicate", text="Remove Duplicate")
        box.prop(scene, "voxtart_export_compact_materials", text="Compact Materials")
        box.prop(scene, "voxtart_export_triangulate", text="Triangulate")
        export = layout.operator(VOXTART_OT_export_optimized_mesh.bl_idname, text="Export Optimized Mesh", icon="EXPORT")
        export.format = scene.voxtart_export_format
        export.backend = scene.voxtart_export_backend
        export.export_selected = scene.voxtart_export_selected
        export.decimation_factor = scene.voxtart_export_decimation_factor
        export.threshold = scene.voxtart_export_threshold
        export.preserve_protected = scene.voxtart_export_preserve_protected
        export.weld_vertices = scene.voxtart_export_weld_vertices
        export.remove_degenerate = scene.voxtart_export_remove_degenerate
        export.remove_duplicate = scene.voxtart_export_remove_duplicate
        export.compact_materials = scene.voxtart_export_compact_materials
        export.triangulate = scene.voxtart_export_triangulate


_CLASSES = (
    VOXTART_OT_export_optimized_mesh,
    VOXTART_PT_export_optimization,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_export_format = EnumProperty(
        name="Export Format", items=_FORMAT_ITEMS, default="AUTO"
    )
    bpy.types.Scene.voxtart_export_backend = EnumProperty(
        name="Export Surface", items=_BACKEND_ITEMS, default="GREEDY"
    )
    bpy.types.Scene.voxtart_export_selected = BoolProperty(
        name="Export Selected Blobs", default=True
    )
    bpy.types.Scene.voxtart_export_decimation_factor = IntProperty(
        name="Voxel Reduction", min=1, max=16, default=1
    )
    bpy.types.Scene.voxtart_export_threshold = EnumProperty(
        name="Reduction Threshold", items=_THRESHOLD_ITEMS, default="majority"
    )
    bpy.types.Scene.voxtart_export_preserve_protected = BoolProperty(
        name="Preserve Protected Voxels", default=True
    )
    bpy.types.Scene.voxtart_export_weld_vertices = BoolProperty(
        name="Weld Vertices", default=True
    )
    bpy.types.Scene.voxtart_export_remove_degenerate = BoolProperty(
        name="Remove Degenerate Faces", default=True
    )
    bpy.types.Scene.voxtart_export_remove_duplicate = BoolProperty(
        name="Remove Duplicate Faces", default=True
    )
    bpy.types.Scene.voxtart_export_compact_materials = BoolProperty(
        name="Compact Materials", default=True
    )
    bpy.types.Scene.voxtart_export_triangulate = BoolProperty(
        name="Triangulate", default=False
    )


def unregister() -> None:
    for name in (
        "voxtart_export_triangulate",
        "voxtart_export_compact_materials",
        "voxtart_export_remove_duplicate",
        "voxtart_export_remove_degenerate",
        "voxtart_export_weld_vertices",
        "voxtart_export_preserve_protected",
        "voxtart_export_threshold",
        "voxtart_export_decimation_factor",
        "voxtart_export_selected",
        "voxtart_export_backend",
        "voxtart_export_format",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
