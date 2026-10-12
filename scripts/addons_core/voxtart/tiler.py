# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Pixel-tile projection and 2D-to-voxel conversion for VOXTART.

The tiler keeps one explicit scale contract: one source texture pixel occupies
exactly ``pixel_scale`` scene units and one voxel cell in the target asset. The
Blender adapter supplies the editor's current grid size; this module contains
only deterministic texture, tile, projection, and palette logic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from .palette import MAX_PALETTE_COLORS, nearest_palette_index
from .voxel import Cell, Color, VoxelAsset


TILER_AXES = ("X", "Y", "Z")
TILER_MODES = ("STAMP", "REPLACE")
TILER_ROTATIONS = (0, 90, 180, 270)
SEAMLESS_PATTERNS = ("SOLID", "CHECKER", "STRIPES", "NOISE", "BRICK")
MAX_TILE_PIXELS = 4_000_000
MAX_TILE_DEPTH = 256
MAX_SEAMLESS_REPEAT = 256


def _color(value: Iterable[float]) -> Color:
    channels = tuple(float(channel) for channel in value)
    if len(channels) != 4 or not all(math.isfinite(channel) for channel in channels):
        raise ValueError("tiler pixels must contain four finite channels")
    if any(channel < 0.0 or channel > 1.0 for channel in channels):
        raise ValueError("tiler pixel channels must be in [0, 1]")
    return channels  # type: ignore[return-value]


def _cell(value: Iterable[int]) -> Cell:
    result = tuple(value)
    if len(result) != 3 or any(isinstance(axis, bool) or not isinstance(axis, int) for axis in result):
        raise ValueError("tiler anchors must contain three integers")
    return result  # type: ignore[return-value]


def _positive_dimension(value: int, field: str) -> int:
    result = int(value)
    if isinstance(value, bool) or result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


@dataclass(frozen=True, slots=True)
class PixelTexture:
    """Immutable, normalized RGBA pixels with a top-left image origin."""

    width: int
    height: int
    pixels: tuple[Color, ...]

    def __post_init__(self) -> None:
        width = _positive_dimension(self.width, "texture width")
        height = _positive_dimension(self.height, "texture height")
        if width * height > MAX_TILE_PIXELS:
            raise ValueError("texture exceeds the tiler pixel budget")
        pixels = tuple(_color(pixel) for pixel in self.pixels)
        if len(pixels) != width * height:
            raise ValueError("texture pixel count does not match dimensions")
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "pixels", pixels)

    @classmethod
    def from_rgba(cls, width: int, height: int, values: Iterable[float | Color]) -> "PixelTexture":
        raw = tuple(values)
        expected = int(width) * int(height) * 4
        if len(raw) == expected and all(not isinstance(value, (list, tuple)) for value in raw):
            pixels = tuple(
                tuple(float(channel) for channel in raw[index:index + 4])
                for index in range(0, expected, 4)
            )
        else:
            pixels = tuple(raw)  # type: ignore[assignment]
        return cls(width, height, pixels)  # type: ignore[arg-type]

    def pixel(self, x: int, y: int) -> Color:
        if not 0 <= int(x) < self.width or not 0 <= int(y) < self.height:
            return (0.0, 0.0, 0.0, 0.0)
        return self.pixels[int(y) * self.width + int(x)]


def _seamless_repeat(value: int, field: str) -> int:
    result = int(value)
    if isinstance(value, bool) or result != value or not 1 <= result <= MAX_SEAMLESS_REPEAT:
        raise ValueError(
            f"{field} must be an integer between 1 and {MAX_SEAMLESS_REPEAT}"
        )
    return result


def _seamless_hash(x: int, y: int, seed: int) -> float:
    value = (
        x * 0x1F123BB5
        + y * 0x5F356495
        + int(seed) * 0x27D4EB2D
    ) & 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 0x45D9F3B) & 0xFFFFFFFF
    value ^= value >> 16
    return value / 0xFFFFFFFF


