"""GUI smoke test for non-modal localized progress and cancellation."""

from __future__ import annotations

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender.progress import (  # noqa: E402
    ProgressCancelled,
    cancel_task,
    progress_scope,
)
from Bake_Tools_Blender.addon.bake_tools_blender.qt_window import (  # noqa: E402
    QtCore,
    show_manager,
    shutdown_manager,
)


window = show_manager()
bpy.context.scene.bake_tools_settings.language = "RU"
window.refresh_from_store(force=True)


def verify_progress():
    task_id = ""
    try:
        try:
            with progress_scope("Find Similar", "Reading geometry: Cube") as progress:
                task_id = progress.task_id
                dialog = window._progress_dialogs[task_id]
                assert dialog.isVisible()
                assert dialog.windowModality() == QtCore.Qt.WindowModality.NonModal
                assert dialog.windowTitle() == "Найти похожие", dialog.windowTitle()
                assert "Чтение геометрии" in dialog.labelText(), dialog.labelText()
                assert window.isEnabled()
                QtCore.QTimer.singleShot(0, lambda: cancel_task(task_id))
                progress.update(50, "Matching: Cube")
        except ProgressCancelled:
            pass
        else:
            raise AssertionError("Progress cancellation was not observed")
        assert task_id not in window._progress_dialogs
        assert window.isVisible() and window.isEnabled()
        print("BAKE_TOOLS_PROGRESS_GUI_OK nonmodal=1 localized=1 cancel=1")
    finally:
        shutdown_manager()
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(verify_progress, first_interval=1.0)
