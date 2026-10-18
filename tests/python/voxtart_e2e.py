# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run VOXTART's complete Windows end-to-end regression matrix.

The runner orchestrates, in order:

* system-Python deterministic core tests;
* the existing Blender-native regression suite;
* the headless editor, palette, recovery, and performance suite;
* save/reopen persistence in separate Blender processes;
* a clean package assembly and isolated package launcher probe;
* a black-box launcher probe and a visible GUI screenshot probe.

Run from the repository root with::

    python tests/python/voxtart_e2e.py

Use ``--skip-launcher`` only when the compiled ``VoxTart.exe`` is unavailable.
Use ``--skip-gui`` only on hosts without an interactive desktop; the run writes
an explicit evidence marker instead of silently dropping the GUI probe.
The default run intentionally fails instead of silently narrowing coverage.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import struct
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT = ROOT / "tests" / "python"
ADDON_SOURCE_ROOT = ROOT / "scripts" / "addons_core"
BLENDER = ROOT / "build" / "blender-5.2.2" / "blender-5.2.2-windows-x64" / "blender-core.exe"
RUNNER = ROOT / "run_voxtart.cmd"
LAUNCHER = ROOT / "VoxTart.exe"
CORE_TEST = TEST_ROOT / "voxtart_core_test.py"
BLENDER_TEST = TEST_ROOT / "voxtart_blender_test.py"
BLENDER_E2E_TEST = TEST_ROOT / "voxtart_e2e_blender.py"
LAUNCHER_PROBE = TEST_ROOT / "voxtart_launcher_probe.py"
PERSISTENCE_PROBE = TEST_ROOT / "voxtart_persistence_probe.py"
GUI_PROBE = TEST_ROOT / "voxtart_gui_probe.py"
PACKAGE_ASSEMBLER = ROOT / "tools" / "assemble_voxtart_package.py"
REGRESSION_BASELINE = {"core_tests": 112, "blender_tests": 33, "e2e_tests": 7}


def _blender_background_command(*, factory_startup: bool) -> list[str]:
    command = [str(BLENDER), "--background"]
    if factory_startup:
        command.append("--factory-startup")
    if os.name == "nt":
        command.extend(("--gpu-backend", "vulkan"))
    return command


def _run(
    name: str,
    command: list[str],
    *,
    timeout: int = 3600,
    env: dict[str, str] | None = None,
    cwd: Path | str = ROOT,
) -> str:
    print(f"\n=== {name} ===")
    print("$ " + subprocess.list2cmdline(command))
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    if output:
        print(output.rstrip())
    if completed.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {completed.returncode}")
    return output


def _assert_test_baseline(label: str, output: str, expected: int) -> None:
    marker = f"Ran {expected} tests"
    if marker not in output:
        raise RuntimeError(f"{label} regression count changed; expected {marker!r}")


def _write_baseline_artifact() -> None:
    artifact_root = os.environ.get("VOXTART_E2E_ARTIFACT_DIR")
    if not artifact_root:
        return
    target = Path(artifact_root) / "regression-baseline.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"schema": 1, **REGRESSION_BASELINE}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_gui_skipped_artifact() -> None:
    artifact_root = os.environ.get("VOXTART_E2E_ARTIFACT_DIR")
    if not artifact_root:
        return
    target = Path(artifact_root) / "gui-skipped.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "Visible GUI probe skipped: host has no interactive desktop.\n",
        encoding="utf-8",
    )


def _assert_paths(skip_launcher: bool, skip_gui: bool) -> None:
    required = [
        BLENDER,
        CORE_TEST,
        BLENDER_TEST,
        BLENDER_E2E_TEST,
        PERSISTENCE_PROBE,
    ]
    if not skip_launcher:
        required.extend((RUNNER, LAUNCHER, LAUNCHER_PROBE, PACKAGE_ASSEMBLER))
        if not skip_gui:
            required.append(GUI_PROBE)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing E2E prerequisites:\n" + "\n".join(missing))


