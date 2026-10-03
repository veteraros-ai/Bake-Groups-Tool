"""Export eligible HP directly to FBX through the bundled subdivider."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import tempfile
import time

import bpy

from .hp_binary import UnsupportedFastExport, write
from .mesh_tools import is_zbrush_object
from .object_repository import ObjectRepository


def helper_path():
    return Path(__file__).resolve().parents[2] / "bin" / "bg_obj_subdivider.exe"


def _levels(pairs, state):
    result = {}
    for pair in pairs:
        for subgroup in pair.subgroups:
            for obj in ObjectRepository.valid_members(subgroup, "HP"):
                key = obj.as_pointer()
                result[key] = max(result.get(key, 0), int(subgroup.smooth_level))
    for pair in pairs:
        for obj in ObjectRepository.meshes_under_root(pair, "HP"):
            result.setdefault(obj.as_pointer(), 0)
    return result


def eligible(task, pairs, state):
    if not helper_path().is_file() or state is None:
        return False
    if abs(float(bpy.context.scene.unit_settings.scale_length) - 1.0) > 1.0e-8:
        return False
    if task.lp_objects or not task.filepath.lower().endswith("_hp.fbx"):
        return False
    hp_ids = {obj.as_pointer() for pair in pairs
              for obj in ObjectRepository.meshes_under_root(pair, "HP")}
    return bool(task.objects) and all(obj.as_pointer() in hp_ids for obj in task.objects)


def export(context, task, pairs, state, progress=None):
    """Return False if Blender's FBX path is required for this geometry."""
    if not eligible(task, pairs, state):
        return False
    levels = _levels(pairs, state)
    selected_levels = {
        obj.as_pointer(): 0 if is_zbrush_object(state, obj) else levels.get(obj.as_pointer(), 0)
        for obj in task.objects
    }
    # The bundled helper uses different Catmull-Clark boundary rules from
    # Blender.  Baking Blender's exact subdivision into a BGHP stream is
    # accurate but slower than Blender's native FBX exporter on chapters with
    # many HP objects.  Keep the external writer for unsmoothed HP only.
    if any(selected_levels.values()):
        return False
    target = Path(task.filepath)
    target.parent.mkdir(parents=True, exist_ok=True)
    from .dependencies import enable_pyside6
    _, _, widgets = enable_pyside6()
    app = widgets.QApplication.instance()
    with tempfile.TemporaryDirectory(prefix="BakeGroups_HP_Input_") as input_dir, \
            tempfile.TemporaryDirectory(prefix=".BakeGroups_HP_Output_", dir=target.parent) as output_dir:
        source = Path(input_dir) / "source.bghp"
        staged = Path(output_dir) / target.name
        capture_started = time.perf_counter()
        try:
            write(source, task.objects, selected_levels, progress)
        except UnsupportedFastExport:
            return False
        capture_seconds = time.perf_counter() - capture_started
        command = [str(helper_path()), "--input", str(source), "--output", str(staged),
                   "--level", "0", "--format", "fbx", "--input-binary"]
        with tempfile.TemporaryFile(mode="w+b") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                next_update = 0.0
                while process.poll() is None:
                    now = time.monotonic()
                    if progress is not None and now >= next_update:
                        progress.update(55, "Subdividing HP: {}".format(task.name))
                        next_update = now + 0.2
                    if app is not None:
                        app.processEvents()
                    time.sleep(0.04)
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
            if process.returncode:
                log.seek(0)
                detail = log.read()[-4000:].decode("utf-8", "replace").strip()
                raise RuntimeError("HP subdivision failed ({}): {}".format(process.returncode, detail))
        if os.environ.get("BAKE_TOOLS_PROFILE"):
            print("BAKE_TOOLS_FAST_HP_PROFILE capture={:.3f}s helper={:.3f}s".format(
                capture_seconds, time.perf_counter() - capture_started - capture_seconds))
        if not staged.is_file() or staged.stat().st_size == 0:
            raise RuntimeError("HP subdivision produced an empty FBX")
        if progress:
            progress.update(95, "Finishing HP FBX: {}".format(task.name))
        os.replace(staged, target)
    return True