def _seamless_noise(
    u: float,
    v: float,
    repeat_x: int,
    repeat_y: int,
    seed: int,
) -> float:
    grid_x = u * repeat_x
    grid_y = v * repeat_y
    base_x = math.floor(grid_x)
    base_y = math.floor(grid_y)
    fraction_x = grid_x - base_x
    fraction_y = grid_y - base_y
    smooth_x = fraction_x * fraction_x * (3.0 - 2.0 * fraction_x)
    smooth_y = fraction_y * fraction_y * (3.0 - 2.0 * fraction_y)
    x0 = base_x % repeat_x
    x1 = (x0 + 1) % repeat_x
    y0 = base_y % repeat_y
    y1 = (y0 + 1) % repeat_y
    top = (
        _seamless_hash(x0, y0, seed) * (1.0 - smooth_x)
        + _seamless_hash(x1, y0, seed) * smooth_x
    )
    bottom = (
        _seamless_hash(x0, y1, seed) * (1.0 - smooth_x)
        + _seamless_hash(x1, y1, seed) * smooth_x
    )
    return top * (1.0 - smooth_y) + bottom * smooth_y


def _mix_colors(first: Color, second: Color, amount: float) -> Color:
    return tuple(
        first[index] * (1.0 - amount) + second[index] * amount
        for index in range(4)
    )  # type: ignore[return-value]


def generate_seamless_texture(
    width: int,
    height: int,
    *,
    pattern: str = "CHECKER",
    color_a: Iterable[float] = (0.15, 0.15, 0.15, 1.0),
    color_b: Iterable[float] = (0.75, 0.75, 0.75, 1.0),
    seed: int = 0,
    repeat_x: int = 2,
    repeat_y: int = 2,
) -> PixelTexture:
    """Generate a deterministic RGBA texture with matching opposite borders."""

    width = _positive_dimension(width, "seamless texture width")
    height = _positive_dimension(height, "seamless texture height")
    if width * height > MAX_TILE_PIXELS:
        raise ValueError("seamless texture exceeds the tiler pixel budget")
    pattern = str(pattern).upper()
    if pattern not in SEAMLESS_PATTERNS:
        raise ValueError(f"unsupported seamless pattern: {pattern!r}")
    repeat_x = _seamless_repeat(repeat_x, "seamless repeat_x")
    repeat_y = _seamless_repeat(repeat_y, "seamless repeat_y")
    if isinstance(seed, bool) or int(seed) != seed:
        raise ValueError("seamless seed must be an integer")
    seed = int(seed)
    first = _color(color_a)
    second = _color(color_b)
    pixels = []
    for y in range(height):
        v = 0.0 if height == 1 else y / (height - 1)
        for x in range(width):
            u = 0.0 if width == 1 else x / (width - 1)
            cell_x = math.floor(u * repeat_x) % repeat_x
            cell_y = math.floor(v * repeat_y) % repeat_y
            if pattern == "SOLID":
                color = first
            elif pattern == "CHECKER":
                color = first if (cell_x + cell_y) % 2 == 0 else second
            elif pattern == "STRIPES":
                color = first if cell_x % 2 == 0 else second
            elif pattern == "NOISE":
                color = _mix_colors(
                    first,
                    second,
                    _seamless_noise(u, v, repeat_x, repeat_y, seed),
                )
            else:
                local_x = (u * repeat_x) % 1.0
                local_y = (v * repeat_y) % 1.0
                offset_x = 0.5 if cell_y % 2 else 0.0
                brick_x = (local_x + offset_x) % 1.0
                mortar = (
                    local_x < 0.1
                    or local_x > 0.9
                    or local_y < 0.12
                    or local_y > 0.88
                    or brick_x < 0.1
                    or brick_x > 0.9
                )
                color = second if mortar else first
            pixels.append(color)
    return PixelTexture(width, height, tuple(pixels))


@dataclass(frozen=True, slots=True)
class Tile:
    """A cropped atlas tile retaining its source pixel rectangle."""

    pixels: PixelTexture
    index: int
    atlas_size: tuple[int, int]
    origin: tuple[int, int]

    @property
    def width(self) -> int:
        return self.pixels.width

    @property
    def height(self) -> int:
        return self.pixels.height

    def uv_rect(self) -> tuple[float, float, float, float]:
        atlas_width, atlas_height = self.atlas_size
        x, y = self.origin
        return (
            x / atlas_width,
            1.0 - (y + self.height) / atlas_height,
            (x + self.width) / atlas_width,
            1.0 - y / atlas_height,
        )



def generate_seamless_tile(
    width: int,
    height: int,
    *,
    pattern: str = "CHECKER",
    color_a: Iterable[float] = (0.15, 0.15, 0.15, 1.0),
    color_b: Iterable[float] = (0.75, 0.75, 0.75, 1.0),
    seed: int = 0,
    repeat_x: int = 2,
    repeat_y: int = 2,
) -> Tile:
    """Generate one seamless tile suitable for UV or voxel projection."""

    texture = generate_seamless_texture(
        width,
        height,
        pattern=pattern,
        color_a=color_a,
        color_b=color_b,
        seed=seed,
        repeat_x=repeat_x,
        repeat_y=repeat_y,
    )
    return Tile(
        pixels=texture,
        index=0,
        atlas_size=(texture.width, texture.height),
        origin=(0, 0),
    )