def _run_launcher_probe() -> None:
    with tempfile.TemporaryDirectory(prefix="voxtart-e2e-") as directory:
        config_path = Path(directory) / "config"
        marker = Path(directory) / "launcher_probe.json"
        config_path.mkdir()
        environment = os.environ.copy()
        environment["VOXTART_CONFIG"] = str(config_path)
        environment["BLENDER_USER_CONFIG"] = str(config_path)
        environment["VOXTART_E2E_PROBE_OUTPUT"] = str(marker)
        _run(
            "legacy splash preference fixture",
            [
                str(BLENDER),
                "--background",
                "--python-expr",
                "import bpy; bpy.context.preferences.view.show_splash=True; bpy.ops.wm.save_userpref()",
            ],
            timeout=180,
            env=environment,
        )
        command = (
            f"call .\\run_voxtart.cmd --background --python {LAUNCHER_PROBE}"
        )
        _run(
            "launcher splash migration smoke",
            ["cmd.exe", "/d", "/s", "/c", command],
            timeout=180,
            env=environment,
        )
        if not marker.is_file():
            raise RuntimeError("Launcher exited successfully without writing its probe marker")
        if not (config_path / "userpref.blend").is_file():
            raise RuntimeError("Launcher did not initialize isolated user preferences")
        if not (config_path / "voxtart-splash-disabled-v1").is_file():
            raise RuntimeError("Launcher did not persist the native splash migration marker")
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if (
            payload.get("background") is not True
            or payload.get("voxtart_registered") is not True
            or payload.get("workspace") != "EDIT"
            or payload.get("editor_mode") != "ADD"
            or payload.get("show_splash") is not False
        ):
            raise RuntimeError(f"Launcher probe reported an invalid payload: {payload!r}")
        print("VOXTART_LAUNCHER_PROBE " + json.dumps(payload, sort_keys=True))

def _run_package_probe() -> None:
    with tempfile.TemporaryDirectory(prefix="voxtart-package-") as directory:
        directory_path = Path(directory)
        package_root = directory_path / "package"
        _run(
            "clean package assembly",
            [
                sys.executable,
                str(PACKAGE_ASSEMBLER),
                "--source-root",
                str(ROOT),
                "--runtime-source",
                str(BLENDER.parent),
                "--link-runtime",
                "--output",
                str(package_root),
            ],
            timeout=600,
        )
        manifest_path = package_root / "package-manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError("Package assembly did not write its manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("assembly_mode") != "smoke-linked":
            raise RuntimeError(f"Package smoke used an unexpected assembly mode: {manifest!r}")
        missing = [
            relative
            for relative in manifest.get("required_paths", [])
            if not (package_root / relative).exists()
        ]
        if missing:
            raise RuntimeError("Package assembly omitted required paths: " + ", ".join(missing))
        license_text = (package_root / "LICENSES" / "GPL-3.0-or-later.txt").read_text(
            encoding="utf-8"
        )
        notices_text = (package_root / "LICENSES" / "VOXTART-NOTICES.txt").read_text(
            encoding="utf-8"
        )
        source_offer_text = (package_root / "SOURCE-CODE-OFFER.txt").read_text(
            encoding="utf-8"
        )
        assets_license = (package_root / "LICENSES" / "VOXTART-ASSETS-CC0.txt").read_text(
            encoding="utf-8"
        )
        if (
            not license_text.startswith("                    GNU GENERAL PUBLIC LICENSE")
            or "GPL-3.0-or-later" not in notices_text
            or "https://github.com/rufl/VOXTART" not in source_offer_text
            or "CC0 1.0 Universal" not in assets_license
        ):
            raise RuntimeError("Package legal files do not contain the expected GPL/source/asset notices")

        config_path = directory_path / "config"
        marker = directory_path / "package_probe.json"
        environment = os.environ.copy()
        environment["VOXTART_CONFIG"] = str(config_path)
        environment["VOXTART_E2E_PROBE_OUTPUT"] = str(marker)
        command = (
            r"call .\run_voxtart.cmd --background "
            r"--python tests\python\voxtart_launcher_probe.py"
        )
        _run(
            "isolated package launcher smoke",
            ["cmd.exe", "/d", "/s", "/c", command],
            timeout=180,
            env=environment,
            cwd=package_root,
        )
        if not marker.is_file():
            raise RuntimeError("Packaged launcher exited without writing its probe marker")
        if not (config_path / "userpref.blend").is_file():
            raise RuntimeError("Packaged launcher did not initialize isolated user preferences")
        if not (config_path / "voxtart-splash-disabled-v1").is_file():
            raise RuntimeError("Packaged launcher did not persist the native splash migration marker")
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if (
            payload.get("background") is not True
            or payload.get("voxtart_registered") is not True
            or payload.get("workspace") != "EDIT"
            or payload.get("editor_mode") != "ADD"
            or payload.get("show_splash") is not False
        ):
            raise RuntimeError(f"Packaged launcher probe reported an invalid payload: {payload!r}")
        print("VOXTART_PACKAGE_PROBE " + json.dumps(payload, sort_keys=True))


