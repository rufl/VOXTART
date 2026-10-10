# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Patch the bundled Blender PE before any native window can be created."""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "build" / "blender-5.2.2" / "blender-5.2.2-windows-x64"
BLENDER_PATH = RUNTIME_DIR / ("blender-core.exe" if (RUNTIME_DIR / "blender-core.exe").is_file() else "blender.exe")
BACKUP_PATH = BLENDER_PATH.with_name(f"{BLENDER_PATH.name}.voxtart-original")
ASSET_PATHS = (
    ROOT / "assets" / "voxtart_icon_16.png",
    ROOT / "assets" / "voxtart_icon_32.png",
    ROOT / "assets" / "voxtart_icon_48.png",
    ROOT / "assets" / "voxtart_icon_256.png",
)
TITLE_OLD = b" - Blender {}"
TITLE_NEW = b" - VoxTart {}"
TITLE_NATIVE = b"\0" + TITLE_NEW[1:]
UNSAVED_OLD = b"(Unsaved)"
UNSAVED_NEW = b"VoxTart\0\0"


class PEImage:
    def __init__(self, data: bytearray):
        self.data = data
        if data[:2] != b"MZ":
            raise ValueError("not a PE image")
        self.pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[self.pe_offset : self.pe_offset + 4] != b"PE\0\0":
            raise ValueError("invalid PE signature")
        coff = self.pe_offset + 4
        self.section_count = struct.unpack_from("<H", data, coff + 2)[0]
        optional_size = struct.unpack_from("<H", data, coff + 16)[0]
        optional = coff + 20
        magic = struct.unpack_from("<H", data, optional)[0]
        directory_offset = 112 if magic == 0x20B else 96
        self.resource_rva, self.resource_size = struct.unpack_from(
            "<II", data, optional + directory_offset + 16
        )
        section_table = optional + optional_size
        self.sections = []
        for index in range(self.section_count):
            section = section_table + index * 40
            virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
                "<IIII", data, section + 8
            )
            self.sections.append((virtual_address, virtual_size, raw_pointer, raw_size))
        self.resource_file_offset = self.rva_to_file_offset(self.resource_rva)

    def rva_to_file_offset(self, rva: int) -> int:
        for virtual_address, virtual_size, raw_pointer, raw_size in self.sections:
            span = max(virtual_size, raw_size)
            if virtual_address <= rva < virtual_address + span:
                return raw_pointer + rva - virtual_address
        raise ValueError(f"RVA is outside PE sections: {rva:#x}")

    def u16(self, offset: int) -> int:
        return struct.unpack_from("<H", self.data, offset)[0]

    def u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.data, offset)[0]

    def directory(self, relative_offset: int) -> list[tuple[int | str, bool, int]]:
        offset = self.resource_file_offset + relative_offset
        named = self.u16(offset + 12)
        ids = self.u16(offset + 14)
        entries = []
        for index in range(named + ids):
            entry = offset + 16 + index * 8
            name = self.u32(entry)
            target = self.u32(entry + 4)
            if name & 0x80000000:
                value: int | str = "named"
            else:
                value = name
            entries.append((value, bool(target & 0x80000000), target & 0x7FFFFFFF))
        return entries

    def resource_leaves(self, resource_type: int) -> list[tuple[int | str, int, int, int]]:
        leaves = []
        type_entry = next(
            (entry for entry in self.directory(0) if entry[0] == resource_type),
            None,
        )
        if type_entry is None or not type_entry[1]:
            raise ValueError(f"resource type not found: {resource_type}")
        for resource_id, id_is_directory, id_offset in self.directory(type_entry[2]):
            if not id_is_directory:
                continue
            for _language, language_is_directory, language_offset in self.directory(id_offset):
                if language_is_directory:
                    continue
                entry_offset = self.resource_file_offset + language_offset
                data_rva, size = struct.unpack_from("<II", self.data, entry_offset)
                leaves.append((resource_id, entry_offset, self.rva_to_file_offset(data_rva), size))
        return leaves


def build_group_icon(images: tuple[bytes, ...]) -> bytes:
    group = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for index, image in enumerate(images):
        size = (16, 32, 48, 256)[index]
        dimension = 0 if size == 256 else size
        group.extend(struct.pack("<BBBBHHIH", dimension, dimension, 0, 0, 1, 32, len(image), index + 1))
    return bytes(group)


def patch_runtime() -> bool:
    if not BLENDER_PATH.is_file():
        raise FileNotFoundError(BLENDER_PATH)
    if not all(path.is_file() for path in ASSET_PATHS):
        missing = [str(path) for path in ASSET_PATHS if not path.is_file()]
        raise FileNotFoundError(", ".join(missing))

    if not BACKUP_PATH.is_file():
        shutil.copy2(BLENDER_PATH, BACKUP_PATH)
    data = bytearray(BLENDER_PATH.read_bytes())
    title_changed = False
    title_offset = data.find(TITLE_OLD)
    if title_offset >= 0:
        data[title_offset : title_offset + len(TITLE_OLD)] = TITLE_NEW
        title_changed = True
    elif TITLE_NEW not in data and TITLE_NATIVE not in data:
        raise ValueError("Blender title format string was not found")

    native_title_offset = data.find(TITLE_NEW)
    if native_title_offset >= 0:
        data[native_title_offset : native_title_offset + len(TITLE_NEW)] = TITLE_NATIVE
        title_changed = True

    unsaved_offset = data.find(UNSAVED_OLD)
    if unsaved_offset >= 0:
        data[unsaved_offset : unsaved_offset + len(UNSAVED_OLD)] = UNSAVED_NEW
        title_changed = True
    elif UNSAVED_NEW not in data:
        raise ValueError("Blender unsaved title string was not found")

    pe = PEImage(data)
    icon_images = tuple(path.read_bytes() for path in ASSET_PATHS)
    icon_leaves = {
        int(resource_id): leaf
        for resource_id, *leaf in pe.resource_leaves(3)
        if isinstance(resource_id, int)
    }
    icon_updates = []
    for resource_id, image in zip(range(1, 5), icon_images):
        if resource_id not in icon_leaves:
            raise ValueError(f"Blender icon resource is missing ID {resource_id}")
        entry_offset, file_offset, capacity = icon_leaves[resource_id]
        if len(image) > capacity:
            raise ValueError(f"icon resource {resource_id} is too small")
        current = bytes(data[file_offset : file_offset + len(image)])
        if capacity != len(image) or current != image:
            icon_updates.append((entry_offset, file_offset, capacity, image))

    group = build_group_icon(icon_images)
    group_updates = []
    for _resource_id, entry_offset, file_offset, capacity in pe.resource_leaves(14):
        if len(group) > capacity:
            raise ValueError("Blender group icon resource is too small")
        current = bytes(data[file_offset : file_offset + len(group)])
        if capacity != len(group) or current != group:
            group_updates.append((entry_offset, file_offset, capacity, group))

    if not title_changed and not icon_updates and not group_updates:
        return False
    for entry_offset, file_offset, capacity, image in icon_updates + group_updates:
        data[file_offset : file_offset + len(image)] = image
        data[file_offset + len(image) : file_offset + capacity] = b"\0" * (capacity - len(image))
        struct.pack_into("<I", data, entry_offset + 4, len(image))
    BLENDER_PATH.write_bytes(data)
    return True


def main() -> int:
    changed = patch_runtime()
    print(f"VOXTART_RUNTIME_BRANDED path={BLENDER_PATH} title=True icon=True changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
