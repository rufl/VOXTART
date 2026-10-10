# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Build the VoxTart Windows icon from the supplied voxel-cake concept."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZES = (16, 32, 48, 256)

ORANGE_DARK = (0xD9, 0x62, 0x25, 0xFF)
ORANGE = (0xF3, 0x8A, 0x32, 0xFF)
ORANGE_LIGHT = (0xFF, 0xB4, 0x5A, 0xFF)
NAVY = (0x18, 0x16, 0x58, 0xFF)
PURPLE = (0x6C, 0x20, 0xC9, 0xFF)
BLUE = (0x2B, 0x5C, 0xE8, 0xFF)
WHITE = (0xFF, 0xF8, 0xE6, 0xFF)
RED = (0xEF, 0x1F, 0x42, 0xFF)
RED_DARK = (0xB9, 0x0F, 0x35, 0xFF)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _write_png(path: Path, pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    height = len(pixels)
    width = len(pixels[0])
    rows = b"".join(b"\x00" + b"".join(bytes(pixel) for pixel in row) for row in pixels)
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    payload += _png_chunk(b"IDAT", zlib.compress(rows, 9))
    payload += _png_chunk(b"IEND", b"")
    path.write_bytes(payload)
    return payload


def _inside(point, polygon) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        crosses = (y1 > y) != (y2 > y)
        if crosses and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def _polygon(canvas, points, color) -> None:
    size = len(canvas)
    scaled = [(int(x * (size - 1)), int(y * (size - 1))) for x, y in points]
    left = max(0, min(x for x, _y in scaled))
    right = min(size - 1, max(x for x, _y in scaled))
    top = max(0, min(y for _x, y in scaled))
    bottom = min(size - 1, max(y for _x, y in scaled))
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if _inside((x, y), scaled):
                canvas[y][x] = color


def _ellipse(canvas, center, radius, color, points=48) -> None:
    cx, cy = center
    rx, ry = radius
    polygon = [
        (cx + math.cos(index * 2.0 * math.pi / points) * rx, cy + math.sin(index * 2.0 * math.pi / points) * ry)
        for index in range(points)
    ]
    _polygon(canvas, polygon, color)


def _cube(canvas, center, width, depth, height, top, left, right) -> None:
    cx, cy = center
    top_face = (
        (cx, cy - depth),
        (cx + width, cy - depth / 2),
        (cx, cy),
        (cx - width, cy - depth / 2),
    )
    left_face = (
        (cx - width, cy - depth / 2),
        (cx, cy),
        (cx, cy + height),
        (cx - width, cy + height - depth / 2),
    )
    right_face = (
        (cx, cy),
        (cx + width, cy - depth / 2),
        (cx + width, cy + height - depth / 2),
        (cx, cy + height),
    )
    _polygon(canvas, top_face, top)
    _polygon(canvas, left_face, left)
    _polygon(canvas, right_face, right)


def _icon(size: int) -> list[list[tuple[int, int, int, int]]]:
    canvas = [[(0, 0, 0, 0) for _x in range(size)] for _y in range(size)]
    _ellipse(canvas, (0.5, 0.54), (0.43, 0.36), (0x08, 0x0B, 0x28, 0xFF))
    _ellipse(canvas, (0.5, 0.47), (0.42, 0.33), ORANGE_DARK)
    _ellipse(canvas, (0.5, 0.43), (0.37, 0.27), ORANGE_LIGHT)
    _ellipse(canvas, (0.5, 0.43), (0.29, 0.2), NAVY)

    for center, color in (
        ((0.31, 0.35), BLUE),
        ((0.69, 0.35), PURPLE),
        ((0.3, 0.54), PURPLE),
        ((0.7, 0.54), BLUE),
        ((0.42, 0.55), BLUE),
        ((0.58, 0.54), PURPLE),
    ):
        _cube(canvas, center, 0.035, 0.03, 0.045, color, tuple(max(0, value - 35) for value in color[:3]) + (0xFF,), color)

    for center in ((0.26, 0.25), (0.74, 0.25), (0.25, 0.62), (0.75, 0.62)):
        _cube(canvas, center, 0.09, 0.05, 0.08, ORANGE_LIGHT, ORANGE_DARK, ORANGE)

    _cube(canvas, (0.5, 0.36), 0.105, 0.06, 0.105, WHITE, (0xD9, 0xCA, 0xAE, 0xFF), (0xF5, 0xE9, 0xD4, 0xFF))
    _cube(canvas, (0.5, 0.22), 0.055, 0.035, 0.07, RED, RED_DARK, (0xFF, 0x4A, 0x58, 0xFF))
    _cube(canvas, (0.69, 0.57), 0.045, 0.03, 0.06, RED, RED_DARK, (0xFF, 0x4A, 0x58, 0xFF))
    return canvas


def _write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, image in images:
        dimension = 0 if size >= 256 else size
        directory.extend(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(image), offset))
        offset += len(image)
    path.write_bytes(directory + b"".join(image for _size, image in images))


def _write_svg(path: Path) -> None:
    path.write_text(
        """<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 256 256\">\n"
        "  <title>VoxTart voxel cake</title>\n"
        "  <ellipse cx=\"128\" cy=\"142\" rx=\"108\" ry=\"92\" fill=\"#080b28\"/>\n"
        "  <ellipse cx=\"128\" cy=\"118\" rx=\"106\" ry=\"84\" fill=\"#d96225\"/>\n"
        "  <ellipse cx=\"128\" cy=\"110\" rx=\"94\" ry=\"69\" fill=\"#ffb45a\"/>\n"
        "  <ellipse cx=\"128\" cy=\"110\" rx=\"73\" ry=\"51\" fill=\"#181658\"/>\n"
        "  <path d=\"M128 60l27 15-27 15-27-15z\" fill=\"#fff8e6\"/>\n"
        "  <path d=\"M101 75l27 15v31l-27-15z\" fill=\"#d9caae\"/>\n"
        "  <path d=\"M155 75l-27 15v31l27-15z\" fill=\"#f5e9d4\"/>\n"
        "  <path d=\"M128 43l15 8-15 8-15-8z\" fill=\"#ef1f42\"/>\n"
        "  <path d=\"M113 51l15 8v17l-15-8z\" fill=\"#b90f35\"/>\n"
        "  <path d=\"M143 51l-15 8v17l15-8z\" fill=\"#ff4a58\"/>\n"
        "</svg>\n""",
        encoding="utf-8",
    )


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    images = []
    for size in SIZES:
        png = _write_png(ASSETS / f"voxtart_icon_{size}.png", _icon(size))
        images.append((size, png))
    _write_ico(ASSETS / "voxtart.ico", images)
    _write_svg(ASSETS / "voxtart_icon.svg")
    print(f"VOXTART_ICON_READY path={ASSETS / 'voxtart.ico'}")


if __name__ == "__main__":
    main()