def _run_persistence_probe() -> None:
    with tempfile.TemporaryDirectory(prefix="voxtart-persistence-") as directory:
        directory_path = Path(directory)
        preferences_path = directory_path / "preferences"
        preferences_path.mkdir()
        blend_path = directory_path / "persisted_asset.blend"
        marker = directory_path / "persistence.json"
        environment = os.environ.copy()
        environment["BLENDER_USER_CONFIG"] = str(preferences_path)
        environment["VOXTART_PERSISTENCE_BLEND"] = str(blend_path)
        environment["VOXTART_PERSISTENCE_MARKER"] = str(marker)
        environment["VOXTART_PERSISTENCE_PHASE"] = "write"
        _run(
            "cross-process persistence write",
            [
                *_blender_background_command(factory_startup=True),
                "--python",
                str(PERSISTENCE_PROBE),
            ],
            env=environment,
        )
        write_payload = json.loads(marker.read_text(encoding="utf-8"))
        if write_payload.get("recovery_property") is not True:
            raise RuntimeError(f"Persistence fixture did not contain recovery state: {write_payload!r}")
        if write_payload.get("palette_index") != 1:
            raise RuntimeError(f"Persistence fixture did not retain palette index: {write_payload!r}")
        if not blend_path.is_file():
            raise RuntimeError("Persistence fixture did not save a .blend file")
        environment["VOXTART_PERSISTENCE_PHASE"] = "read"
        addon_bootstrap = f"import sys; sys.path.insert(0, {str(ADDON_SOURCE_ROOT)!r})"
        _run(
            "cross-process persistence reopen",
            [
                *_blender_background_command(factory_startup=False),
                "--python-expr",
                addon_bootstrap,
                str(blend_path),
                "--python",
                str(PERSISTENCE_PROBE),
            ],
            env=environment,
        )
        read_payload = json.loads(marker.read_text(encoding="utf-8"))
        expected = {
            "phase": "read",
            "recovered": True,
            "clipboard_count": 2,
            "palette_index": 1,
            "recovery_cleared": True,
        }
        if any(read_payload.get(key) != value for key, value in expected.items()):
            raise RuntimeError(f"Persistence probe reported an invalid payload: {read_payload!r}")
        print("VOXTART_PERSISTENCE_PROBE " + json.dumps(read_payload, sort_keys=True))


def _window_titles() -> set[str]:
    if os.name != "nt":
        return set()
    user32 = ctypes.windll.user32
    titles: set[str] = set()
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_long)

    def collect(hwnd, _parameter):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        titles.add(buffer.value)
        return True

    callback = callback_type(collect)
    user32.EnumWindows(callback, 0)
    return titles


def _window_has_standard_controls(title_fragment: str) -> bool:
    if os.name != "nt":
        return False
    user32 = ctypes.windll.user32
    get_style = user32.GetWindowLongW
    get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
    get_style.restype = ctypes.c_long
    required = (
        0x00C00000  # WS_CAPTION
        | 0x00080000  # WS_SYSMENU
        | 0x00020000  # WS_MINIMIZEBOX
        | 0x00010000  # WS_MAXIMIZEBOX
    )
    found = False
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_long)

    def collect(hwnd, _parameter):
        nonlocal found
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if title_fragment not in buffer.value:
            return True
        found = (int(get_style(hwnd, -16)) & required) == required
        return not found

    callback = callback_type(collect)
    user32.EnumWindows(callback, 0)
    return found


def _validate_png(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 32 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"GUI probe did not produce a valid PNG: {path}")
    width, height = struct.unpack(">II", data[16:24])
    if width < 100 or height < 100 or len(data) < 1024:
        raise RuntimeError(f"GUI probe screenshot is unexpectedly small: {width}x{height}, {len(data)} bytes")
    if b"IDAT" not in data:
        raise RuntimeError("GUI probe screenshot has no pixel payload")
    return width, height


