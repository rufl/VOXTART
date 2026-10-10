# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Palette window, importers, and palette enforcement for VOXTART."""


import json
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from .palette import (
    DEFAULT_PALETTE,
    MAX_PALETTE_BYTES,
    MAX_PALETTE_COLORS,
    PALETTE_SOURCES,
    compact_palette,
    coerce_color,
    load_palette_file,
    normalize_palette,
    nearest_palette_index,
    palette_gradient,
    parse_palette_bytes,
    reorder_palette,
    scene_palette,
    source_by_id,
)
from .palette_reduce import PALETTE_REDUCTION_METHODS, reduce_asset_palette
from .operators import ASSET_PROPERTY
from .voxel import VoxelAsset
_REDUCTION_ITEMS = tuple(
    (
        method,
        method.replace("_", " ").title(),
        "Deterministically reduce the active asset palette",
    )
    for method in PALETTE_REDUCTION_METHODS
)



def _source_items(_self, _context):
    return [(source.source_id, source.name, source.license_note) for source in PALETTE_SOURCES]


def _active_asset(context):
    from . import operators

    obj = getattr(context, "active_object", None)
    if obj is None or ASSET_PROPERTY not in obj:
        return None
    return obj, operators._load_asset(obj), operators._load_layer_stack(obj)


def _set_palette(
    scene,
    colors,
    *,
    name: str,
    source: str = "",
    preserve_duplicates: bool = False,
) -> list[tuple[float, float, float, float]]:
    if preserve_duplicates:
        normalized = [coerce_color(color) for color in colors]
        if not normalized:
            raise ValueError("palette contains no colors")
        if len(normalized) > MAX_PALETTE_COLORS:
            raise ValueError(f"palettes may contain at most {MAX_PALETTE_COLORS} colors")
    else:
        normalized = normalize_palette(colors)
    collection = scene.voxtart_palette_colors
    collection.clear()
    for index, color in enumerate(normalized):
        item = collection.add()
        item.name = f"Color {index:03d}"
        item.color = color
        if hasattr(item, "selected"):
            item.selected = False
    scene.voxtart_palette_name = name[:128] or "VOXTART Palette"
    scene.voxtart_palette_source = source[:512]
    scene.voxtart_palette_index = 0
    scene.voxtart_edit_palette_index = 0
    if hasattr(scene, "voxtart_palette_anchor"):
        scene.voxtart_palette_anchor = 0
    return normalized


def _ensure_palette(scene) -> list[tuple[float, float, float, float]]:
    collection = getattr(scene, "voxtart_palette_colors", None)
    if collection is not None and len(collection) == 0:
        for index, color in enumerate(DEFAULT_PALETTE):
            item = collection.add()
            item.name = f"Color {index:03d}"
            item.color = color
        scene.voxtart_palette_name = "VOXTART Default"
        scene.voxtart_palette_source = ""
    return scene_palette(scene, deduplicate=False)


def _palette_for_display(scene) -> list[tuple[float, float, float, float]]:
    """Read palette UI data without mutating Blender data during panel draw."""
    collection = getattr(scene, "voxtart_palette_colors", None)
    if collection is None or len(collection) == 0:
        return list(DEFAULT_PALETTE)
    return scene_palette(scene, deduplicate=False)
def _color_hex(color) -> str:
    channels = tuple(max(0, min(255, int(round(float(value) * 255.0)))) for value in color)
    return "#" + "".join(f"{channel:02X}" for channel in channels[:3])


def _apply_palette_to_active(context, colors, *, index_map=None) -> None:
    from . import operators

    active = _active_asset(context)
    if active is None:
        return
    obj, asset, stack = active
    target = [coerce_color(color) for color in colors]
    if not target:
        raise ValueError("palette contains no colors")
    if index_map is None:
        stack.remap_palette(asset.palette, target)
    else:
        stack.remap_palette_indices(index_map)
    replacement = VoxelAsset(
        grid_size=asset.grid_size,
        palette=target,
        voxels=stack.composite(target),
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
    )
    replacement.provenance["palette"] = str(getattr(context.scene, "voxtart_palette_name", "VOXTART Palette"))
    source = str(getattr(context.scene, "voxtart_palette_source", ""))
    if source:
        replacement.provenance["palette_source"] = source
    operators._replace_asset_mesh(obj, replacement, layer_stack=stack)
    context.scene.voxtart_edit_palette_index = min(
        int(context.scene.voxtart_palette_index), len(target) - 1
    )


