# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender import, rendering, and controls for layered sprite stacks."""


import json
import tempfile
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ElementTree


import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import operators
from .layers import LayerStack
from .stacking import (
    STACK_AXIS_ITEMS,
    STACK_ORDER_ITEMS,
    STACK_PROPERTY,
    STACK_SOURCE_PROPERTY,
    StackDocument,
    StackLayer,
    decode_openraster,
    decode_psd,
    decode_stack_manifest,
    stack_to_voxel_asset,
)
from .voxel_formats import decode_magica_vox_models, decode_qb_models


_IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tga",
    ".tif",
    ".tiff",
    ".webp",
}
_MANIFEST_EXTENSIONS = {".json", ".sstack", ".spritestack", ".stack"}


def _image_from_pillow(data: bytes, name: str) -> tuple[StackLayer, ...] | None:
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        return None
    try:
        with Image.open(__import__("io").BytesIO(data)) as image:
            layers = []
            for index, frame in enumerate(ImageSequence.Iterator(image)):
                rgba = frame.convert("RGBA")
                pixels = tuple(
                    tuple(channel / 255.0 for channel in pixel)
                    for pixel in rgba.getdata()
                )
                layers.append(StackLayer(
                    name=f"{Path(name).stem or 'Layer'} {index + 1:03d}" if getattr(image, "n_frames", 1) > 1 else Path(name).stem or "Layer",
                    width=rgba.width,
                    height=rgba.height,
                    pixels=pixels,
                ))
            return tuple(layers)
    except (OSError, ValueError, TypeError):
        return None


def _image_from_blender(data: bytes, name: str) -> StackLayer:
    suffix = Path(name).suffix or ".png"
    temporary_path = None
    image = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        image = bpy.data.images.load(str(temporary_path), check_existing=False)
        width, height = (int(image.size[0]), int(image.size[1]))
        pixels = tuple(float(value) for value in image.pixels[:])
        return StackLayer(
            name=Path(name).stem or "Layer",
            width=width,
            height=height,
            pixels=tuple(
                tuple(pixels[index:index + 4])  # type: ignore[misc]
                for index in range(0, width * height * 4, 4)
            ),
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise ValueError(f"unable to decode image layer {name}: {error}") from error
    finally:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except (AttributeError, RuntimeError, TypeError):
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _image_layers_from_bytes(data: bytes, name: str) -> tuple[StackLayer, ...]:
    layers = _image_from_pillow(data, name)
    if layers is not None:
        return layers
    return (_image_from_blender(data, name),)


def _load_layer_reference(reference: str, payload: bytes | None = None) -> StackLayer:
    path = Path(reference).expanduser()
    data = payload if payload is not None else path.read_bytes()
    layers = _image_layers_from_bytes(data, path.name)
    if len(layers) != 1:
        raise ValueError(f"{path.name} contains multiple frames; use it as a direct stack source")
    return layers[0]


def _load_image_document(path: Path) -> StackDocument:
    layers = _image_layers_from_bytes(path.read_bytes(), path.name)
    return StackDocument.from_layers(
        layers,
        source_format="image" if len(layers) == 1 else "animated-image",
        source_name=str(path),
    )


def _load_openraster(path: Path) -> StackDocument:
    return decode_openraster(
        path.read_bytes(),
        _load_layer_reference,
        source_name=str(path),
    )


def _load_manifest(path: Path) -> StackDocument:
    return decode_stack_manifest(
        path.read_bytes(),
        _load_layer_reference,
        base_path=path.parent,
        source_name=str(path),
    )


def _load_krita(path: Path) -> StackDocument:
    """Load the common PNG-layer subset of Krita archives."""

    try:
        archive = ZipFile(path)
    except (BadZipFile, OSError) as error:
        raise ValueError("invalid Krita archive") from error
    with archive:
        try:
            document = archive.read("maindoc.xml")
        except KeyError as error:
            raise ValueError("Krita file is missing maindoc.xml") from error
        try:
            root = ElementTree.fromstring(document)
        except (ElementTree.ParseError, ValueError) as error:
            raise ValueError("invalid Krita layer manifest") from error
        canvas = root
        width = int(canvas.attrib.get("width", canvas.attrib.get("w", 0)))
        height = int(canvas.attrib.get("height", canvas.attrib.get("h", 0)))
        if width <= 0 or height <= 0:
            image = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "image"), None)
            width = int(getattr(image, "attrib", {}).get("width", 0))
            height = int(getattr(image, "attrib", {}).get("height", 0))
        if width <= 0 or height <= 0:
            raise ValueError("Krita document has no canvas dimensions")
        values = []
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag not in {"layer", "shapelayer"}:
                continue
            filename = node.attrib.get("filename", node.attrib.get("src", ""))
            if not filename or filename not in archive.namelist():
                continue
            loaded = _load_layer_reference(filename, archive.read(filename))
            values.append(StackLayer(
                name=node.attrib.get("name", loaded.name),
                width=loaded.width,
                height=loaded.height,
                pixels=loaded.pixels,
                offset=(int(node.attrib.get("x", 0)), int(node.attrib.get("y", 0))),
                visible=node.attrib.get("visible", "1") not in {"0", "false", "False"},
                opacity=float(node.attrib.get("opacity", 1.0)),
            ))
    if not values:
        raise ValueError("Krita file contains no PNG-backed layers")
    return StackDocument.from_layers(values, canvas_size=(width, height), source_format="krita", source_name=str(path))