def _run_gui_probe() -> None:
    with tempfile.TemporaryDirectory(prefix="voxtart-gui-") as directory:
        directory_path = Path(directory)
        marker = directory_path / "gui.json"
        screenshot = directory_path / "gui.png"
        scene_screenshot = directory_path / "gui_scene.png"
        environment = os.environ.copy()
        environment["VOXTART_CONFIG"] = str(directory_path / "config")
        environment["VOXTART_GUI_MARKER"] = str(marker)
        environment["VOXTART_GUI_SCREENSHOT"] = str(screenshot)
        environment["VOXTART_GUI_SCENE_SCREENSHOT"] = str(scene_screenshot)
        environment["VOXTART_GUI_SAVE_PATH"] = str(directory_path / "gui_saved.blend")
        environment["VOXTART_GUI_ASSET_PATH"] = str(directory_path / "gui_asset.voxel.json")
        environment["VOXTART_GUI_MESH_PATH"] = str(directory_path / "gui_export.obj")
        environment["VOXTART_GUI_PALETTE_PATH"] = str(directory_path / "gui_palette.hex")
        environment["VOXTART_GUI_VOX_PATH"] = str(directory_path / "gui_roundtrip.vox")
        environment["VOXTART_GUI_BLOCKBENCH_PATH"] = str(directory_path / "gui_roundtrip.bbmodel")
        environment["VOXTART_GUI_MINECRAFT_PATH"] = str(directory_path / "gui_roundtrip.schem")
        environment["VOXTART_GUI_STACK_PATH"] = str(directory_path / "gui_roundtrip.sstack.json")
        environment["VOXTART_GUI_GALLERY_PATH"] = str(directory_path / "gui_gallery")
        environment["VOXTART_GUI_GAME_RIG_PATH"] = str(directory_path / "gui_game_rig.glb")
        command = [str(LAUNCHER), "--python", str(GUI_PROBE)]
        print("\n=== visible VoxTart GUI smoke ===")
        print("$ " + subprocess.list2cmdline(command))
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        window_seen = False
        window_controls_seen = False
        deadline = time.monotonic() + 180.0
        try:
            while process.poll() is None:
                window_seen = window_seen or any(
                    "VoxTart" in title for title in _window_titles()
                )
                window_controls_seen = (
                    window_controls_seen or _window_has_standard_controls("VoxTart")
                )
                if time.monotonic() >= deadline:
                    raise RuntimeError("Visible VoxTart GUI probe timed out")
                time.sleep(0.25)
            return_code = process.wait(timeout=10)
        except Exception:
            if process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            raise
        if return_code != 0:
            raise RuntimeError(f"Visible VoxTart GUI probe failed with exit code {return_code}")
        if not marker.is_file():
            raise RuntimeError("Visible GUI exited without writing its probe marker")
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if (
            payload.get("ok") is not True
            or payload.get("save_control") is not True
            or payload.get("starter_pyramid") != 56
            or payload.get("starter_palette", 0) < 4
            or payload.get("shell_make") is not True
            or payload.get("shell_native_ui_blocked") is not True
            or payload.get("chatbox_surface") is not True
            or payload.get("shell_editor") is not True
            or payload.get("shell_voxelize") is not True
            or payload.get("shell_refresh") is not True
            or payload.get("palette_reduce") is not True
            or payload.get("blockbench_roundtrip") is not True
            or payload.get("minecraft_roundtrip") is not True
            or payload.get("gallery_roundtrip") is not True
            or payload.get("hierarchy_roundtrip") is not True
            or payload.get("kitbash_roundtrip") is not True
            or payload.get("character_editor_roundtrip") is not True
            or payload.get("mcp_roundtrip") is not True
            or payload.get("stack_roundtrip") is not True
            or payload.get("native_vox_roundtrip") is not True
            or payload.get("character_prepare") is not True
            or payload.get("character_native_ui_blocked") is not True
            or payload.get("character_extract") is not True
            or payload.get("character_export") is not True
            or payload.get("character_bake") is not True
            or payload.get("character_preview") is not True
            or payload.get("startup_editor") is not True
            or payload.get("first_run_hint") is not True
            or payload.get("native_chrome_hidden") is not True
            or payload.get("area_types") != ["VIEW_3D"]
            or payload.get("window_fullscreen") is not False
            or any(payload.get("native_region_props", {}).values())
            or payload.get("native_ui_lock") is not True
            or payload.get("advanced_tools_toggle") is not True
            or payload.get("beginner_toolbar_after_first_edit") is not True
            or payload.get("options_menu") is not True
            or payload.get("menu_surface") is not True
            or payload.get("viewport_hud") is not True
            or payload.get("viewport_hud_hints") is not True
            or payload.get("tool_options") is not True
            or payload.get("tool_options_hints") is not True
            or payload.get("selection_tool_options") is not True
            or payload.get("scene_options") is not True
            or payload.get("scene_completion_controls") is not True
            or payload.get("scene_option_hints") is not True
            or payload.get("grid_toggle") is not True
            or payload.get("product_controls") is not True
            or payload.get("details_toggle") is not True
            or payload.get("help_toggle") is not True
            or payload.get("escape_keeps_ui") is not True
            or payload.get("palette_selection") is not True
            or payload.get("palette_favorite") is not True
            or payload.get("color_wheel") is not True
            or payload.get("grid_shortcut") is not True
            or payload.get("dock_drawers") is not True
            or payload.get("dock_hints") is not True
            or payload.get("dock_snap") is not True
            or payload.get("material_shading") is not True
            or payload.get("history_presentation") is not True
            or not any(str(label).startswith("Added ") for label in payload.get("history_labels", []))
            or not any(str(label).startswith("Erased ") for label in payload.get("history_labels", []))
            or not any(str(label).startswith("Colored ") for label in payload.get("history_labels", []))
            or payload.get("paint_undo_redo") is not True
            or payload.get("drag_paint") is not True
            or payload.get("undo_redo_controls") is not True
            or payload.get("selection_workflow") is not True
            or payload.get("project_control") is not True
            or not str(payload.get("first_edit_feedback", "")).startswith("Added ")
            or payload.get("operator_running_modal") is not True
            or payload.get("draw_overlay_handle") is not True
            or payload.get("editor_overlay_draw") is not True
            or payload.get("draw_preview_handle") is not True
            or payload.get("shell_draw_handle") is not True
            or payload.get("preview_count", 0) <= 0
        ):
            raise RuntimeError(f"Visible GUI probe reported an invalid payload: {payload!r}")
        if not window_seen:
            raise RuntimeError("Visible GUI probe never exposed a VoxTart window title")
        if not window_controls_seen:
            raise RuntimeError("Visible GUI probe window lacks standard title bar controls")
        dimensions = _validate_png(screenshot)
        scene_dimensions = _validate_png(scene_screenshot)
        artifact_root = os.environ.get("VOXTART_E2E_ARTIFACT_DIR")
        if artifact_root:
            artifact_path = Path(artifact_root)
            artifact_path.mkdir(parents=True, exist_ok=True)
            shutil.copy2(marker, artifact_path / "voxtart_gui.json")
            shutil.copy2(screenshot, artifact_path / "voxtart_gui.png")
            shutil.copy2(scene_screenshot, artifact_path / "voxtart_gui_scene.png")
        print(
            "VOXTART_GUI_PROBE "
            + json.dumps(
                {**payload, "dimensions": dimensions, "scene_dimensions": scene_dimensions},
                sort_keys=True,
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-launcher",
        action="store_true",
        help="skip launcher-dependent background and visible GUI checks",
    )
    parser.add_argument(
        "--skip-gui",
        action="store_true",
        help="skip the visible GUI probe on hosts without an interactive desktop",
    )
    args = parser.parse_args()
    _assert_paths(args.skip_launcher, args.skip_gui)

    core_output = _run("core regression", [sys.executable, str(CORE_TEST)])
    _assert_test_baseline("core", core_output, REGRESSION_BASELINE["core_tests"])
    blender_output = _run(
        "Blender regression",
        [
            *_blender_background_command(factory_startup=True),
            "--python",
            str(BLENDER_TEST),
        ],
    )
    _assert_test_baseline("Blender", blender_output, REGRESSION_BASELINE["blender_tests"])
    e2e_output = _run(
        "Blender end-to-end editor matrix",
        [
            *_blender_background_command(factory_startup=True),
            "--python",
            str(BLENDER_E2E_TEST),
        ],
    )
    _assert_test_baseline("Blender E2E", e2e_output, REGRESSION_BASELINE["e2e_tests"])
    _write_baseline_artifact()
    _run_persistence_probe()
    if not args.skip_launcher:
        _run_launcher_probe()
        _run_package_probe()
        if args.skip_gui:
            _write_gui_skipped_artifact()
            print("VOXTART_GUI_PROBE_SKIPPED host_has_no_interactive_desktop=true")
        else:
            _run_gui_probe()
    print("\nVOXTART_E2E_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