def _commit_palette(
    context,
    colors,
    *,
    name: str,
    source: str = "",
    preserve_duplicates: bool = False,
    index_map=None,
) -> None:
    normalized = _set_palette(
        context.scene,
        colors,
        name=name,
        source=source,
        preserve_duplicates=preserve_duplicates,
    )
    if bool(getattr(context.scene, "voxtart_palette_enforce", True)):
        _apply_palette_to_active(context, normalized, index_map=index_map)


def _download(url: str) -> tuple[bytes, str]:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("palette URL must use http or https")
    request = urllib.request.Request(url, headers={"User-Agent": "VOXTART palette importer"})
    try:
        with urllib.request.urlopen(request, timeout=20.0) as response:
            data = response.read(MAX_PALETTE_BYTES + 1)
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise ValueError(f"could not download palette: {error}") from error
    if len(data) > MAX_PALETTE_BYTES:
        raise ValueError("downloaded palette is too large")
    suffix = Path(parsed.path).suffix.lower()
    return data, suffix


def _load_image_palette(filepath: str) -> list[tuple[float, float, float, float]]:
    image = bpy.data.images.load(filepath, check_existing=False)
    try:
        width, height = image.size
        if width * height > MAX_PALETTE_COLORS * 1024:
            raise ValueError("palette image contains too many pixels")
        pixels = tuple(image.pixels)
        colors = [pixels[index:index + 4] for index in range(0, len(pixels), 4)]
        return normalize_palette(colors)
    finally:
        bpy.data.images.remove(image)


def _parse_file(filepath: str) -> list[tuple[float, float, float, float]]:
    if Path(filepath).suffix.lower() == ".png":
        return _load_image_palette(filepath)
    return load_palette_file(filepath)


def _parse_remote(data: bytes, suffix: str):
    if suffix == ".png":
        temporary = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            temporary.write(data)
            temporary.close()
            return _load_image_palette(temporary.name)
        finally:
            Path(temporary.name).unlink(missing_ok=True)
    return parse_palette_bytes(data, suffix)


