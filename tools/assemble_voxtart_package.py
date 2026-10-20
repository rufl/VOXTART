# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Assemble a runnable VOXTART package tree for release smoke tests.

The default mode copies the Blender runtime for a genuinely isolated release
tree. ``--link-runtime`` is available for fast local/CI smoke tests when the
package and source are on one volume; executable files that branding may
modify are copied even in that mode.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

RUNTIME_RELATIVE = Path("build") / "blender-5.2.2" / "blender-5.2.2-windows-x64"
RUNTIME_REQUIRED_FILES = (
    Path("blender.exe"),
    Path("blender-core.exe"),
    Path("5.2") / "python" / "bin" / "python.exe",
)
TOOL_FILES = (
    "brand_voxtart_runtime.py",
    "voxtart_desktop_start.py",
    "voxtart_preferences_init.py",
)
ASSET_FILES = (
    "voxtart_icon_16.png",
    "voxtart_icon_32.png",
    "voxtart_icon_48.png",
    "voxtart_icon_256.png",
)
VENDOR_DIRECTORIES = (
    Path("third_party") / "anyCreature",
    Path("third_party") / "node",
)
VENDOR_FILES = (
    Path("third_party") / "VOXTART-VENDOR.lock",
)
VENDOR_REQUIRED_FILES = (
    Path("third_party") / "anyCreature" / "engine" / "cli.js",
    Path("third_party") / "anyCreature" / "LICENSE",
    Path("third_party") / "node" / "node.exe",
    Path("third_party") / "node" / "LICENSE",
    Path("third_party") / "VOXTART-VENDOR.lock",
)
LICENSE_FILES = (
    (Path("LICENSES") / "GPL-3.0-or-later.txt", Path("doc/license/GPL3-license.txt")),
    (Path("LICENSES") / "VOXTART-NOTICES.txt", Path("VOXTART-NOTICES.txt")),
    (Path("LICENSES") / "VOXTART-ASSETS-CC0.txt", Path("assets/LICENSE")),
    (Path("LICENSES") / "anyCreature-MIT.txt", Path("LICENSES") / "anyCreature-MIT.txt"),
    (Path("LICENSES") / "LicenseRef-Node.js.txt", Path("LICENSES") / "LicenseRef-Node.js.txt"),
    (Path("SOURCE-CODE-OFFER.txt"), Path("VOXTART-SOURCE-CODE-OFFER.txt")),
)


def _validate_vendor(source_root: Path) -> None:
    missing = [str(path) for path in VENDOR_REQUIRED_FILES if not (source_root / path).is_file()]
    if missing:
        raise FileNotFoundError(
            f"VOXTART offline vendor is missing required files: {', '.join(missing)}"
        )


def _validate_runtime(source: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"Blender runtime directory not found: {source}")
    missing = [str(path) for path in RUNTIME_REQUIRED_FILES if not (source / path).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Blender runtime is missing required files: {', '.join(missing)}"
        )

def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _stage_runtime(
    source: Path,
    target: Path,
    *,
    link_runtime: bool,
) -> dict[str, int | str | bool]:
    _validate_runtime(source)
    linked = 0
    copied = 0
    target.mkdir(parents=True, exist_ok=True)
    for current, directories, files in os.walk(source):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        target_path = target / relative
        target_path.mkdir(parents=True, exist_ok=True)
        for directory in directories:
            (target_path / directory).mkdir(parents=True, exist_ok=True)
        for name in files:
            source_file = current_path / name
            target_file = target_path / name
            target_file.parent.mkdir(parents=True, exist_ok=True)
            # brand_voxtart_runtime.py patches this executable and creates its
            # backup. Break the hardlink so a smoke test cannot mutate source.
            copy_required = not link_runtime or name in {
                "blender-core.exe",
                "blender-core.exe.voxtart-original",
            }
            if not copy_required:
                try:
                    os.link(source_file, target_file)
                    linked += 1
                    continue
                except OSError:
                    pass
            shutil.copy2(source_file, target_file)
            copied += 1
    if not link_runtime:
        link_mode = "copy"
    elif copied > 2:
        link_mode = "mixed"
    else:
        link_mode = "hardlink"
    return {
        "link_mode": link_mode,
        "runtime_isolated": not link_runtime,
        "hardlinked_files": linked,
        "copied_files": copied,
    }


