# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Deterministic RGBA image intake for orthographic voxel reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Iterable, Literal, Sequence

from .provenance import SourceIdentity
from .reconstruct import OrthographicView, Pixel


BackgroundMode = Literal["ALPHA", "NONE", "CORNER", "EXPLICIT"]
DEFAULT_EMPTY_PALETTE_COLOR: Color = (0.0, 0.0, 0.0, 1.0)


def _color(value: Iterable[float]) -> Color:
    result = tuple(float(item) for item in value)
    if len(result) != 4 or any(not isfinite(item) or item < 0.0 or item > 1.0 for item in result):
        raise ValueError(f"image colors must be four finite values in [0, 1]: {value!r}")
    return tuple(round(item * 255.0) / 255.0 for item in result)  # type: ignore[return-value]


def _rgba8(color: Color) -> tuple[int, int, int, int]:
    return tuple(int(round(channel * 255.0)) for channel in color)  # type: ignore[return-value]


def _distance_squared(first: Color, second: Color) -> float:
    return sum((left - right) ** 2 for left, right in zip(first, second))


@dataclass(frozen=True, slots=True)
class ImageFrame:
    """Nearest-neighbor-preserved RGBA pixels before palette indexing."""

    name: str
    pixels: tuple[tuple[Color, ...], ...]

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("image name must not be empty")
        rows = tuple(tuple(_color(pixel) for pixel in row) for row in self.pixels)
        if not rows or not rows[0]:
            raise ValueError("image pixels must contain at least one cell")
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("image pixel rows must have equal widths")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "pixels", rows)

    @classmethod
    def from_rows(
        cls,
        name: str,
        rows: Sequence[Sequence[Iterable[float]]],
    ) -> "ImageFrame":
        return cls(name=name, pixels=tuple(tuple(pixel for pixel in row) for row in rows))

    @property
    def width(self) -> int:
        return len(self.pixels[0])

    @property
    def height(self) -> int:
        return len(self.pixels)

    @property
    def source_digest(self) -> str:
        digest = sha256()
        digest.update(self.width.to_bytes(4, "big"))
        digest.update(self.height.to_bytes(4, "big"))
        for row in self.pixels:
            for pixel in row:
                digest.update(bytes(_rgba8(pixel)))
        return digest.hexdigest()

    def source_identity(
        self,
        *,
        source_id: str | None = None,
        frame_id: str = "",
        pose_id: str = "",
        camera_id: str = "",
        revision: str = "",
    ) -> SourceIdentity:
        return SourceIdentity(
            source_id or self.name,
            self.source_digest,
            frame_id=frame_id,
            pose_id=pose_id,
            camera_id=camera_id,
            revision=revision,
        )


@dataclass(frozen=True, slots=True)
class PaletteIntakeSettings:
    """Background and palette extraction policy for one image."""

    background_mode: BackgroundMode = "ALPHA"
    alpha_threshold: float = 0.0
    background_color: Color | None = None
    background_tolerance: float = 0.0
    max_colors: int = 256

    def __post_init__(self) -> None:
        mode = str(self.background_mode).upper()
        if mode not in ("ALPHA", "NONE", "CORNER", "EXPLICIT"):
            raise ValueError("background mode must be ALPHA, NONE, CORNER, or EXPLICIT")
        for name, value in (
            ("alpha_threshold", self.alpha_threshold),
            ("background_tolerance", self.background_tolerance),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if isinstance(self.max_colors, bool) or not isinstance(self.max_colors, int) or self.max_colors <= 0:
            raise ValueError("max_colors must be a positive integer")
        background = None if self.background_color is None else _color(self.background_color)
        if mode == "EXPLICIT" and background is None:
            raise ValueError("EXPLICIT background mode requires background_color")
        object.__setattr__(self, "background_mode", mode)
        object.__setattr__(self, "alpha_threshold", float(self.alpha_threshold))
        object.__setattr__(self, "background_tolerance", float(self.background_tolerance))
        object.__setattr__(self, "background_color", background)


@dataclass(frozen=True, slots=True)
class IndexedImage:
    """Palette-indexed image ready to become an orthographic view."""

    name: str
    pixels: tuple[tuple[Pixel, ...], ...]
    palette: tuple[Color, ...]
    source_digest: str
    background_color: Color | None
    settings: PaletteIntakeSettings

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("indexed image name must not be empty")
        rows = tuple(tuple(pixel for pixel in row) for row in self.pixels)
        if not rows or not rows[0]:
            raise ValueError("indexed image pixels must contain at least one cell")
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("indexed image pixel rows must have equal widths")
        normalized_palette = tuple(_color(color) for color in self.palette)
        if not normalized_palette:
            raise ValueError("indexed images require at least one palette entry")
        for row in rows:
            for pixel in row:
                if pixel is not None and (isinstance(pixel, bool) or not isinstance(pixel, int) or not 0 <= pixel < len(normalized_palette)):
                    raise ValueError(f"indexed image pixel is outside palette: {pixel!r}")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "pixels", rows)
        object.__setattr__(self, "palette", normalized_palette)
        object.__setattr__(self, "background_color", None if self.background_color is None else _color(self.background_color))

    @property
    def width(self) -> int:
        return len(self.pixels[0])

    @property
    def height(self) -> int:
        return len(self.pixels)

    def source_identity(
        self,
        *,
        source_id: str | None = None,
        frame_id: str = "",
        pose_id: str = "",
        camera_id: str = "",
        revision: str = "",
    ) -> SourceIdentity:
        return SourceIdentity(
            source_id or self.name,
            self.source_digest,
            frame_id=frame_id,
            pose_id=pose_id,
            camera_id=camera_id,
            revision=revision,
        )

    def to_view(
        self,
        *,
        axis: str = "Z",
        direction: int = 1,
        origin: Cell = (0, 0, 0),
        depth: int = 1,
        priority: int = 0,
        name: str | None = None,
        source_identity: SourceIdentity | None = None,
    ) -> OrthographicView:
        """Apply explicit calibration without resampling the source image."""

        return OrthographicView.from_rows(
            name or self.name,
            self.pixels,
            axis=axis,
            direction=direction,
            origin=origin,
            depth=depth,
            priority=priority,
            source_identity=source_identity,
        )

    def provenance(self) -> dict[str, str]:
        return {
            "image_name": self.name,
            "image_width": str(self.width),
            "image_height": str(self.height),
            "image_sha256": self.source_digest,
            "background_mode": self.settings.background_mode,
            "alpha_threshold": f"{self.settings.alpha_threshold:.6f}",
            "background_tolerance": f"{self.settings.background_tolerance:.6f}",
            "palette_size": str(len(self.palette)),
        }