class VOXTART_PG_palette_color(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", default="Color")
    color: FloatVectorProperty(
        name="Color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )
    selected: BoolProperty(name="Selected", default=False, options={"HIDDEN"})


class VOXTART_OT_palette_select(bpy.types.Operator):
    bl_idname = "voxtart.palette_select"
    bl_label = "Select Palette Color"
    bl_description = "Select a palette color; Ctrl toggles and Shift selects a range"

    index: IntProperty(name="Index", min=0, default=0, options={"HIDDEN"})
    extend: BoolProperty(name="Extend Selection", default=False, options={"HIDDEN"})
    range_select: BoolProperty(name="Range Selection", default=False, options={"HIDDEN"})

    def invoke(self, context, event):
        self.extend = bool(event.ctrl)
        self.range_select = bool(event.shift)
        return self.execute(context)

    def execute(self, context):
        scene = context.scene
        colors = _ensure_palette(scene)
        if not 0 <= self.index < len(colors):
            self.report({"ERROR"}, "palette index out of range")
            return {"CANCELLED"}
        collection = scene.voxtart_palette_colors
        if self.range_select:
            anchor = max(0, min(len(collection) - 1, int(getattr(scene, "voxtart_palette_anchor", self.index))))
            if not self.extend:
                for item in collection:
                    item.selected = False
            for index in range(min(anchor, self.index), max(anchor, self.index) + 1):
                collection[index].selected = True
        elif self.extend:
            collection[self.index].selected = not collection[self.index].selected
        else:
            for item in collection:
                item.selected = False
            collection[self.index].selected = True
        scene.voxtart_palette_hover_index = self.index
        scene.voxtart_palette_index = self.index
        scene.voxtart_edit_palette_index = self.index
        scene.voxtart_palette_anchor = self.index
        return {"FINISHED"}
class VOXTART_OT_palette_set_selected(bpy.types.Operator):
    bl_idname = "voxtart.palette_set_selected"
    bl_label = "Apply Picker To Slot"
    bl_description = "Apply the color picker value to the active palette slot"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        colors = _ensure_palette(scene)
        index = int(getattr(scene, "voxtart_palette_index", 0))
        if not 0 <= index < len(colors):
            self.report({"ERROR"}, "palette index out of range")
            return {"CANCELLED"}
        replacement = list(colors)
        replacement[index] = coerce_color(scene.voxtart_palette_edit_color)
        _commit_palette(
            context,
            replacement,
            name=scene.voxtart_palette_name,
            source=scene.voxtart_palette_source,
            preserve_duplicates=True,
            index_map={palette_index: palette_index for palette_index in range(len(colors))},
        )
        scene.voxtart_palette_index = index
        scene.voxtart_edit_palette_index = index
        for item_index, item in enumerate(scene.voxtart_palette_colors):
            item.selected = item_index == index
        return {"FINISHED"}



def _selected_palette_indices(scene) -> list[int]:
    collection = getattr(scene, "voxtart_palette_colors", None)
    if collection is None:
        return []
    selected = [index for index, item in enumerate(collection) if bool(getattr(item, "selected", False))]
    if selected:
        return selected
    index = int(getattr(scene, "voxtart_palette_index", 0))
    return [index] if 0 <= index < len(collection) else []


class VOXTART_OT_palette_gradient(bpy.types.Operator):
    bl_idname = "voxtart.palette_gradient"
    bl_label = "Generate Palette Gradient"
    bl_description = "Replace the palette with an inclusive linear color gradient"
    bl_options = {"REGISTER", "UNDO"}

    start: FloatVectorProperty(
        name="Start",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0, 1.0),
    )
    end: FloatVectorProperty(
        name="End",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )
    count: IntProperty(name="Count", min=1, max=MAX_PALETTE_COLORS, default=8)

    def execute(self, context):
        try:
            colors = palette_gradient(self.start, self.end, self.count)
            _commit_palette(context, colors, name=context.scene.voxtart_palette_name)
        except (TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_palette_duplicate(bpy.types.Operator):
    bl_idname = "voxtart.palette_duplicate"
    bl_label = "Duplicate Palette Color"
    bl_description = "Duplicate the selected palette entries without changing voxel indices"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="Index", min=0, default=0, options={"HIDDEN"})

    def execute(self, context):
        colors = _ensure_palette(context.scene)
        indices = set(_selected_palette_indices(context.scene))
        if not indices and 0 <= self.index < len(colors):
            indices = {self.index}
        if len(colors) + len(indices) > MAX_PALETTE_COLORS:
            self.report({"ERROR"}, f"palettes may contain at most {MAX_PALETTE_COLORS} colors")
            return {"CANCELLED"}
        target = []
        index_map = {}
        for old_index, color in enumerate(colors):
            target.append(color)
            index_map[old_index] = len(target) - 1
            if old_index in indices:
                target.append(color)
        _commit_palette(
            context,
            target,
            name=context.scene.voxtart_palette_name,
            preserve_duplicates=True,
            index_map=index_map,
        )
        return {"FINISHED"}


class VOXTART_OT_palette_reorder(bpy.types.Operator):
    bl_idname = "voxtart.palette_reorder"
    bl_label = "Reorder Palette Color"
    bl_description = "Move one palette entry while preserving every voxel palette index"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="Index", min=0, default=0, options={"HIDDEN"})
    target_index: IntProperty(name="Target Index", min=0, default=0, options={"HIDDEN"})

    def execute(self, context):
        colors = _ensure_palette(context.scene)
        if not 0 <= self.index < len(colors) or not 0 <= self.target_index < len(colors):
            self.report({"ERROR"}, "palette index out of range")
            return {"CANCELLED"}
        order = list(range(len(colors)))
        moved = order.pop(self.index)
        order.insert(self.target_index, moved)
        target, index_map = reorder_palette(colors, order)
        _commit_palette(
            context,
            target,
            name=context.scene.voxtart_palette_name,
            preserve_duplicates=True,
            index_map=index_map,
        )
        context.scene.voxtart_palette_index = self.target_index
        context.scene.voxtart_edit_palette_index = self.target_index
        return {"FINISHED"}