def extract_tile(texture: PixelTexture, tile_width: int, tile_height: int, index: int) -> Tile:
    """Extract a row-major tile from an exact, evenly divided atlas."""

    tile_width = _positive_dimension(tile_width, "tile width")
    tile_height = _positive_dimension(tile_height, "tile height")
    if texture.width % tile_width or texture.height % tile_height:
        raise ValueError("tile dimensions must divide the texture dimensions")
    columns = texture.width // tile_width
    rows = texture.height // tile_height
    index = int(index)
    if isinstance(index, bool) or not 0 <= index < columns * rows:
        raise ValueError("tile index is outside the texture atlas")
    tile_x = (index % columns) * tile_width
    tile_y = (index // columns) * tile_height
    pixels = tuple(
        texture.pixel(tile_x + x, tile_y + y)
        for y in range(tile_height)
        for x in range(tile_width)
    )
    return Tile(
        pixels=PixelTexture(tile_width, tile_height, pixels),
        index=index,
        atlas_size=(texture.width, texture.height),
        origin=(tile_x, tile_y),
    )


@dataclass(frozen=True, slots=True)
class TilerSettings:
    """Projection settings shared by the UI and pure conversion APIs."""

    pixel_scale: float
    axis: str = "Z"
    side: int = 1
    depth: int = 1
    rotation: int = 0
    flip_x: bool = False
    flip_y: bool = False
    mode: str = "STAMP"
    alpha_threshold: float = 0.05

    def __post_init__(self) -> None:
        pixel_scale = float(self.pixel_scale)
        if not math.isfinite(pixel_scale) or pixel_scale <= 0.0:
            raise ValueError("tiler pixel scale must be positive and finite")
        axis = str(self.axis).upper()
        if axis not in TILER_AXES:
            raise ValueError(f"unsupported tiler axis: {self.axis!r}")
        side = self.side
        if isinstance(side, bool) or not isinstance(side, int) or side not in (-1, 1):
            raise ValueError("tiler side must be -1 or 1")
        depth = self.depth
        if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= MAX_TILE_DEPTH:
            raise ValueError(f"tiler depth must be between 1 and {MAX_TILE_DEPTH}")
        rotation = self.rotation
        if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation not in TILER_ROTATIONS:
            raise ValueError("tiler rotation must be a right-angle turn")
        mode = str(self.mode).upper()
        if mode not in TILER_MODES:
            raise ValueError(f"unsupported tiler mode: {self.mode!r}")
        alpha_threshold = float(self.alpha_threshold)
        if not math.isfinite(alpha_threshold) or not 0.0 <= alpha_threshold <= 1.0:
            raise ValueError("tiler alpha threshold must be in [0, 1]")
        object.__setattr__(self, "pixel_scale", pixel_scale)
        object.__setattr__(self, "axis", axis)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "depth", depth)
        object.__setattr__(self, "rotation", rotation)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "alpha_threshold", alpha_threshold)


def ensure_pixel_scale(asset: VoxelAsset, pixel_scale: float) -> float:
    """Require the editor scale and asset cell scale to describe the same pixel."""

    pixel_scale = float(pixel_scale)
    if not math.isclose(asset.grid_size, pixel_scale, rel_tol=1.0e-9, abs_tol=1.0e-9):
        raise ValueError(
            "tiler pixel scale must match the active asset grid size; "
            f"asset={asset.grid_size:g}, editor={pixel_scale:g}"
        )
    return pixel_scale


def _transformed_pixels(tile: Tile, settings: TilerSettings) -> tuple[int, int, tuple[Color, ...]]:
    source_width, source_height = tile.width, tile.height
    if settings.rotation in (0, 180):
        width, height = source_width, source_height
    else:
        width, height = source_height, source_width
    output = [(0.0, 0.0, 0.0, 0.0)] * (width * height)
    for source_y in range(source_height):
        for source_x in range(source_width):
            if settings.rotation == 0:
                x, y = source_x, source_y
            elif settings.rotation == 90:
                x, y = source_height - 1 - source_y, source_x
            elif settings.rotation == 180:
                x, y = source_width - 1 - source_x, source_height - 1 - source_y
            else:
                x, y = source_y, source_width - 1 - source_x
            if settings.flip_x:
                x = width - 1 - x
            if settings.flip_y:
                y = height - 1 - y
            output[y * width + x] = tile.pixels.pixel(source_x, source_y)
    return width, height, tuple(output)


