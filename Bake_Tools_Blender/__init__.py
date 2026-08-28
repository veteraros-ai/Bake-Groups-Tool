"""Blender add-on entry point and restart-safe package activator."""


def _update_state_dir():
    from pathlib import Path
    return Path.home() / ".bake_tools_blender" / "updates"


def _safe_archive_member(name):
    from pathlib import Path
    normalized = str(name).replace("\\", "/")
    return not normalized.startswith(("/", "\\")) and ".." not in Path(normalized).parts


def _apply_pending_package():
    """Apply an explicitly staged, validated package before native modules load."""
    import json
    from pathlib import Path
    import shutil
    import tempfile
    import zipfile

    target = Path(__file__).resolve().parent
    state_dir = _update_state_dir()
    pending = state_dir / "pending.json"
    if not pending.is_file():
        return
    try:
        payload = json.loads(pending.read_text(encoding="utf-8"))
        archive_path = Path(payload["archive"]).resolve()
        if Path(payload["target"]).resolve() != target or not archive_path.is_file():
            raise ValueError("Invalid pending Bake Tools package")
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = archive.infolist()
            if not members:
                raise ValueError("Empty Bake Tools package")
            for member in members:
                if not _safe_archive_member(member.filename):
                    raise ValueError("Unsafe path in Bake Tools package")
            names = {member.filename.replace("\\", "/").rstrip("/") for member in members}
            required = {
                "Bake_Tools_Blender/__init__.py",
                "Bake_Tools_Blender/addon/bake_tools_blender/__init__.py",
            }
            if not required.issubset(names):
                raise ValueError("Incomplete Bake Tools package")
            with tempfile.TemporaryDirectory(prefix="BakeToolsInstall-") as temp_name:
                temporary = Path(temp_name)
                archive.extractall(temporary)
                source = temporary / "Bake_Tools_Blender"
                if not source.is_dir():
                    raise ValueError("Bake Tools package root was not found")
                shutil.copytree(source, target, dirs_exist_ok=True)
        install_state = {
            "current_version": str(payload.get("target_version") or ""),
            "previous_version": str(payload.get("current_version") or ""),
            "previous_archive": str(payload.get("current_backup") or ""),
            "last_action": str(payload.get("action") or "update"),
        }
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "install_state.json").write_text(
            json.dumps(install_state, indent=2), encoding="utf-8"
        )
        pending.unlink(missing_ok=True)
        if payload.get("remove_after_apply"):
            archive_path.unlink(missing_ok=True)
        (state_dir / "last_result.txt").write_text(
            "Package applied successfully", encoding="utf-8"
        )
    except Exception as exc:
        failed = state_dir / "pending.failed.json"
        if failed.exists():
            failed.unlink()
        pending.replace(failed)
        (state_dir / "last_result.txt").write_text(
            "Package failed: {}".format(exc), encoding="utf-8"
        )


_apply_pending_package()

bl_info = {
    "name": "Bake Groups Tool",
    "author": "Veteraros AI",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Bake Tools",
    "description": "Prepare HP/LP bake groups, cages, smoothing and FBX exports",
    "category": "3D View",
}

from .addon import bake_tools_blender as _implementation


def register():
    _implementation.register()


def unregister():
    _implementation.unregister()