class VOXTART_OT_palette_compact(bpy.types.Operator):
    bl_idname = "voxtart.palette_compact"
    bl_label = "Compact Palette"
    bl_description = "Remove duplicate and unused palette entries with exact voxel remapping"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        colors = _ensure_palette(context.scene)
        active = _active_asset(context)
        used = None
        if active is not None:
            _obj, _asset, stack = active
            used = {
                palette_index
                for layer in stack.layers
                for palette_index in layer.voxels.values()
            }
        try:
            target, index_map = compact_palette(colors, used)
            _commit_palette(
                context,
                target,
                name=context.scene.voxtart_palette_name,
                preserve_duplicates=True,
                index_map=index_map if active is not None else None,
            )
        except (TypeError, ValueError, IndexError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_palette_add(bpy.types.Operator):
    bl_idname = "voxtart.palette_add"
    bl_label = "Add Palette Color"
    bl_description = "Append a color to the selected VOXTART palette"
    bl_options = {"REGISTER", "UNDO"}

    color: FloatVectorProperty(
        name="Color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )

    def execute(self, context):
        colors = _ensure_palette(context.scene)
        if len(colors) >= MAX_PALETTE_COLORS:
            self.report({"ERROR"}, f"palettes may contain at most {MAX_PALETTE_COLORS} colors")
            return {"CANCELLED"}
        _commit_palette(context, [*colors, tuple(self.color)], name=context.scene.voxtart_palette_name)
        return {"FINISHED"}


class VOXTART_OT_palette_remove(bpy.types.Operator):
    bl_idname = "voxtart.palette_remove"
    bl_label = "Remove Palette Color"
    bl_description = "Remove a palette color and remap affected voxels"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="Index", min=0, default=0, options={"HIDDEN"})

    def execute(self, context):
        colors = _ensure_palette(context.scene)
        if len(colors) <= 1:
            self.report({"ERROR"}, "a palette must keep at least one color")
            return {"CANCELLED"}
        if not 0 <= self.index < len(colors):
            self.report({"ERROR"}, "palette index out of range")
            return {"CANCELLED"}
        removed = colors[self.index]
        target = colors[:self.index] + colors[self.index + 1:]
        index_map = {}
        for old_index in range(len(colors)):
            if old_index == self.index:
                index_map[old_index] = nearest_palette_index(removed, target)
            elif old_index > self.index:
                index_map[old_index] = old_index - 1
            else:
                index_map[old_index] = old_index
        _commit_palette(
            context,
            target,
            name=context.scene.voxtart_palette_name,
            preserve_duplicates=True,
            index_map=index_map,
        )
        return {"FINISHED"}


class VOXTART_OT_palette_apply(bpy.types.Operator):
    bl_idname = "voxtart.palette_apply"
    bl_label = "Apply Palette To Asset"
    bl_description = "Restrict every voxel color in the active asset to this palette"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        try:
            _apply_palette_to_active(context, _ensure_palette(context.scene))
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class VOXTART_OT_palette_reduce(bpy.types.Operator):
    bl_idname = "voxtart.palette_reduce"
    bl_label = "Reduce Active Palette"
    bl_description = "Reduce the active asset palette with deterministic weighted median cut"
    bl_options = {"REGISTER", "UNDO"}

    max_colors: IntProperty(
        name="Maximum Colors",
        min=1,
        max=MAX_PALETTE_COLORS,
        default=32,
    )
    method: EnumProperty(name="Method", items=_REDUCTION_ITEMS, default="MEDIAN_CUT")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and ASSET_PROPERTY in context.active_object

    def execute(self, context):
        from . import operators

        active = _active_asset(context)
        if active is None:
            self.report({"ERROR"}, "activate a VOXTART voxel asset first")
            return {"CANCELLED"}
        obj, asset, stack = active
        try:
            reduced, report = reduce_asset_palette(
                asset,
                self.max_colors,
                method=self.method,
            )
            stack.remap_palette(asset.palette, reduced.palette)
            replacement = VoxelAsset(
                grid_size=reduced.grid_size,
                palette=list(reduced.palette),
                voxels=stack.composite(reduced.palette),
                sockets=list(reduced.sockets),
                provenance=dict(reduced.provenance),
            )
            operators._replace_asset_mesh(obj, replacement, layer_stack=stack)
            obj["voxtart_palette_reduction_report"] = json.dumps(report.to_dict(), sort_keys=True)
        except (TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"reduced palette from {report.source_colors} to {report.target_colors} colors",
        )
        return {"FINISHED"}


class VOXTART_OT_palette_import(bpy.types.Operator, ImportHelper):
    bl_idname = "voxtart.palette_import"
    bl_label = "Import Palette File"
    bl_description = "Import HEX, TXT, GPL, PAL, ASE, JSON, or PNG palette data"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".hex"
    filter_glob: StringProperty(
        default="*.hex;*.txt;*.gpl;*.pal;*.ase;*.json;*.png",
        options={"HIDDEN"},
    )

    def execute(self, context):
        try:
            colors = _parse_file(self.filepath)
            _commit_palette(
                context,
                colors,
                name=Path(self.filepath).stem,
                source=f"file://{Path(self.filepath).resolve()}",
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"imported {len(colors)} palette colors")
        return {"FINISHED"}


class VOXTART_OT_palette_import_url(bpy.types.Operator):
    bl_idname = "voxtart.palette_import_url"
    bl_label = "Import Palette URL"
    bl_description = "Download one palette from an explicit HTTP or HTTPS URL"
    bl_options = {"REGISTER", "UNDO"}

    url: StringProperty(name="URL", default="https://lospec.com/palette-list/dawnbringer-16.hex")

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            data, suffix = _download(self.url)
            colors = _parse_remote(data, suffix)
            _commit_palette(context, colors, name=Path(urllib.parse.urlparse(self.url).path).stem, source=self.url)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"imported {len(colors)} palette colors")
        return {"FINISHED"}


