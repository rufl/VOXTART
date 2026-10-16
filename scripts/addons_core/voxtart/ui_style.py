# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Small shared typography helpers for the custom VOXTART overlays."""

from __future__ import annotations

from pathlib import Path

import blf


_FONT_ATTEMPTED = False
_FONT_ID = 0
_FONT_PATH: Path | None = None
_UI_SCALE_CACHE: float | None = None

# Affinity-inspired neutral chrome: near-black surfaces, cool slate elevation,
# one calm blue action color, and restrained semantic feedback colors.
THEME = {
    "canvas": (0.055, 0.06, 0.07, 0.98),
    "chrome": (0.075, 0.08, 0.09, 0.99),
    "panel": (0.105, 0.115, 0.135, 0.98),
    "panel_raised": (0.145, 0.155, 0.18, 0.98),
    "panel_hover": (0.18, 0.235, 0.31, 0.98),
    "border": (0.24, 0.26, 0.30, 0.98),
    "border_subtle": (0.15, 0.17, 0.20, 0.98),
    "text": (0.93, 0.94, 0.96, 1.0),
    "text_muted": (0.62, 0.66, 0.72, 1.0),
    "text_dim": (0.45, 0.50, 0.57, 1.0),
    "accent": (0.32, 0.58, 0.88, 1.0),
    "accent_hover": (0.42, 0.69, 0.96, 1.0),
    "accent_fill": (0.13, 0.28, 0.46, 0.98),
    "accent_soft": (0.10, 0.18, 0.28, 0.98),
    "warning_fill": (0.25, 0.18, 0.08, 0.98),
    "success": (0.32, 0.72, 0.52, 1.0),
    "success_fill": (0.06, 0.15, 0.12, 0.98),
    "warning": (0.90, 0.62, 0.27, 1.0),
    "danger": (0.88, 0.34, 0.32, 1.0),
    "danger_fill": (0.25, 0.09, 0.09, 0.98),
}


def ui_scale() -> float:
    """Return a comfortable scale for custom UI drawn in raw region pixels.

    Blender's native widgets follow the Windows display scale, but POST_PIXEL
    overlays do not. Cap the boost so a very high-DPI display stays usable
    without making the compact layout overflow.
    """
    global _UI_SCALE_CACHE
    if _UI_SCALE_CACHE is not None:
        return _UI_SCALE_CACHE
    factor = 1.0
    try:
        import ctypes

        factor = float(ctypes.windll.shcore.GetScaleFactorForDevice(0)) / 100.0
    except (AttributeError, OSError, RuntimeError, TypeError):
        try:
            import bpy

            factor = float(getattr(bpy.context.preferences.system, "ui_scale", 1.0))
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
            factor = 1.0
    _UI_SCALE_CACHE = max(1.0, min(1.35, factor))
    return _UI_SCALE_CACHE


def scale(value: float) -> float:
    return float(value) * ui_scale()


def _font_candidates():
    root = Path(__file__).resolve().parents[3]
    yield root / "build" / "blender-5.2.2" / "blender-5.2.2-windows-x64" / "5.2" / "datafiles" / "fonts" / "Inter.woff2"
    try:
        import bpy

        binary = Path(str(bpy.app.binary_path)).resolve()
        yield binary.parent / "5.2" / "datafiles" / "fonts" / "Inter.woff2"
    except (ImportError, AttributeError, RuntimeError, TypeError):
        return


def font_id() -> int:
    """Return the bundled Inter font when the packaged runtime exposes it."""
    global _FONT_ATTEMPTED, _FONT_ID, _FONT_PATH
    if _FONT_ATTEMPTED:
        return _FONT_ID
    _FONT_ATTEMPTED = True
    seen = set()
    for candidate in _font_candidates():
        candidate = candidate.resolve()
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            _FONT_ID = int(blf.load(str(candidate)))
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        _FONT_PATH = candidate
        break
    return _FONT_ID


def font_path() -> str | None:
    """Return the loaded font path, or ``None`` when Blender's default is used."""
    font_id()
    return str(_FONT_PATH) if _FONT_PATH is not None else None


def draw_text(text: str, x: float, y: float, size: float, color) -> None:
    current = font_id()
    blf.position(current, x, y, 0)
    blf.size(current, scale(size))
    blf.color(current, *color)
    blf.draw(current, text)


def text_width(text: str, size: float) -> float:
    current = font_id()
    blf.size(current, scale(size))
    return blf.dimensions(current, text)[0]


def wrap_text(text: str, size: float, max_width: float) -> tuple[str, ...]:
    """Wrap short UI copy at word boundaries without splitting words."""
    words = str(text).split()
    if not words:
        return ()
    lines = []
    line = words[0]
    for word in words[1:]:
        candidate = f"{line} {word}"
        if text_width(candidate, size) <= max_width:
            line = candidate
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return tuple(lines)


__all__ = ["THEME", "draw_text", "font_id", "font_path", "scale", "text_width", "ui_scale", "wrap_text"]