def _load_document(path: Path) -> StackDocument:
    suffix = path.suffix.lower()
    if suffix == ".psd":
        return decode_psd(path.read_bytes(), source_name=str(path))
    if suffix == ".ora":
        return _load_openraster(path)
    if suffix == ".kra":
        return _load_krita(path)
    if suffix in _MANIFEST_EXTENSIONS:
        return _load_manifest(path)
    if suffix in _IMAGE_EXTENSIONS:
        return _load_image_document(path)
    if path.is_dir():
        paths = sorted(
            item for item in path.iterdir()
            if item.is_file() and item.suffix.lower() in _IMAGE_EXTENSIONS
        )
        if not paths:
            raise ValueError("stack directory contains no supported image files")
        return StackDocument.from_layers(
            tuple(_load_layer_reference(str(item)) for item in paths),
            source_format="image-sequence",
            source_name=str(path),
        )
    raise ValueError("choose a PSD, ORA, KRA, image, slice manifest, or SpriteStack VOX file")




def _scene_options(scene) -> tuple[str, str, float, float, int]:
    return (
        str(getattr(scene, "voxtart_stack_axis", "Z")),
        str(getattr(scene, "voxtart_stack_order", "BOTTOM_TO_TOP")),
        float(getattr(scene, "voxtart_stack_alpha_threshold", 0.05)),
        float(getattr(scene, "voxtart_stack_voxel_scale", 1.0)),
        int(getattr(scene, "voxtart_stack_palette_limit", 256)),
    )


def _store_stack_metadata(
    obj: bpy.types.Object,
    document: StackDocument,
    source: Path,
    axis: str,
    layer_order: str,
    alpha_threshold: float,
    voxel_scale: float,
    palette_limit: int,
) -> None:
    obj[STACK_PROPERTY] = document.encode()
    obj[STACK_SOURCE_PROPERTY] = str(source)
    obj["voxtart_stack_axis"] = axis
    obj["voxtart_stack_order"] = layer_order
    obj["voxtart_stack_alpha_threshold"] = float(alpha_threshold)
    obj["voxtart_stack_voxel_scale"] = float(voxel_scale)
    obj["voxtart_stack_palette_limit"] = int(palette_limit)
    obj["voxtart_stack_layer_count"] = len(document.layers)
    obj["voxtart_stack_source_format"] = document.source_format


def _create_stack_object(context, document: StackDocument, source: Path):
    axis, layer_order, alpha_threshold, voxel_scale, palette_limit = _scene_options(context.scene)
    asset = stack_to_voxel_asset(
        document,
        axis=axis,
        layer_order=layer_order,
        alpha_threshold=alpha_threshold,
        voxel_scale=voxel_scale,
        palette_limit=palette_limit,
    )
    obj = operators._create_asset_object(context, source.stem or "Sprite Stack", asset, backend="greedy")
    _store_stack_metadata(
        obj,
        document,
        source,
        axis,
        layer_order,
        alpha_threshold,
        voxel_scale,
        palette_limit,
    )
    obj["voxtart_stack_mode"] = True
    return obj


