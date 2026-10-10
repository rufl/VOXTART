# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Run the Blender-native VOXTART suite and print UI line coverage."""

from __future__ import annotations

import dis
import json
import os
import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "scripts" / "addons_core" / "voxtart"
TEST_FILE = Path(__file__).with_name("voxtart_blender_test.py")


def _source_lines(path: Path) -> set[int]:
    text = path.read_text(encoding="utf-8")
    physical = {
        number
        for number, line in enumerate(text.splitlines(), 1)
        if line.strip() and not line.lstrip().startswith("#")
    }
    code = compile(text, str(path), "exec")
    traceable: set[int] = set()
    pending = [code]
    while pending:
        current = pending.pop()
        traceable.update(line for _offset, line in dis.findlinestarts(current) if line is not None)
        pending.extend(constant for constant in current.co_consts if isinstance(constant, type(code)))
    return physical & traceable


def main() -> int:
    tracked = {
        path.name: path
        for path in SOURCE_ROOT.glob("*.py")
        if path.name == "__init__.py" or "import bpy" in path.read_text(encoding="utf-8")
    }
    executed = {name: set() for name in tracked}
    source_paths = {str(path.resolve()): name for name, path in tracked.items()}

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
    for name in sorted(tracked):
        source = _source_lines(tracked[name])
        hits = executed[name] & source
        total_lines += len(source)
        total_hits += len(hits)
        missing = sorted(source - hits)
        percent = 100.0 * len(hits) / len(source) if source else 100.0
        print(f"{name:28} {len(hits):4}/{len(source):4} {percent:6.2f}% missing {missing[:8]}")
        files.append({"file": name, "hits": len(hits), "source": len(source), "percent": round(percent, 3)})
    overall = 100.0 * total_hits / total_lines if total_lines else 100.0
    print(f"VOXTART_BLENDER_LINE_COVERAGE {total_hits}/{total_lines} {overall:.2f}%")
    output_path = os.environ.get("VOXTART_COVERAGE_OUTPUT")
    if output_path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "scope": "blender",
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