class VOXTART_OT_palette_import_source(bpy.types.Operator):
    bl_idname = "voxtart.palette_import_source"
    bl_label = "Import Free Palette Source"
    bl_description = "Download a free palette format listed by Lospec"
    bl_options = {"REGISTER", "UNDO"}

    source_id: EnumProperty(name="Source", items=_source_items, options={"HIDDEN"})

    def execute(self, context):
        try:
            source = source_by_id(self.source_id or PALETTE_SOURCES[0].source_id)
            data, suffix = _download(source.download_url)
            colors = _parse_remote(data, suffix)
            _commit_palette(context, colors, name=source.name, source=source.page_url)
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"imported {len(colors)} palette colors")
        return {"FINISHED"}


class VOXTART_OT_palette_open_source(bpy.types.Operator):
    bl_idname = "voxtart.palette_open_source"
    bl_label = "Open Palette Source"
    bl_description = "Open the selected palette source page in the system browser"

    def execute(self, context):
        try:
            source = source_by_id(context.scene.voxtart_palette_source_id)
            bpy.ops.wm.url_open(url=source.page_url)
        except (KeyError, RuntimeError, AttributeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}
class VOXTART_PT_color_picker(bpy.types.Panel):
    bl_label = "Color Picker"
    bl_idname = "VOXTART_PT_color_picker"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        colors = _palette_for_display(scene)
        index = max(0, min(len(colors) - 1, int(getattr(scene, "voxtart_palette_index", 0))))
        layout.prop(scene, "voxtart_palette_edit_color", text="Picker")
        picker_row = layout.row(align=True)
        picker_row.label(text=_color_hex(scene.voxtart_palette_edit_color), icon="COLOR")
        apply = picker_row.operator(VOXTART_OT_palette_set_selected.bl_idname, text="Set Slot", icon="CHECKMARK")
        apply_row = layout.row(align=True)
        apply_row.prop(scene, "voxtart_palette_index", text="Slot")
        select = apply_row.operator(VOXTART_OT_palette_select.bl_idname, text="Use", icon="EYEDROPPER")
        select.index = index
        add = layout.operator(VOXTART_OT_palette_add.bl_idname, text="Add Picker Color", icon="ADD")
        add.color = tuple(scene.voxtart_palette_edit_color)
        layout.label(text=f"Active slot {index}: {_color_hex(colors[index])}", icon="MATERIAL")