def _import_voxel_models(context, path: Path):
    data = path.read_bytes()
    grid_size = float(getattr(context.scene, "voxtart_stack_voxel_scale", 1.0))
    if path.suffix.lower() == ".vox" or data[:4] == b"VOX ":
        models = decode_magica_vox_models(data, grid_size=grid_size)
        format_name = "VOX"
    else:
        models = decode_qb_models(data, grid_size=grid_size)
        format_name = "QB"
    if not models:
        raise ValueError("voxel file contains no models")
    objects = []
    for index, model in enumerate(models, start=1):
        name = path.stem if len(models) == 1 else f"{path.stem} {model.name or index}"
        obj = operators._create_asset_object(context, name, model.asset, backend="greedy")
        obj[STACK_SOURCE_PROPERTY] = str(path)
        obj["voxtart_stack_source_format"] = format_name
        obj["voxtart_stack_mode"] = True
        obj["voxtart_stack_layer_count"] = model.asset.bounds[1][2] - model.asset.bounds[0][2] + 1 if model.asset.bounds else 0
        objects.append(obj)
    return objects


class VOXTART_OT_import_stack(bpy.types.Operator, ImportHelper):
    bl_idname = "voxtart.import_stack"
    bl_label = "Import Sprite Stack / Layered File"
    bl_description = "Import SpriteStack VOX exports, PSD/ORA/KRA layers, images, or slice manifests as rendered voxels"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".psd"
    filter_glob: StringProperty(
        default="*.psd;*.ora;*.kra;*.sstack;*.spritestack;*.stack;*.json;*.vox;*.qb;*.png;*.jpg;*.jpeg;*.tga;*.bmp;*.tif;*.tiff;*.gif;*.webp",
        options={"HIDDEN"},
    )

    def execute(self, context):
        path = Path(self.filepath).expanduser()
        try:
            if path.suffix.lower() in {".vox", ".qb"} or path.read_bytes()[:4] == b"VOX ":
                objects = _import_voxel_models(context, path)
                self.report({"INFO"}, f"imported {len(objects)} SpriteStack-compatible {path.suffix[1:].upper() or 'VOX'} model(s)")
                return {"FINISHED"}
            document = _load_document(path)
            obj = _create_stack_object(context, document, path)
            context.scene.voxtart_stack_mode = True
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"stacked {len(document.layers)} layer(s) into {obj.name}")
        return {"FINISHED"}


class VOXTART_OT_rebuild_stack(bpy.types.Operator):
    bl_idname = "voxtart.rebuild_stack"
    bl_label = "Rebuild Stack Voxels"
    bl_description = "Re-render the active layered stack with the current stack settings"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        return obj is not None and isinstance(obj.get(STACK_PROPERTY), str)

    def execute(self, context):
        obj = context.active_object
        try:
            document = StackDocument.decode(obj[STACK_PROPERTY])
            axis, layer_order, alpha_threshold, voxel_scale, palette_limit = _scene_options(context.scene)
            asset = stack_to_voxel_asset(
                document,
                axis=axis,
                layer_order=layer_order,
                alpha_threshold=alpha_threshold,
                voxel_scale=voxel_scale,
                palette_limit=palette_limit,
            )
            operators._replace_asset_mesh(
                obj,
                asset,
                backend=str(obj.get(operators.MESH_BACKEND_PROPERTY, "greedy")),
                layer_stack=LayerStack.from_asset(asset),
            )
            source = Path(str(obj.get(STACK_SOURCE_PROPERTY, document.source_name or obj.name)))
            _store_stack_metadata(obj, document, source, axis, layer_order, alpha_threshold, voxel_scale, palette_limit)
        except (AttributeError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"rebuilt {len(document.layers)} stack layer(s)")
        return {"FINISHED"}


class VOXTART_OT_export_stack_manifest(bpy.types.Operator, ExportHelper):
    bl_idname = "voxtart.export_stack_manifest"
    bl_label = "Export Stack Manifest"
    bl_description = "Export the active layered stack as a portable SpriteStack-style JSON manifest"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".sstack.json"
    filter_glob: StringProperty(default="*.sstack.json;*.json", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        return obj is not None and isinstance(obj.get(STACK_PROPERTY), str)

    def execute(self, context):
        try:
            document = StackDocument.decode(context.active_object[STACK_PROPERTY])
            Path(self.filepath).write_text(document.encode() + "\n", encoding="utf-8", newline="\n")
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"exported {len(document.layers)} stack layer(s)")
        return {"FINISHED"}


