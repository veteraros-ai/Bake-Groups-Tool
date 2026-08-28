"""Secure, explicit update and rollback service for the Blender release."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request
import zipfile


PLUGIN_NAME = "Bake Groups Tool"
AUTHOR_NAME = "Veteraros AI"
CONTACT_EMAIL = "veteraros@gmail.com"
VERSION = "1.0.0"
GITHUB_URL = "https://github.com/veteraros-ai/Bake-Groups-Tool"
RELEASES_URL = GITHUB_URL + "/releases"
MANIFEST_URL = (
    "https://raw.githubusercontent.com/veteraros-ai/Bake-Groups-Tool/"
    "main/Bake_Tools_Blender/update_manifest.json"
)
MAX_PACKAGE_BYTES = 750 * 1024 * 1024


def addon_root():
    return Path(__file__).resolve().parents[2]


def update_state_dir():
    """Keep update state outside the add-on so replacement/uninstall is safe."""
    return Path.home() / ".bake_tools_blender" / "updates"


def manual_path():
    path = addon_root() / "docs" / "MANUAL.md"
    return path if path.is_file() else None


def privacy_path():
    path = addon_root() / "PRIVACY.md"
    return path if path.is_file() else None


def _version_tuple(value):
    return tuple(int(part) for part in re.findall(r"\d+", str(value))[:4])


def _online_access_allowed():
    try:
        import bpy
        return bool(bpy.app.online_access)
    except (ImportError, AttributeError):
        return True


def _manifest_from_text(text):
    data = json.loads(text)
    remote = str(data.get("latest_version") or data.get("version") or "").strip()
    if not remote:
        raise ValueError("Remote manifest version not found")
    notes = data.get("release_notes") or data.get("notes") or ""
    if isinstance(notes, (list, tuple)):
        notes = "\n".join(str(item) for item in notes)
    package_url = str(data.get("package_url") or "").strip()
    package_sha256 = str(data.get("package_sha256") or "").strip().lower()
    available = _version_tuple(remote) > _version_tuple(VERSION)
    if available and (not package_url or not re.fullmatch(r"[0-9a-f]{64}", package_sha256)):
        raise ValueError("Update manifest must provide a package URL and SHA-256")
    return {
        "current_version": VERSION,
        "remote_version": remote,
        "is_update_available": available,
        "release_notes": str(notes),
        "package_url": package_url,
        "package_sha256": package_sha256,
        "github_url": str(data.get("github_url") or GITHUB_URL),
        "releases_url": str(data.get("releases_url") or RELEASES_URL),
    }


def check_for_update(timeout=5):
    if not _online_access_allowed():
        return {
            "current_version": VERSION,
            "remote_version": "?",
            "is_update_available": False,
            "error": "Blender online access is disabled in Preferences",
            "releases_url": RELEASES_URL,
        }
    try:
        request = urllib.request.Request(
            MANIFEST_URL,
            headers={"User-Agent": "BakeTools-Blender/" + VERSION},
        )
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            return _manifest_from_text(response.read().decode("utf-8-sig"))
    except Exception as exc:
        return {
            "current_version": VERSION,
            "remote_version": "?",
            "is_update_available": False,
            "error": str(exc),
            "releases_url": RELEASES_URL,
        }


def _archive_member_is_safe(name):
    normalized = str(name).replace("\\", "/")
    path = Path(normalized)
    return not normalized.startswith(("/", "\\")) and ".." not in path.parts


def validate_release_archive(path):
    source = Path(path).resolve()
    if not source.is_file() or source.suffix.lower() != ".zip":
        raise ValueError("Bake Tools update archive was not found")
    with zipfile.ZipFile(source, "r") as archive:
        members = archive.infolist()
        if not members:
            raise ValueError("Bake Tools update archive is empty")
        for member in members:
            if not _archive_member_is_safe(member.filename):
                raise ValueError("Unsafe path in Bake Tools update archive")
        names = {member.filename.replace("\\", "/").rstrip("/") for member in members}
        required = {
            "Bake_Tools_Blender/__init__.py",
            "Bake_Tools_Blender/addon/bake_tools_blender/__init__.py",
        }
        if not required.issubset(names):
            raise ValueError("Release ZIP does not contain the complete Blender add-on")
    return source


def _emit_progress(callback, value, message):
    if callback is not None:
        callback(int(value), str(message))


def _download_package(url, destination, expected_sha256, progress_callback=None):
    if not str(url).lower().startswith("https://"):
        raise ValueError("Update package URL must use HTTPS")
    request = urllib.request.Request(
        str(url), headers={"User-Agent": "BakeTools-Blender/" + VERSION}
    )
    digest = hashlib.sha256()
    downloaded = 0
    with urllib.request.urlopen(request, timeout=20.0) as response, open(destination, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        if total > MAX_PACKAGE_BYTES:
            raise ValueError("Update package is larger than the safety limit")
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            downloaded += len(chunk)
            if downloaded > MAX_PACKAGE_BYTES:
                raise ValueError("Update package is larger than the safety limit")
            digest.update(chunk)
            handle.write(chunk)
            if total:
                _emit_progress(progress_callback, 5 + int(65 * downloaded / total), "Downloading update")
    if digest.hexdigest().lower() != str(expected_sha256).lower():
        raise ValueError("Downloaded update SHA-256 does not match the manifest")
    return destination


def _backup_current_release(version, progress_callback=None):
    state_dir = update_state_dir()
    rollback_dir = state_dir / "rollback"
    rollback_dir.mkdir(parents=True, exist_ok=True)
    archive_path = rollback_dir / "Bake_Tools_Blender-{}-win64.zip".format(version)
    temporary = archive_path.with_suffix(".tmp")
    root = addon_root()
    _emit_progress(progress_callback, 76, "Creating rollback package")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for item in root.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(root)
            if "__pycache__" in relative.parts or item.suffix.lower() in {".pyc", ".obj", ".lib", ".exp"}:
                continue
            archive.write(item, str(Path("Bake_Tools_Blender") / relative))
    temporary.replace(archive_path)
    return archive_path


def _write_pending(action, archive, target_version, current_backup):
    state_dir = update_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "action": str(action),
        "archive": str(Path(archive).resolve()),
        "target": str(addon_root()),
        "target_version": str(target_version),
        "current_version": VERSION,
        "current_backup": str(Path(current_backup).resolve()),
        "remove_after_apply": action == "update",
    }
    pending = state_dir / "pending.json"
    temporary = pending.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(pending)
    return pending


def download_and_stage_update(update_info, progress_callback=None):
    if not _online_access_allowed():
        raise RuntimeError("Blender online access is disabled in Preferences")
    remote = str(update_info.get("remote_version") or "")
    if _version_tuple(remote) <= _version_tuple(VERSION):
        raise ValueError("No newer Blender release is available")
    package_url = str(update_info.get("package_url") or "")
    package_sha256 = str(update_info.get("package_sha256") or "").lower()
    if not package_url or not re.fullmatch(r"[0-9a-f]{64}", package_sha256):
        raise ValueError("Update manifest is missing a verified release package")
    state_dir = update_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    staged = state_dir / "pending_update.zip"
    with tempfile.TemporaryDirectory(prefix="BakeToolsDownload-") as temporary_name:
        downloaded = Path(temporary_name) / "update.zip"
        _emit_progress(progress_callback, 2, "Connecting to GitHub")
        _download_package(package_url, downloaded, package_sha256, progress_callback)
        validate_release_archive(downloaded)
        shutil.copy2(downloaded, staged)
    backup = _backup_current_release(VERSION, progress_callback)
    _write_pending("update", staged, remote, backup)
    _emit_progress(progress_callback, 100, "Update ready; restart Blender")
    return str(staged)


def _install_state():
    path = update_state_dir() / "install_state.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError, TypeError):
        return {}


def rollback_info():
    state = _install_state()
    archive = Path(str(state.get("previous_archive") or ""))
    previous = str(state.get("previous_version") or "")
    available = bool(previous and archive.is_file() and _version_tuple(previous) != _version_tuple(VERSION))
    return {
        "available": available,
        "previous_version": previous if available else "",
        "archive": str(archive) if available else "",
    }


def stage_rollback(archive):
    source = validate_release_archive(archive)
    match = re.search(r"-(\d+(?:\.\d+)+)-win64\.zip$", source.name, re.I)
    target_version = match.group(1) if match else "previous"
    state_dir = update_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    staged = state_dir / "pending_rollback.zip"
    shutil.copy2(source, staged)
    backup = _backup_current_release(VERSION)
    _write_pending("rollback", staged, target_version, backup)
    return str(staged)