class VOXTART_PT_palette_panel(bpy.types.Panel):
    bl_label = "Palette Library (Advanced)"
    bl_idname = "VOXTART_PT_palette_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VOXTART"
    bl_order = 40
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        colors = _palette_for_display(scene)
        layout.prop(scene, "voxtart_palette_name", text="Name")
        selected_count = sum(bool(getattr(item, "selected", False)) for item in scene.voxtart_palette_colors)
        layout.label(text=f"{len(colors)} colors · {selected_count} selected")
        layout.prop(scene, "voxtart_palette_enforce", text="Enforce on voxelize/import")
        layout.separator()
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True)
        for index, item in enumerate(scene.voxtart_palette_colors):
            row = grid.row(align=True)
            select = row.operator(
                VOXTART_OT_palette_select.bl_idname,
                text=str(index),
                depress=bool(getattr(item, "selected", False)),
            )
            select.index = index
            row.prop(item, "color", text="")
            duplicate = row.operator(VOXTART_OT_palette_duplicate.bl_idname, text="", icon="DUPLICATE")
            duplicate.index = index
            remove = row.operator(VOXTART_OT_palette_remove.bl_idname, text="", icon="X")
            remove.index = index
            if len(colors) > 1:
                move = row.operator(VOXTART_OT_palette_reorder.bl_idname, text="", icon="TRIA_UP")
                move.index = index
                move.target_index = max(0, index - 1)
                move = row.operator(VOXTART_OT_palette_reorder.bl_idname, text="", icon="TRIA_DOWN")
                move.index = index
                move.target_index = min(len(colors) - 1, index + 1)
        hover_index = int(getattr(scene, "voxtart_palette_hover_index", -1))
        if 0 <= hover_index < len(colors):
            layout.label(text=f"Preview {hover_index}: {_color_hex(colors[hover_index])}", icon="COLOR")
        row = layout.row(align=True)
        add = row.operator(VOXTART_OT_palette_add.bl_idname, text="Add", icon="ADD")
        add.color = tuple(scene.voxtart_palette_edit_color)
        layout.prop(scene, "voxtart_palette_edit_color", text="New Color")
        row = layout.row(align=True)
        row.operator(VOXTART_OT_palette_compact.bl_idname, text="Compact", icon="SORTALPHA")
        layout.prop(scene, "voxtart_palette_gradient_start", text="Gradient Start")
        layout.prop(scene, "voxtart_palette_gradient_end", text="Gradient End")
        gradient = layout.row(align=True)
        gradient.prop(scene, "voxtart_palette_gradient_count", text="Count")
        gradient_op = gradient.operator(VOXTART_OT_palette_gradient.bl_idname, text="Generate", icon="COLOR")
        gradient_op.start = tuple(scene.voxtart_palette_gradient_start)
        gradient_op.end = tuple(scene.voxtart_palette_gradient_end)
        gradient_op.count = scene.voxtart_palette_gradient_count
        layout.operator(VOXTART_OT_palette_import.bl_idname, text="Import Palette File", icon="FILE_FOLDER")
        layout.operator(VOXTART_OT_palette_import_url.bl_idname, text="Import Palette URL", icon="URL")
        layout.separator()
        layout.prop(scene, "voxtart_palette_source_id", text="Free Source")
        source = layout.operator(VOXTART_OT_palette_import_source.bl_idname, text="Import Free Source", icon="IMPORT")
        source.source_id = scene.voxtart_palette_source_id
        layout.operator(VOXTART_OT_palette_open_source.bl_idname, text="Open Source Page", icon="URL")
        if scene.voxtart_palette_source:
            layout.label(text="Source recorded on palette", icon="INFO")
        active = getattr(context, "active_object", None)
        if active is not None and ASSET_PROPERTY in active:
            layout.separator()
            layout.operator(VOXTART_OT_palette_apply.bl_idname, text="Apply To Active Asset", icon="CHECKMARK")
            layout.prop(scene, "voxtart_palette_reduce_colors", text="Reduce To")
            layout.prop(scene, "voxtart_palette_reduce_method", text="Method")
            reduce = layout.operator(
                VOXTART_OT_palette_reduce.bl_idname,
                text="Reduce Active Palette",
                icon="MOD_DECIM",
            )
            reduce.max_colors = scene.voxtart_palette_reduce_colors
            reduce.method = scene.voxtart_palette_reduce_method
        source_id = scene.voxtart_palette_source_id
        try:
            layout.label(text=source_by_id(source_id).license_note, icon="INFO")
        except KeyError:
            pass


