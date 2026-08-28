"""Validate the artist-facing ZIP without importing Blender or Qt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    args = parser.parse_args()
    path = Path(args.archive).resolve()
    if not path.is_file():
        raise SystemExit("Release archive not found: {}".format(path))
    match = re.search(r"Bake_Tools_Blender-(\d+\.\d+\.\d+)-win64\.zip$", path.name)
    if not match:
        raise SystemExit("Unexpected release filename")
    version = match.group(1)
    with ZipFile(path, "r") as archive:
        names = {item.filename.replace("\\", "/").rstrip("/") for item in archive.infolist()}
        unsafe = [name for name in names if name.startswith("/") or ".." in Path(name).parts]
        if unsafe:
            raise SystemExit("Unsafe ZIP member: {}".format(unsafe[0]))
        required = {
            "Bake_Tools_Blender/__init__.py",
            "Bake_Tools_Blender/blender_manifest.toml",
            "Bake_Tools_Blender/LICENSE",
            "Bake_Tools_Blender/PRIVACY.md",
            "Bake_Tools_Blender/THIRD_PARTY_NOTICES.md",
            "Bake_Tools_Blender/addon/bake_tools_blender/__init__.py",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/PySide6/QtCore.pyd",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/PySide6/Qt6Core.dll",
            "Bake_Tools_Blender/addon/bake_tools_blender/vendor/shiboken6/Shiboken.pyd",
        }
        missing = sorted(required - names)
        if missing:
            raise SystemExit("Missing release files: {}".format(", ".join(missing)))
        forbidden = [name for name in names if "__pycache__" in name or Path(name).suffix.lower() in {".pyc", ".obj", ".lib", ".exp"}]
        if forbidden:
            raise SystemExit("Developer artifact in release: {}".format(forbidden[0]))
        manifest = archive.read("Bake_Tools_Blender/blender_manifest.toml").decode("utf-8-sig")
        if 'version = "{}"'.format(version) not in manifest:
            raise SystemExit("Manifest version does not match archive filename")
        telemetry_source = archive.read(
            "Bake_Tools_Blender/addon/bake_tools_blender/telemetry.py"
        ).decode("utf-8-sig")
        if 'PLUGIN_VERSION = "{}"'.format(version) not in telemetry_source:
            raise SystemExit("Telemetry version does not match archive filename")
        release_files = json.loads(archive.read("Bake_Tools_Blender/release_files.json"))
        if not isinstance(release_files, list) or not release_files:
            raise SystemExit("release_files.json is invalid")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(json.dumps({"archive": str(path), "version": version, "sha256": digest}, indent=2))


if __name__ == "__main__":
    main()
