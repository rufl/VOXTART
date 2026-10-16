import bpy
import struct
from pathlib import Path

FRAME_DIR = Path(r"C:\Users\rafael\Downloads\voxtart_rotation_frames")
OUT = Path(r"C:\Users\rafael\Downloads\VOXTART_pixel_character_rotation.gif")
WIDTH = HEIGHT = 480
FRAME_COUNT = 24
MIN_CODE_SIZE = 8
PALETTE_SIZE = 1 << MIN_CODE_SIZE
LZW_CHUNK_SIZE = PALETTE_SIZE - 3
PIXEL_COUNT = WIDTH * HEIGHT

frames = sorted(FRAME_DIR.glob("frame_*.png"))
expected_names = [f"frame_{index:03d}.png" for index in range(FRAME_COUNT)]
actual_names = [path.name for path in frames]
if actual_names != expected_names:
    raise RuntimeError(
        f"expected {expected_names!r} in {FRAME_DIR}, got {actual_names!r}"
    )


def srgb_byte(value):
    value = min(1.0, max(0.0, value))
    if value <= 0.0031308:
        value *= 12.92
    else:
        value = 1.055 * (value ** (1.0 / 2.4)) - 0.055
    return min(255, max(0, int(value * 255.0 + 0.5)))


def pixels(path):
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        if tuple(image.size) != (WIDTH, HEIGHT):
            raise RuntimeError(f"{path} is {tuple(image.size)}, expected {(WIDTH, HEIGHT)}")
        raw = tuple(image.pixels)
        if len(raw) != PIXEL_COUNT * 4:
            raise RuntimeError(f"{path} has {len(raw) // 4} pixels, expected {PIXEL_COUNT}")
        indexed = bytearray(PIXEL_COUNT)
        for index in range(PIXEL_COUNT):
            red, green, blue = raw[index * 4:index * 4 + 3]
            luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
            indexed[index] = srgb_byte(luminance)
        return bytes(indexed)
    finally:
        bpy.data.images.remove(image)


def lzw(data):
    clear = 1 << MIN_CODE_SIZE
    end = clear + 1
    width = MIN_CODE_SIZE + 1
    output = []
    current = 0
    bits = 0

    def put(value):
        nonlocal current, bits
        current |= value << bits
        bits += width
        while bits >= 8:
            output.append(current & 0xFF)
            current >>= 8
            bits -= 8

    for start in range(0, len(data), LZW_CHUNK_SIZE):
        chunk = data[start:start + LZW_CHUNK_SIZE]
        put(clear)
        table = {bytes([index]): index for index in range(clear)}
        next_code = clear + 2
        sequence = bytes([chunk[0]])
        for value in chunk[1:]:
            candidate = sequence + bytes([value])
            if candidate in table:
                sequence = candidate
                continue
            put(table[sequence])
            table[candidate] = next_code
            next_code += 1
            if next_code >= (1 << width) - 1:
                raise RuntimeError("GIF LZW chunk exceeded its fixed code width")
            sequence = bytes([value])
        put(table[sequence])
    put(end)
    if bits:
        output.append(current & 0xFF)
    return bytes(output)


def sub_blocks(data):
    output = bytearray()
    for start in range(0, len(data), 255):
        chunk = data[start:start + 255]
        output.append(len(chunk))
        output.extend(chunk)
    output.append(0)
    return bytes(output)


def read_sub_blocks(data, position):
    chunks = []
    while True:
        if position >= len(data):
            raise RuntimeError("GIF ended inside a sub-block stream")
        size = data[position]
        position += 1
        if size == 0:
            return b"".join(chunks), position
        end = position + size
        if end > len(data):
            raise RuntimeError("GIF ended inside a sub-block")
        chunks.append(data[position:end])
        position = end


