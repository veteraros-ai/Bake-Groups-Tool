# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.join(ROOT, "Bake_Groups")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import bg_update


def _zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content, mode in entries:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            archive.writestr(info, content)


def run():
    assert bg_update._validated_release_version("1.4.5") == "1.4.5"
    for invalid in ("", "../1.4.5", "1.4", "1.4.5/evil", "v1.4.5"):
        try:
            bg_update._validated_release_version(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe version accepted: {}".format(invalid))

    manifest = bg_update._manifest_info_from_text(json.dumps({"version": "1.4.5"}))
    assert manifest["package_url"].endswith("/refs/tags/1.4.5")

    root = tempfile.mkdtemp(prefix="BakeGroupsUpdateSafety_")
    try:
        valid_zip = os.path.join(root, "valid.zip")
        valid_out = os.path.join(root, "valid")
        _zip(valid_zip, [("package/Bake_Groups/bg_version.py", "__version__ = '1.4.5'", None)])
        with zipfile.ZipFile(valid_zip, "r") as archive:
            bg_update._safe_extract_archive(archive, valid_out)
        runtime = os.path.join(valid_out, "package", "Bake_Groups")
        assert bg_update._validate_runtime_version(runtime, "1.4.5") == "1.4.5"

        traversal_zip = os.path.join(root, "traversal.zip")
        _zip(traversal_zip, [("../escaped.txt", "bad", None)])
        try:
            with zipfile.ZipFile(traversal_zip, "r") as archive:
                bg_update._safe_extract_archive(archive, os.path.join(root, "traversal"))
        except RuntimeError:
            pass
        else:
            raise AssertionError("path traversal archive was accepted")
        assert not os.path.exists(os.path.join(root, "escaped.txt"))

        symlink_zip = os.path.join(root, "symlink.zip")
        _zip(symlink_zip, [("link", "target", stat.S_IFLNK | 0o777)])
        try:
            with zipfile.ZipFile(symlink_zip, "r") as archive:
                bg_update._safe_extract_archive(archive, os.path.join(root, "symlink"))
        except RuntimeError:
            pass
        else:
            raise AssertionError("symlink archive was accepted")

        try:
            bg_update._validate_runtime_version(runtime, "1.4.6")
        except RuntimeError:
            pass
        else:
            raise AssertionError("package version mismatch was accepted")

        state_path = os.path.join(root, "active_version.json")
        bg_update._atomic_write_json(state_path, {"active_version": "1.4.5"})
        with io.open(state_path, "r", encoding="utf-8") as handle:
            assert json.load(handle)["active_version"] == "1.4.5"
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("Updater safety tests passed")


if __name__ == "__main__":
    run()