def _background_color(frame: ImageFrame, settings: PaletteIntakeSettings) -> Color | None:
    if settings.background_mode == "CORNER":
        return frame.pixels[0][0]
    if settings.background_mode == "EXPLICIT":
        return settings.background_color
    return None


def _is_background(pixel: Color, background: Color | None, settings: PaletteIntakeSettings) -> bool:
    if settings.background_mode != "NONE" and pixel[3] <= settings.alpha_threshold:
        return True
    return background is not None and _distance_squared(pixel, background) <= settings.background_tolerance ** 2


def _extract_palette_counts(
    frames: Sequence[ImageFrame],
    settings: PaletteIntakeSettings,
) -> tuple[Color, ...]:
    counts: dict[Color, int] = {}
    for frame in frames:
        background = _background_color(frame, settings)
        for row in frame.pixels:
            for pixel in row:
                if _is_background(pixel, background, settings):
                    continue
                counts[pixel] = counts.get(pixel, 0) + 1
    if not counts:
        return (DEFAULT_EMPTY_PALETTE_COLOR,)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(color for color, _count in ordered[: settings.max_colors])


def extract_palette(
    frame: ImageFrame,
    settings: PaletteIntakeSettings = PaletteIntakeSettings(),
) -> tuple[Color, ...]:
    """Extract the most frequent foreground colors with deterministic ties."""

    return _extract_palette_counts((frame,), settings)


def extract_palette_frames(
    frames: Sequence[ImageFrame],
    settings: PaletteIntakeSettings = PaletteIntakeSettings(),
) -> tuple[Color, ...]:
    """Extract one shared deterministic palette from multiple source views."""

    normalized_frames = tuple(frames)
    if not normalized_frames:
        raise ValueError("at least one image frame is required")
    if any(not isinstance(frame, ImageFrame) for frame in normalized_frames):
        raise TypeError("frames must contain ImageFrame values")
    return _extract_palette_counts(normalized_frames, settings)


def _normalize_palette(palette: Sequence[Color]) -> tuple[Color, ...]:
    normalized = tuple(_color(color) for color in palette)
    if not normalized:
        raise ValueError("palette must contain at least one color")
    return normalized


def _nearest_palette_index(pixel: Color, normalized_palette: tuple[Color, ...]) -> int:
    return min(
        range(len(normalized_palette)),
        key=lambda index: (_distance_squared(pixel, normalized_palette[index]), index),
    )


def nearest_palette_index(pixel: Color, palette: Sequence[Color]) -> int:
    """Choose a stable nearest palette entry, preferring the lowest index."""

    return _nearest_palette_index(_color(pixel), _normalize_palette(palette))


def index_image(
    frame: ImageFrame,
    settings: PaletteIntakeSettings = PaletteIntakeSettings(),
    *,
    palette: Sequence[Color] | None = None,
) -> IndexedImage:
    """Convert RGBA pixels into deterministic palette indices and background."""

    selected_palette = _normalize_palette(palette) if palette is not None else extract_palette(frame, settings)
    background = _background_color(frame, settings)
    indexed_rows: list[tuple[Pixel, ...]] = []
    for row in frame.pixels:
        indexed_rows.append(
            tuple(
                None
                if _is_background(pixel, background, settings)
                else _nearest_palette_index(pixel, selected_palette)
                for pixel in row
            )
        )
    return IndexedImage(
        name=frame.name,
        pixels=tuple(indexed_rows),
        palette=selected_palette,
        source_digest=frame.source_digest,
        background_color=background,
        settings=settings,
    )


__all__ = [
    "BackgroundMode",
    "DEFAULT_EMPTY_PALETTE_COLOR",
    "ImageFrame",
    "IndexedImage",
    "PaletteIntakeSettings",
    "extract_palette",
    "extract_palette_frames",
    "nearest_palette_index",
]
