"""Validate standard and Superhive artist-facing ZIP files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile


STANDARD_NAME = re.compile(r"Bake_Tools_Blender-(\d+\.\d+\.\d+)-win64\.zip$")
SUPERHIVE_NAME = re.compile(
    r"Bake_Groups_Tool_Blender_(\d+\.\d+\.\d+)_Superhive_Windows_x64\.zip$"
)


def _source(archive, name):
    return archive.read(name).decode("utf-8-sig")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    args = parser.parse_args()
    path = Path(args.archive).resolve()
    if not path.is_file():
        raise SystemExit("Release archive not found: {}".format(path))
    standard = STANDARD_NAME.search(path.name)
    superhive = SUPERHIVE_NAME.search(path.name)
    match = standard or superhive
    if not match:
        raise SystemExit("Unexpected release filename")
    version = match.group(1)
    is_superhive = superhive is not None

    with ZipFile(path, "r") as archive:
        names = {
            item.filename.replace("\\", "/").rstrip("/")
            for item in archive.infolist()
        }
        unsafe = [name for name in names if name.startswith("/") or ".." in Path(name).parts]
        if unsafe:
            raise SystemExit("Unsafe ZIP member: {}".format(unsafe[0]))

        required = {
            "Bake_Tools_Blender/__init__.py",
            "Bake_Tools_Blender/blender_manifest.toml",
            "Bake_Tools_Blender/LICENSE",
            "Bake_Tools_Blender/README.md",
            "Bake_Tools_Blender/THIRD_PARTY_NOTICES.md",
            "Bake_Tools_Blender/release_files.json",
            "Bake_Tools_Blender/addon/bake_tools_blender/__init__.py",
            "Bake_Tools_Blender/addon/bake_tools_blender/release_channel.py",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/PySide6/QtCore.pyd",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/PySide6/QtGui.pyd",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/PySide6/QtWidgets.pyd",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/PySide6/Qt6Core.dll",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/PySide6/Qt6Gui.dll",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/PySide6/Qt6Widgets.dll",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/PySide6/plugins/platforms/qwindows.dll",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/shiboken6/Shiboken.pyd",
        }
        if is_superhive:
            required.update({
                "INSTALLATION.txt",
                "Bake_Tools_Blender/assets/Manual.pur",
            })
        else:
            required.update({
                "Bake_Tools_Blender/PRIVACY.md",
                "Bake_Tools_Blender/SECURITY.md",
                "Bake_Tools_Blender/update_manifest.json",
                "Bake_Tools_Blender/docs/Manual.pur",
                "Bake_Tools_Blender/addon/bake_tools_blender/about_update.py",
                "Bake_Tools_Blender/addon/bake_tools_blender/update_service.py",
                "Bake_Tools_Blender/addon/bake_tools_blender/telemetry.py",
            })
        missing = sorted(required - names)
        if missing:
            raise SystemExit("Missing release files: {}".format(", ".join(missing)))

        forbidden_suffixes = {".exe", ".pyc", ".obj", ".lib", ".exp"}
        forbidden = [
            name for name in names
            if "__pycache__" in name or Path(name).suffix.lower() in forbidden_suffixes
        ]
        if forbidden:
            raise SystemExit("Forbidden artifact in release: {}".format(forbidden[0]))

        manifest = _source(archive, "Bake_Tools_Blender/blender_manifest.toml")
        if 'version = "{}"'.format(version) not in manifest:
            raise SystemExit("Manifest version does not match archive filename")

        root_init = _source(archive, "Bake_Tools_Blender/__init__.py")
        release_channel = _source(
            archive, "Bake_Tools_Blender/addon/bake_tools_blender/release_channel.py"
        )
        if is_superhive:
            forbidden_superhive = {
                "Bake_Tools_Blender/PRIVACY.md",
                "Bake_Tools_Blender/SECURITY.md",
                "Bake_Tools_Blender/update_manifest.json",
                "Bake_Tools_Blender/docs",
                "Bake_Tools_Blender/addon/bake_tools_blender/about_update.py",
                "Bake_Tools_Blender/addon/bake_tools_blender/update_service.py",
                "Bake_Tools_Blender/addon/bake_tools_blender/telemetry.py",
                "Bake_Tools_Blender/addon/bake_tools_blender/localization/publication_overrides.json",
            }
            leaked = sorted(forbidden_superhive & names)
            leaked.extend(sorted(
                name for name in names if name.startswith("Bake_Tools_Blender/docs/")
            ))
            if leaked:
                raise SystemExit("Superhive-only forbidden content: {}".format(leaked[0]))
            forbidden_code = (
                "_apply_pending_package",
                "download_and_stage_update",
                "pending.json",
                "urllib.request",
                "from . import telemetry",
                "telemetry.report",
            )
            combined = root_init + "\n" + release_channel
            hit = next((token for token in forbidden_code if token in combined), None)
            if hit:
                raise SystemExit("Superhive channel contains forbidden code: {}".format(hit))
            if re.search(r"(?m)^network\s*=", manifest):
                raise SystemExit("Superhive manifest still requests network permission")
        else:
            telemetry_source = _source(
                archive, "Bake_Tools_Blender/addon/bake_tools_blender/telemetry.py"
            )
            if 'PLUGIN_VERSION = "{}"'.format(version) not in telemetry_source:
                raise SystemExit("Telemetry version does not match archive filename")
            if "_apply_pending_package" not in root_init:
                raise SystemExit("Standard build lost its updater bootstrap")
            if "telemetry" not in release_channel:
                raise SystemExit("Standard build lost its opt-in telemetry channel")

        release_files = json.loads(
            archive.read("Bake_Tools_Blender/release_files.json")
        )
        if not isinstance(release_files, list) or not release_files:
            raise SystemExit("release_files.json is invalid")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(json.dumps({
        "archive": str(path),
        "channel": "superhive" if is_superhive else "standard",
        "version": version,
        "sha256": digest,
    }, indent=2))


if __name__ == "__main__":
    main()