_CLASSES = (
    VOXTART_PG_palette_color,
    VOXTART_OT_palette_select,
    VOXTART_OT_palette_set_selected,
    VOXTART_OT_palette_gradient,
    VOXTART_OT_palette_duplicate,
    VOXTART_OT_palette_reorder,
    VOXTART_OT_palette_compact,
    VOXTART_OT_palette_add,
    VOXTART_OT_palette_remove,
    VOXTART_OT_palette_apply,
    VOXTART_OT_palette_reduce,
    VOXTART_OT_palette_import,
    VOXTART_OT_palette_import_url,
    VOXTART_OT_palette_import_source,
    VOXTART_OT_palette_open_source,
    VOXTART_PT_color_picker,
    VOXTART_PT_palette_panel,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.voxtart_palette_colors = CollectionProperty(type=VOXTART_PG_palette_color)
    bpy.types.Scene.voxtart_palette_name = StringProperty(name="Palette Name", default="VOXTART Default")
    bpy.types.Scene.voxtart_palette_source = StringProperty(name="Palette Source", default="", options={"HIDDEN"})
    bpy.types.Scene.voxtart_palette_index = IntProperty(name="Palette Color", min=0, max=MAX_PALETTE_COLORS - 1, default=0)
    bpy.types.Scene.voxtart_palette_anchor = IntProperty(
        name="Palette Selection Anchor",
        min=0,
        max=MAX_PALETTE_COLORS - 1,
        default=0,
        options={"HIDDEN"},
    )
    bpy.types.Scene.voxtart_palette_hover_index = IntProperty(
        name="Palette Hover",
        min=-1,
        max=MAX_PALETTE_COLORS - 1,
        default=-1,
        options={"HIDDEN"},
    )
    bpy.types.Scene.voxtart_palette_enforce = BoolProperty(
        name="Enforce Palette",
        description="Quantize voxelized and imported colors to the selected palette",
        default=True,
    )
    bpy.types.Scene.voxtart_palette_edit_color = FloatVectorProperty(
        name="New Palette Color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )
    bpy.types.Scene.voxtart_palette_gradient_start = FloatVectorProperty(
        name="Gradient Start",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0, 1.0),
    )
    bpy.types.Scene.voxtart_palette_gradient_end = FloatVectorProperty(
        name="Gradient End",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )
    bpy.types.Scene.voxtart_palette_gradient_count = IntProperty(
        name="Gradient Count",
        min=1,
        max=MAX_PALETTE_COLORS,
        default=8,
    )
    bpy.types.Scene.voxtart_palette_source_id = EnumProperty(
        name="Free Palette Source",
        items=_source_items,
        default=0,
    )
    bpy.types.Scene.voxtart_palette_reduce_colors = IntProperty(
        name="Palette Reduction Colors",
        min=1,
        max=MAX_PALETTE_COLORS,
        default=32,
    )
    bpy.types.Scene.voxtart_palette_reduce_method = EnumProperty(
        name="Palette Reduction Method",
        items=_REDUCTION_ITEMS,
        default="MEDIAN_CUT",
    )

def unregister():
    for name in (
        "voxtart_palette_reduce_method",
        "voxtart_palette_reduce_colors",
        "voxtart_palette_source_id",
        "voxtart_palette_gradient_count",
        "voxtart_palette_gradient_end",
        "voxtart_palette_gradient_start",
        "voxtart_palette_edit_color",
        "voxtart_palette_enforce",
        "voxtart_palette_hover_index",
        "voxtart_palette_anchor",
        "voxtart_palette_index",
        "voxtart_palette_source",
        "voxtart_palette_name",
        "voxtart_palette_colors",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