def _cell_for_pixel(anchor: Cell, axis: str, side: int, depth: int, x: int, y: int, height: int) -> Cell:
    vertical = height - 1 - y
    if axis == "X":
        return anchor[0] + side * depth, anchor[1] + x, anchor[2] + vertical
    if axis == "Y":
        return anchor[0] + x, anchor[1] + side * depth, anchor[2] + vertical
    return anchor[0] + x, anchor[1] + vertical, anchor[2] + side * depth



def tile_uvs(
    tile: Tile,
    face_coordinates: Iterable[tuple[float, float]],
    settings: TilerSettings,
) -> tuple[tuple[float, float], ...]:
    """Map face-plane world coordinates to a tile at the configured texel density.

    ``face_coordinates`` use a local bottom-left origin. A face edge of
    ``tile.width * pixel_scale`` therefore spans exactly one tile in U, and
    larger faces repeat the tile instead of silently changing pixel density.
    """

    coordinates = tuple(
        (float(point[0]), float(point[1]))
        for point in face_coordinates
    )
    if len(coordinates) < 3 or not all(
        math.isfinite(axis)
        for point in coordinates
        for axis in point
    ):
        raise ValueError("tiler faces need at least three finite 2D coordinates")
    minimum_x = min(point[0] for point in coordinates)
    maximum_x = max(point[0] for point in coordinates)
    minimum_y = min(point[1] for point in coordinates)
    maximum_y = max(point[1] for point in coordinates)
    width = maximum_x - minimum_x
    height = maximum_y - minimum_y
    if width <= 0.0 or height <= 0.0:
        raise ValueError("tiler faces must have non-zero width and height")
    tile_width = tile.height if settings.rotation in (90, 270) else tile.width
    tile_height = tile.width if settings.rotation in (90, 270) else tile.height
    repeat_u = width / (tile_width * settings.pixel_scale)
    repeat_v = height / (tile_height * settings.pixel_scale)
    u0, v0, u1, v1 = tile.uv_rect()
    mapped = []
    for coordinate_x, coordinate_y in coordinates:
        output_u = (coordinate_x - minimum_x) / width
        output_v = (coordinate_y - minimum_y) / height
        scaled_u = output_u * repeat_u
        scaled_v = output_v * repeat_v
        if settings.flip_x:
            scaled_u = repeat_u - scaled_u
        if settings.flip_y:
            scaled_v = repeat_v - scaled_v
        if settings.rotation == 0:
            source_u, source_v = scaled_u, scaled_v
        elif settings.rotation == 90:
            source_u, source_v = 1.0 - scaled_v, scaled_u
        elif settings.rotation == 180:
            source_u, source_v = 1.0 - scaled_u, 1.0 - scaled_v
        else:
            source_u, source_v = scaled_v, 1.0 - scaled_u
        mapped.append((
            u0 + source_u * (u1 - u0),
            v0 + source_v * (v1 - v0),
        ))
    return tuple(mapped)


@dataclass(frozen=True, slots=True)
class TileProjection:
    """Transformed tile cells and its complete replace footprint."""

    cells: dict[Cell, Color]
    footprint: tuple[Cell, ...]
    dimensions: tuple[int, int, int]
    world_size: tuple[float, float, float]


def project_tile(tile: Tile, anchor: Iterable[int], settings: TilerSettings) -> TileProjection:
    """Project opaque tile pixels into voxel cells without touching an asset."""

    anchor = _cell(anchor)
    width, height, pixels = _transformed_pixels(tile, settings)
    footprint = []
    cells: dict[Cell, Color] = {}
    for depth in range(settings.depth):
        for y in range(height):
            for x in range(width):
                cell = _cell_for_pixel(anchor, settings.axis, settings.side, depth, x, y, height)
                footprint.append(cell)
                color = pixels[y * width + x]
                if color[3] > settings.alpha_threshold:
                    cells[cell] = color
    if settings.axis == "X":
        dimensions = (settings.depth, width, height)
    elif settings.axis == "Y":
        dimensions = (width, settings.depth, height)
    else:
        dimensions = (width, height, settings.depth)
    world_size = tuple(dimension * settings.pixel_scale for dimension in dimensions)
    return TileProjection(cells, tuple(footprint), dimensions, world_size)