def assemble(
    source_root: Path,
    output: Path,
    runtime_source: Path,
    *,
    link_runtime: bool = False,
) -> dict[str, object]:
    if output.exists():
        if any(output.iterdir()):
            raise FileExistsError(f"Package output is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    for relative in (Path("run_voxtart.cmd"), Path("VoxTart.exe")):
        _copy_file(source_root / relative, output / relative)
    for name in TOOL_FILES:
        _copy_file(source_root / "tools" / name, output / "tools" / name)
    for name in ASSET_FILES:
        _copy_file(source_root / "assets" / name, output / "assets" / name)
    for target_relative, source_relative in LICENSE_FILES:
        _copy_file(source_root / source_relative, output / target_relative)
    shutil.copytree(
        source_root / "scripts" / "addons_core" / "voxtart",
        output / "scripts" / "addons_core" / "voxtart",
    )
    _copy_file(
        source_root / "tests" / "python" / "voxtart_launcher_probe.py",
        output / "tests" / "python" / "voxtart_launcher_probe.py",
    )
    _validate_vendor(source_root)
    for relative in VENDOR_DIRECTORIES:
        shutil.copytree(source_root / relative, output / relative)
    for relative in VENDOR_FILES:
        _copy_file(source_root / relative, output / relative)

    runtime_target = output / RUNTIME_RELATIVE
    runtime_stats = _stage_runtime(runtime_source, runtime_target, link_runtime=link_runtime)
    manifest = {
        "schema": 1,
        "assembly_mode": "smoke-linked" if link_runtime else "release-copy",
        "package_root": str(output),
        "runtime_source": str(runtime_source),
        "runtime_relative": str(RUNTIME_RELATIVE),
        "runtime": runtime_stats,
        "offline_vendor": {
            "anycreature_root": "third_party/anyCreature",
            "node_executable": "third_party/node/node.exe",
            "network_runtime_dependency": False,
            "node_modules": False,
            "python_runtime_dependency": False,
        },
        "required_paths": [
            "VoxTart.exe",
            "run_voxtart.cmd",
            "LICENSES/GPL-3.0-or-later.txt",
            "LICENSES/VOXTART-NOTICES.txt",
            "LICENSES/VOXTART-ASSETS-CC0.txt",
            "LICENSES/anyCreature-MIT.txt",
            "LICENSES/LicenseRef-Node.js.txt",
            "SOURCE-CODE-OFFER.txt",
            "third_party/anyCreature/engine/cli.js",
            "third_party/anyCreature/LICENSE",
            "third_party/node/node.exe",
            "third_party/node/LICENSE",
            "third_party/VOXTART-VENDOR.lock",
            "tools/brand_voxtart_runtime.py",
            "tools/voxtart_desktop_start.py",
            "tools/voxtart_preferences_init.py",
            "scripts/addons_core/voxtart",
            "tests/python/voxtart_launcher_probe.py",
            str(RUNTIME_RELATIVE / "blender.exe"),
            str(RUNTIME_RELATIVE / "blender-core.exe"),
            str(RUNTIME_RELATIVE / "5.2" / "python" / "bin" / "python.exe"),
        ],
    }
    (output / "package-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-source", type=Path)
    parser.add_argument(
        "--link-runtime",
        action="store_true",
        help="use hardlinks for unchanged runtime files in a smoke-only staging tree",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    runtime_source = (args.runtime_source or (source_root / RUNTIME_RELATIVE)).resolve()
    manifest = assemble(
        source_root,
        args.output.resolve(),
        runtime_source,
        link_runtime=args.link_runtime,
    )
    print("VOXTART_PACKAGE " + json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
