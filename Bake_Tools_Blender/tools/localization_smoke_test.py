"""Runtime localization smoke test for the persistent Qt widget tree."""

from __future__ import annotations

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender import localization as i18n  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.qt_window import (  # noqa: E402
    show_manager,
    shutdown_manager,
)
from Bake_Tools_Blender.addon.bake_tools_blender.ui import _tr as native_tr  # noqa: E402


languages = i18n.available_languages()
assert {item["state_code"] for item in languages} == {"EN", "RU", "JA", "ZH_CN"}
assert i18n.text("Assign LP", "RU") == "Назначить LP"
assert i18n.text("Assign LP", "JA") == "LPを割り当て"
assert i18n.text("Assign LP", "ZH_CN") == "分配 LP"
assert i18n.text("Select a chapter in the TOC.", "RU") == "Выберите главу в оглавлении."
assert i18n.runtime_text("Ready.", "RU") == "Готово."
assert i18n.runtime_text("Separate: 1 source mesh(es) -> 2 part(s)", "RU") == (
    "Разделение: 1 исходных мешей → 2 частей"
)

window = show_manager()


def verify():
    state = bpy.context.scene.bake_tools_settings
    state.language = "RU"
    assert native_tr(bpy.context, "Close Bake Group Manager") == "Закрыть Bake Group Manager"
    window.request_refresh()
    window.refresh_from_store(force=True)
    assert window.language_button.text() == "Язык", window.language_button.text()
    assert window.subgroups_title.text() == "САБГРУППЫ", window.subgroups_title.text()
    assert window.algorithm_button.text().endswith("Алгоритм"), window.algorithm_button.text()

    # Relocalize the same widgets, proving source keys survive a language switch.
    state.language = "JA"
    window.request_refresh()
    window.refresh_from_store(force=True)
    assert window.language_button.text() == "言語", window.language_button.text()
    assert window.subgroups_title.text() == "サブグループ", window.subgroups_title.text()
    print("BAKE_TOOLS_LOCALIZATION_OK languages={} runtime_switch=RU->JA".format(len(languages)))
    shutdown_manager()
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(verify, first_interval=1.0)
