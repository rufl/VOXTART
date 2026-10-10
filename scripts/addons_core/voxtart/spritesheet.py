# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Deterministic frame sampling and spritesheet atlas composition."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .tiler import PixelTexture


SPRITESHEET_FORMAT = "voxtart.spritesheet"
SPRITESHEET_FORMAT_VERSION = 1
MAX_SPRITESHEET_FRAMES = 4096
MAX_SPRITESHEET_PIXELS = 4_000_000
MAX_SPRITESHEET_DIMENSION = 8192


def _integer(value: object, field: str, *, minimum: int | None = None) -> int:
    result = int(value)
    if isinstance(value, bool) or result != value:
        raise ValueError(f"{field} must be an integer")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return result


def sample_frame_range(start: int, end: int, step: int = 1) -> tuple[int, ...]:
    """Return inclusive, ascending scene frames for an export."""

    start = _integer(start, "frame start")
    end = _integer(end, "frame end")
    step = _integer(step, "frame step", minimum=1)
    if end < start:
        raise ValueError("frame end must be greater than or equal to frame start")
    count = (end - start) // step + 1
    if count > MAX_SPRITESHEET_FRAMES:
        raise ValueError(f"spritesheet exports may contain at most {MAX_SPRITESHEET_FRAMES} frames")
    return tuple(range(start, end + 1, step))


@dataclass(frozen=True, slots=True)
class SpritesheetLayout:
    """Row-major atlas geometry with top-left pixel origins."""

    frame_width: int
    frame_height: int
    frame_count: int
    columns: int = 0
    spacing: int = 0
    padding: int = 0

    def __post_init__(self) -> None:
        frame_width = _integer(self.frame_width, "frame width", minimum=1)
        frame_height = _integer(self.frame_height, "frame height", minimum=1)
        frame_count = _integer(self.frame_count, "frame count", minimum=1)
        columns = _integer(self.columns, "atlas columns", minimum=0)
        spacing = _integer(self.spacing, "frame spacing", minimum=0)
        padding = _integer(self.padding, "atlas padding", minimum=0)
        if frame_count > MAX_SPRITESHEET_FRAMES:
            raise ValueError(f"spritesheet exports may contain at most {MAX_SPRITESHEET_FRAMES} frames")
        if columns == 0:
            columns = max(1, min(frame_count, math.ceil(math.sqrt(frame_count))))
        if columns > frame_count:
            raise ValueError("atlas columns must not exceed the frame count")
        rows = math.ceil(frame_count / columns)
        width = padding * 2 + columns * frame_width + max(0, columns - 1) * spacing
        height = padding * 2 + rows * frame_height + max(0, rows - 1) * spacing
        if width > MAX_SPRITESHEET_DIMENSION or height > MAX_SPRITESHEET_DIMENSION:
            raise ValueError(f"spritesheet dimensions may not exceed {MAX_SPRITESHEET_DIMENSION}")
        if width * height > MAX_SPRITESHEET_PIXELS:
            raise ValueError("spritesheet exceeds the pixel budget")
        object.__setattr__(self, "frame_width", frame_width)
        object.__setattr__(self, "frame_height", frame_height)
        object.__setattr__(self, "frame_count", frame_count)
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "spacing", spacing)
        object.__setattr__(self, "padding", padding)

    @property
    def rows(self) -> int:
        return math.ceil(self.frame_count / self.columns)

    @property
    def width(self) -> int:
        return self.padding * 2 + self.columns * self.frame_width + max(0, self.columns - 1) * self.spacing

    @property
    def height(self) -> int:
        return self.padding * 2 + self.rows * self.frame_height + max(0, self.rows - 1) * self.spacing

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def origin(self, index: int) -> tuple[int, int]:
        index = _integer(index, "frame index", minimum=0)
        if index >= self.frame_count:
            raise ValueError("frame index is outside the spritesheet")
        return (
            self.padding + (index % self.columns) * (self.frame_width + self.spacing),
            self.padding + (index // self.columns) * (self.frame_height + self.spacing),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "frame_count": self.frame_count,
            "columns": self.columns,
            "rows": self.rows,
            "spacing": self.spacing,
            "padding": self.padding,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class SpritesheetResult:
    """Composited atlas plus scene-frame metadata for runtime importers."""

    texture: PixelTexture
    layout: SpritesheetLayout
    scene_frames: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.scene_frames) != self.layout.frame_count:
            raise ValueError("scene-frame metadata must match the atlas frame count")
        if tuple(self.texture.pixels) == () or (self.texture.width, self.texture.height) != self.layout.size:
            raise ValueError("spritesheet texture dimensions must match its layout")

    def to_dict(self) -> dict[str, object]:
        frames = []
        for index, scene_frame in enumerate(self.scene_frames):
            x, y = self.layout.origin(index)
            frames.append(
                {
                    "index": index,
                    "scene_frame": scene_frame,
                    "x": x,
                    "y": y,
                    "width": self.layout.frame_width,
                    "height": self.layout.frame_height,
                }
            )
        return {
            "format": SPRITESHEET_FORMAT,
            "format_version": SPRITESHEET_FORMAT_VERSION,
            "width": self.layout.width,
            "height": self.layout.height,
            "layout": self.layout.to_dict(),
            "frames": frames,
        }


def compose_spritesheet(
    frames: Iterable[PixelTexture],
    *,
    scene_frames: Iterable[int] | None = None,
    columns: int = 0,
    spacing: int = 0,
    padding: int = 0,
) -> SpritesheetResult:
    """Pack equal-sized top-left-origin RGBA frames into a transparent atlas."""

    frames = tuple(frames)
    if not frames:
        raise ValueError("spritesheet composition requires at least one frame")
    if any(not isinstance(frame, PixelTexture) for frame in frames):
        raise TypeError("spritesheet frames must be PixelTexture values")
    frame_width = frames[0].width
    frame_height = frames[0].height
    if any((frame.width, frame.height) != (frame_width, frame_height) for frame in frames[1:]):
        raise ValueError("all spritesheet frames must have matching dimensions")
    if scene_frames is None:
        scene_frames = range(len(frames))
    scene_frames = tuple(_integer(frame, "scene frame") for frame in scene_frames)
    layout = SpritesheetLayout(
        frame_width,
        frame_height,
        len(frames),
        columns=columns,
        spacing=spacing,
        padding=padding,
    )
    if len(scene_frames) != layout.frame_count:
        raise ValueError("scene-frame metadata must match the number of frames")
    transparent = (0.0, 0.0, 0.0, 0.0)
    pixels = [transparent] * (layout.width * layout.height)
    for index, frame in enumerate(frames):
        origin_x, origin_y = layout.origin(index)
        for y in range(frame.height):
            destination = (origin_y + y) * layout.width + origin_x
            source = y * frame.width
            pixels[destination:destination + frame.width] = frame.pixels[source:source + frame.width]
    texture = PixelTexture(layout.width, layout.height, tuple(pixels))
    return SpritesheetResult(texture, layout, scene_frames)


__all__ = [
    "MAX_SPRITESHEET_DIMENSION",
    "MAX_SPRITESHEET_FRAMES",
    "MAX_SPRITESHEET_PIXELS",
    "SPRITESHEET_FORMAT",
    "SPRITESHEET_FORMAT_VERSION",
    "SpritesheetLayout",
    "SpritesheetResult",
    "compose_spritesheet",
    "sample_frame_range",
]