class VOXTART_PT_stack_panel(bpy.types.Panel):
    bl_label = "SpriteStack / Layered Voxels"
    bl_idname = "VOXTART_PT_stack_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 37

    @classmethod
    def poll(cls, _context):
        return True

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "voxtart_stack_mode", text="Stacking Mode")
        settings = layout.box()
        settings.label(text="Layered voxel render", icon="IMAGE_DATA")
        settings.prop(scene, "voxtart_stack_axis", text="Depth Axis")
        settings.prop(scene, "voxtart_stack_order", text="Layer Order")
        settings.prop(scene, "voxtart_stack_alpha_threshold", text="Alpha Threshold")
        settings.prop(scene, "voxtart_stack_voxel_scale", text="Voxel Scale")
        settings.prop(scene, "voxtart_stack_palette_limit", text="Palette Limit")
        layout.operator(VOXTART_OT_import_stack.bl_idname, text="Import PSD / ORA / Slices", icon="IMPORT")
        layout.label(text="SpriteStack .vox exports are imported directly.", icon="INFO")
        obj = getattr(context, "active_object", None)
        if obj is None or not isinstance(obj.get(STACK_PROPERTY), str):
            layout.label(text="Import a layered file to create rendered voxels.", icon="INFO")
            return
        layout.separator()
        layout.label(text=f"{obj.get('voxtart_stack_layer_count', '?')} layer(s) · {obj.get('voxtart_stack_source_format', 'stack')}", icon="MESH_CUBE")
        layout.label(text=str(obj.get(STACK_SOURCE_PROPERTY, "")), icon="FILE")
        layout.operator(VOXTART_OT_rebuild_stack.bl_idname, text="Rebuild with Settings", icon="FILE_REFRESH")
        layout.operator(VOXTART_OT_export_stack_manifest.bl_idname, text="Export .sstack.json", icon="EXPORT")
        try:
            document = StackDocument.decode(obj[STACK_PROPERTY])
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            layout.label(text=f"Saved stack data is invalid: {error}", icon="ERROR")
            return
        layer_box = layout.box()
        layer_box.label(text="Source layers (top shown first)", icon="LINENUMBERS")
        for layer in tuple(reversed(document.layers))[:32]:
            layer_box.label(
                text=f"{layer.name} · {layer.width}×{layer.height} · {'visible' if layer.visible else 'hidden'}",
                icon="HIDE_OFF" if layer.visible else "HIDE_ON",
            )


_CLASSES = (
    VOXTART_OT_import_stack,
    VOXTART_OT_rebuild_stack,
    VOXTART_OT_export_stack_manifest,
    VOXTART_PT_stack_panel,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_stack_mode = BoolProperty(
        name="Stacking Mode",
        description="Enable layered image to voxel stack workflows",
        default=False,
    )
    bpy.types.Scene.voxtart_stack_axis = EnumProperty(
        name="Stack Depth Axis",
        items=[(axis, axis, f"Place slices along {axis} axis") for axis in STACK_AXIS_ITEMS],
        default="Z",
    )
    bpy.types.Scene.voxtart_stack_order = EnumProperty(
        name="Layer Order",
        items=(
            ("BOTTOM_TO_TOP", "Bottom to Top", "First layer is the nearest bottom slice"),
            ("TOP_TO_BOTTOM", "Top to Bottom", "Reverse the source layer order"),
        ),
        default="BOTTOM_TO_TOP",
    )
    bpy.types.Scene.voxtart_stack_alpha_threshold = FloatProperty(
        name="Stack Alpha Threshold",
        min=0.0,
        max=1.0,
        default=0.05,
    )
    bpy.types.Scene.voxtart_stack_voxel_scale = FloatProperty(
        name="Stack Voxel Scale",
        min=0.001,
        max=100.0,
        default=1.0,
    )
    bpy.types.Scene.voxtart_stack_palette_limit = IntProperty(
        name="Stack Palette Limit",
        min=1,
        max=256,
        default=256,
    )


def unregister():
    for name in (
        "voxtart_stack_palette_limit",
        "voxtart_stack_voxel_scale",
        "voxtart_stack_alpha_threshold",
        "voxtart_stack_order",
        "voxtart_stack_axis",
        "voxtart_stack_mode",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


__all__ = [
    "VOXTART_OT_export_stack_manifest",
    "VOXTART_OT_import_stack",
    "VOXTART_OT_rebuild_stack",
    "VOXTART_PT_stack_panel",
    "register",
    "unregister",
]
