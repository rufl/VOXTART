# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Validate VOXTART release evidence produced by the CI probes.

Usage::

    python tools/validate_voxtart_evidence.py core voxtart-coverage/core.json
    python tools/validate_voxtart_evidence.py blender voxtart-blender-artifacts
    python tools/validate_voxtart_evidence.py windows voxtart-e2e-artifacts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class EvidenceError(ValueError):
    """Raised when a release evidence artifact violates its contract."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise EvidenceError(f"missing or empty JSON artifact: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"invalid JSON artifact: {path}: {error}") from error
    if not isinstance(payload, dict):
        raise EvidenceError(f"JSON artifact must be an object: {path}")
    return payload


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def _positive_number(value: Any, message: str) -> None:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0.0,
        message,
    )


def _validate_png(path: Path) -> None:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise EvidenceError(f"cannot read PNG artifact: {path}: {error}") from error
    _require(
        len(data) >= 32 and data[:8] == b"\x89PNG\r\n\x1a\n",
        f"GUI evidence is not a valid PNG: {path}",
    )
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    _require(
        width >= 100 and height >= 100 and len(data) >= 1024,
        f"GUI evidence screenshot is unexpectedly small: {width}x{height}, {len(data)} bytes",
    )
    _require(b"IDAT" in data, "GUI evidence screenshot has no pixel payload")


def _validate_core(path: Path) -> None:
    payload = _read_json(path)
    _require(payload.get("schema") == 1, "core coverage schema must be 1")
    _require(payload.get("scope") == "core", "core coverage scope must be core")
    hits = payload.get("hits")
    source = payload.get("source")
    _require(
        isinstance(hits, int)
        and not isinstance(hits, bool)
        and isinstance(source, int)
        and not isinstance(source, bool),
        "core coverage counts must be integers",
    )
    _require(hits == source and source > 0, "core coverage must be complete and non-empty")
    _require(payload.get("percent") == 100.0, "core coverage must be 100 percent")


def _validate_blender(directory: Path) -> None:
    coverage = _read_json(directory / "coverage.json")
    _require(coverage.get("schema") == 1, "Blender coverage schema must be 1")
    _require(coverage.get("scope") == "blender", "Blender coverage scope must be blender")
    hits = coverage.get("hits")
    source = coverage.get("source")
    percent = coverage.get("percent")
    _require(
        isinstance(hits, int)
        and not isinstance(hits, bool)
        and isinstance(source, int)
        and not isinstance(source, bool),
        "Blender coverage counts must be integers",
    )
    _require(0 < hits <= source, "Blender coverage counts are invalid")
    _require(
        isinstance(percent, (int, float))
        and not isinstance(percent, bool)
        and 0.0 < percent <= 100.0,
        "Blender coverage percent is invalid",
    )

    performance = _read_json(directory / "performance.json")
    _require(performance.get("schema") == 1, "Blender performance schema must be 1")
    _positive_number(performance.get("voxels"), "Blender performance voxel count must be positive")
    _positive_number(performance.get("rebuild_ms"), "Blender rebuild timing must be positive")
    _positive_number(performance.get("selection_ms"), "Blender selection timing must be positive")


def _validate_windows(directory: Path) -> None:
    required = (
        "e2e.log",
        "performance.json",
        "regression-baseline.json",
        "voxtart_gui.json",
        "voxtart_gui.png",
    )
    for name in required:
        path = directory / name
        _require(path.is_file() and path.stat().st_size > 0, f"missing or empty Windows artifact: {path}")

    baseline = _read_json(directory / "regression-baseline.json")
    _require(baseline.get("schema") == 1, "Windows regression baseline schema must be 1")
    _require(
        baseline.get("core_tests") == 109
        and baseline.get("blender_tests") == 29
        and baseline.get("e2e_tests") == 7,
        "Windows regression baseline does not match 109/29/7",
    )

    performance = _read_json(directory / "performance.json")
    _require(performance.get("schema") == 1, "Windows performance schema must be 1")
    _positive_number(performance.get("voxels"), "Windows performance voxel count must be positive")
    _positive_number(performance.get("rebuild_ms"), "Windows rebuild timing must be positive")
    _positive_number(performance.get("selection_ms"), "Windows selection timing must be positive")

    _validate_png(directory / "voxtart_gui.png")
    gui = _read_json(directory / "voxtart_gui.json")
    _require(gui.get("ok") is True, "Windows GUI evidence is not successful")
    log = (directory / "e2e.log").read_text(encoding="utf-8", errors="replace")
    _require("VOXTART_E2E_OK" in log, "Windows E2E log is missing its success marker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("core", "blender", "windows"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.kind == "core":
            _validate_core(args.path)
            marker = "VOXTART_CORE_COVERAGE_ARTIFACT_OK"
        elif args.kind == "blender":
            _validate_blender(args.path)
            marker = "VOXTART_BLENDER_EVIDENCE_OK"
        else:
            _validate_windows(args.path)
            marker = "VOXTART_WINDOWS_E2E_EVIDENCE_OK"
    except EvidenceError as error:
        print(f"VOXTART_EVIDENCE_INVALID: {error}", file=sys.stderr)
        return 1
    print(marker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