def decode_lzw(payload, minimum_code_size, expected_pixels):
    clear = 1 << minimum_code_size
    end = clear + 1
    position = 0
    width = minimum_code_size + 1
    next_code = clear + 2
    table = {index: bytes([index]) for index in range(clear)}
    previous = None
    decoded = 0

    def read_code():
        nonlocal position
        if position + width > len(payload) * 8:
            raise RuntimeError("GIF LZW stream ended before its end code")
        value = int.from_bytes(
            payload[position // 8:(position + width + 7) // 8],
            "little",
        )
        value >>= position % 8
        position += width
        return value & ((1 << width) - 1)

    while True:
        code = read_code()
        if code == clear:
            width = minimum_code_size + 1
            next_code = clear + 2
            table = {index: bytes([index]) for index in range(clear)}
            previous = None
            continue
        if code == end:
            break
        if previous is None:
            if code not in table:
                raise RuntimeError(f"GIF LZW first code {code} is invalid")
            entry = table[code]
        elif code in table:
            entry = table[code]
        elif code == next_code:
            entry = previous + previous[:1]
        else:
            raise RuntimeError(f"GIF LZW code {code} is invalid at bit {position}")
        decoded += len(entry)
        if decoded > expected_pixels:
            raise RuntimeError(f"GIF LZW decoded more than {expected_pixels} pixels")
        if previous is not None and next_code < 4096:
            table[next_code] = previous + entry[:1]
            next_code += 1
            if next_code == (1 << width) - 1 and width < 12:
                width += 1
        previous = entry
    if decoded != expected_pixels:
        raise RuntimeError(f"GIF LZW decoded {decoded} pixels, expected {expected_pixels}")


def validate_gif(data, expected_frames):
    if len(data) < 13 or data[:6] not in (b"GIF87a", b"GIF89a"):
        raise RuntimeError("GIF header is invalid")
    width, height = struct.unpack_from("<HH", data, 6)
    if (width, height) != (WIDTH, HEIGHT):
        raise RuntimeError(f"GIF canvas is {(width, height)}, expected {(WIDTH, HEIGHT)}")
    position = 13
    packed = data[10]
    if packed & 0x80:
        position += 3 * (1 << ((packed & 7) + 1))
    frame_count = 0
    while position < len(data):
        introducer = data[position]
        position += 1
        if introducer == 0x3B:
            if position != len(data):
                raise RuntimeError("GIF has data after its trailer")
            break
        if introducer == 0x21:
            if position >= len(data):
                raise RuntimeError("GIF ended inside an extension")
            label = data[position]
            position += 1
            if label == 0xF9:
                if position >= len(data) or data[position] != 4:
                    raise RuntimeError("GIF graphics control extension is invalid")
                position += 1 + 4
                if position >= len(data) or data[position] != 0:
                    raise RuntimeError("GIF graphics control extension lacks terminator")
                position += 1
            else:
                _, position = read_sub_blocks(data, position)
            continue
        if introducer != 0x2C:
            raise RuntimeError(f"GIF block introducer 0x{introducer:02x} is invalid")
        if position + 9 > len(data):
            raise RuntimeError("GIF ended inside an image descriptor")
        left, top, frame_width, frame_height, packed = struct.unpack_from(
            "<HHHHB", data, position
        )
        del left, top
        position += 9
        if (frame_width, frame_height) != (WIDTH, HEIGHT):
            raise RuntimeError(
                f"GIF frame is {(frame_width, frame_height)}, expected {(WIDTH, HEIGHT)}"
            )
        if packed & 0x80:
            position += 3 * (1 << ((packed & 7) + 1))
        if position >= len(data):
            raise RuntimeError("GIF image lacks an LZW minimum code size")
        minimum_code_size = data[position]
        position += 1
        compressed, position = read_sub_blocks(data, position)
        decode_lzw(compressed, minimum_code_size, PIXEL_COUNT)
        frame_count += 1
    if position > len(data) or data[-1:] != b";":
        raise RuntimeError("GIF has no valid trailer")
    if frame_count != expected_frames:
        raise RuntimeError(f"GIF has {frame_count} frames, expected {expected_frames}")


palette = bytearray()
for level in range(PALETTE_SIZE):
    palette.extend((level, level, level))

gif = bytearray(b"GIF89a")
gif.extend((0xF7, 0, 0))
gif.extend(b"!\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00")
for path in frames:
    indexed = pixels(path)
    compressed = lzw(indexed)
    decode_lzw(compressed, MIN_CODE_SIZE, len(indexed))
    gif.extend(b"!\xF9\x04\x08\x08\x00\x00\x00")
    gif.extend(b",\x00\x00\x00\x00" + struct.pack("<HH", WIDTH, HEIGHT) + b"\x00")
    gif.extend(bytes([MIN_CODE_SIZE]) + sub_blocks(compressed))
gif.extend(b";")

temporary = OUT.with_name(OUT.name + ".tmp")
temporary.write_bytes(gif)
try:
    validate_gif(temporary.read_bytes(), len(frames))
    temporary.replace(OUT)
finally:
    if temporary.exists():
        temporary.unlink()
print(f"VOXTART_GIF frames={len(frames)} bytes={OUT.stat().st_size} output={OUT}")