@dataclass(frozen=True, slots=True)
class TileStampReport:
    """Observable result of applying a projected tile to an asset."""

    tile_index: int
    mode: str
    added: int
    removed: int
    painted: int
    projected: int
    footprint: int
    world_size: tuple[float, float, float]


def _palette_index(asset: VoxelAsset, color: Color) -> int:
    try:
        return asset.palette.index(color)
    except ValueError:
        if len(asset.palette) < MAX_PALETTE_COLORS:
            asset.palette.append(color)
            return len(asset.palette) - 1
        return nearest_palette_index(color, asset.palette)


def apply_tile(
    asset: VoxelAsset,
    tile: Tile,
    anchor: Iterable[int],
    settings: TilerSettings,
) -> TileStampReport:
    """Stamp or replace a tile on an asset using the asset's current palette."""

    ensure_pixel_scale(asset, settings.pixel_scale)
    projection = project_tile(tile, anchor, settings)
    removed = 0
    if settings.mode == "REPLACE":
        for cell in projection.footprint:
            if cell in asset.voxels:
                del asset.voxels[cell]
                removed += 1
    added = 0
    painted = 0
    for cell, color in projection.cells.items():
        palette_index = _palette_index(asset, color)
        previous = asset.voxels.get(cell)
        if previous is None:
            added += 1
        elif previous != palette_index:
            painted += 1
        asset.voxels[cell] = palette_index
    return TileStampReport(
        tile_index=tile.index,
        mode=settings.mode,
        added=added,
        removed=removed,
        painted=painted,
        projected=len(projection.cells),
        footprint=len(projection.footprint),
        world_size=projection.world_size,
    )


def tile_to_voxel_asset(
    tile: Tile,
    settings: TilerSettings,
    *,
    palette: Sequence[Color],
    anchor: Iterable[int] = (0, 0, 0),
) -> tuple[VoxelAsset, TileStampReport]:
    """Convert one 2D tile into a standalone voxel asset."""

    normalized_palette = [_color(color) for color in palette]
    if not normalized_palette:
        raise ValueError("tiler conversion requires a non-empty palette")
    asset = VoxelAsset(grid_size=settings.pixel_scale, palette=normalized_palette)
    report = apply_tile(asset, tile, anchor, settings)
    asset.provenance.update({
        "workflow": "pixel_tiler",
        "tile_index": str(tile.index),
        "pixel_scale": f"{settings.pixel_scale:g}",
        "axis": settings.axis,
    })
    return asset, report


def generate_seamless_voxel_asset(
    width: int,
    height: int,
    settings: TilerSettings,
    *,
    palette: Sequence[Color] | None = None,
    pattern: str = "CHECKER",
    color_a: Iterable[float] = (0.15, 0.15, 0.15, 1.0),
    color_b: Iterable[float] = (0.75, 0.75, 0.75, 1.0),
    seed: int = 0,
    repeat_x: int = 2,
    repeat_y: int = 2,
    anchor: Iterable[int] = (0, 0, 0),
) -> tuple[VoxelAsset, TileStampReport]:
    """Generate a seamless tile and convert it into a voxel asset."""

    tile = generate_seamless_tile(
        width,
        height,
        pattern=pattern,
        color_a=color_a,
        color_b=color_b,
        seed=seed,
        repeat_x=repeat_x,
        repeat_y=repeat_y,
    )
    source_palette = palette if palette is not None else (_color(color_a), _color(color_b))
    asset, report = tile_to_voxel_asset(
        tile,
        settings,
        palette=source_palette,
        anchor=anchor,
    )
    asset.provenance.update({
        "generator": "seamless",
        "pattern": str(pattern).upper(),
        "repeat": f"{repeat_x}x{repeat_y}",
        "seed": str(seed),
    })
    return asset, report


__all__ = [
    "MAX_SEAMLESS_REPEAT",
    "MAX_TILE_DEPTH",
    "MAX_TILE_PIXELS",
    "SEAMLESS_PATTERNS",
    "TILER_AXES",
    "TILER_MODES",
    "TILER_ROTATIONS",
    "PixelTexture",
    "Tile",
    "TileProjection",
    "TileStampReport",
    "TilerSettings",
    "apply_tile",
    "ensure_pixel_scale",
    "extract_tile",
    "generate_seamless_texture",
    "generate_seamless_tile",
    "generate_seamless_voxel_asset",
    "project_tile",
    "tile_uvs",
    "tile_to_voxel_asset",
]
