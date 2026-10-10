# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Run the system-Python VOXTART suite and print line coverage.

This is intentionally implemented with ``sys.settrace`` instead of requiring
third-party ``coverage`` or ``pytest`` packages. It reports executable,
non-comment source lines in the deterministic core modules exercised by
``voxtart_core_test.py``. Blender-facing modules require the native runner.
"""

from __future__ import annotations

import dis
import json
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "scripts" / "addons_core" / "voxtart"
TEST_FILE = Path(__file__).with_name("voxtart_core_test.py")
TRACKED_MODULES = {
    "asset_placer.py",
    "blob.py",
    "blob_gallery.py",
    "character_editor.py",
    "blockbench_formats.py",
    "edit.py",
    "game_rig.py",
    "io.py",
    "kitbash.py",
    "layers.py",
    "mcp_protocol.py",
    "mesh_optimize.py",
    "meshing.py",
    "minecraft_formats.py",
    "minecraft_nbt.py",
    "optimize.py",
    "optimizer.py",
    "palette.py",
    "palette_reduce.py",
    "scene_hierarchy.py",
    "spritesheet.py",
    "stacking.py",
    "voxel.py",
    "voxel_formats.py",
    "voxelizer.py",
    "volume_ops.py",
    "tiler.py",
    "pick.py",
}


def _source_lines(path: Path) -> set[int]:
    physical = {
        number
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if line.strip() and not line.lstrip().startswith("#")
    }
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    traceable: set[int] = set()
    pending = [code]
    while pending:
        current = pending.pop()
        traceable.update(line for _offset, line in dis.findlinestarts(current) if line is not None)
        pending.extend(constant for constant in current.co_consts if isinstance(constant, type(code)))
    return physical & traceable


def main() -> int:
    all_missing = "--all-missing" in sys.argv
    executed: dict[str, set[int]] = {name: set() for name in TRACKED_MODULES}
    source_by_name = {name: SOURCE_ROOT / name for name in TRACKED_MODULES}
    source_paths = {str(path.resolve()): name for name, path in source_by_name.items()}

    def trace(frame, event, _arg):
        if event == "line":
            name = source_paths.get(str(Path(frame.f_code.co_filename).resolve()))
            if name is not None:
                executed[name].add(frame.f_lineno)
        return trace

    sys.path.insert(0, str(ROOT / "scripts" / "addons_core"))
    original_argv = sys.argv
    sys.argv = [str(TEST_FILE)]
    sys.settrace(trace)
    exit_code = 0
    try:
        runpy.run_path(str(TEST_FILE), run_name="__main__")
    except SystemExit as error:
        exit_code = int(error.code or 0)
    finally:
        sys.settrace(None)
        sys.argv = original_argv

    total_lines = total_hits = 0
    files = []
    for name in sorted(TRACKED_MODULES):
        source = _source_lines(source_by_name[name])
        hits = executed[name] & source
        total_lines += len(source)
        total_hits += len(hits)
        percent = 100.0 * len(hits) / len(source) if source else 100.0
        missing = sorted(source - hits)
        missing_text = ", ".join(str(line) for line in (missing if all_missing else missing[:12]))
        if len(missing) > 12 and not all_missing:
            missing_text += ", ..."
        print(f"{name:28} {len(hits):4}/{len(source):4} {percent:6.2f}%" + (f" missing {missing_text}" if missing_text else ""))
        files.append({"file": name, "hits": len(hits), "source": len(source), "percent": round(percent, 3)})
    overall = 100.0 * total_hits / total_lines if total_lines else 100.0
    print(f"VOXTART_CORE_LINE_COVERAGE {total_hits}/{total_lines} {overall:.2f}%")
    output_path = os.environ.get("VOXTART_COVERAGE_OUTPUT")
    if output_path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "scope": "core",
                    "hits": total_hits,
                    "source": total_lines,
                    "percent": round(overall, 3),
                    "files": files,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
