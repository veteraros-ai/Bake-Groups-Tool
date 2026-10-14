"""Context-help instructions, saved-board refresh and real Guide navigation."""
import copy
import ast
import os
import sys
import tempfile

source = os.environ.get("BAKE_GROUPS_TEST_SCRIPT_DIR") or os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, source)

import bg_guide
import bg_guide_manual
import bg_main_window
from test_guide_help_mode_qt import HelpHarness, click


class GuideHarness(HelpHarness):
    show_guide = bg_main_window.BakeManagerUI.show_guide

    def __init__(self):
        super(GuideHarness, self).__init__()
        self._guide_window = None


def run():
    assert bg_guide_manual.release_title("ru") == "Что нового в версии 1.4.6 - 1.4.7"
    with open(os.path.join(source, "launcher.py"), encoding="utf-8-sig") as stream:
        launch_tree = ast.parse(stream.read())
    reload_modules = next(ast.literal_eval(node.value) for node in launch_tree.body
                          if isinstance(node, ast.Assign) and
                          any(isinstance(target, ast.Name) and target.id == "modules_to_reload"
                              for target in node.targets))
    assert reload_modules.index("bg_guide_translations") < reload_modules.index("bg_guide_manual")
    app = bg_guide.QtWidgets.QApplication.instance() or bg_guide.QtWidgets.QApplication([])
    original_path, original_language = bg_guide._document_path, bg_guide._language
    with tempfile.TemporaryDirectory(prefix="bake_guide_context_") as folder:
        bg_guide._document_path = lambda language: os.path.join(folder, language + ".json")
        ui = GuideHarness()
        ui.show()
        app.installEventFilter(ui)
        try:
            for language in ("en", "ru", "ja", "zh-CN"):
                bg_guide._language = lambda chosen=language: chosen
                bundled = bg_guide_manual.build_document(language)
                help_items = [entry for entry in bundled["items"]
                              if entry.get("anchor") == "guide.help"]
                assert sum(entry["type"] == "rect" for entry in help_items) == 1
                assert sum(entry["type"] == "image" for entry in help_items) == 2
                body = next(entry["text"] for entry in help_items if entry["type"] == "text"
                            and entry["text"].startswith("1."))
                assert all(text in body for text in ("2.", "3.", "Help", "Esc"))
                legacy = copy.deepcopy(bundled)
                legacy.pop("help_card_revision")
                frame = next(entry for entry in legacy["items"]
                             if entry.get("anchor") == "guide.help" and entry["type"] == "rect")
                frame["x"] += 11
                frame["y"] += 17
                note = {"type": "text", "anchor": "my.note", "text": "Keep this note"}
                legacy["items"].append(note)
                title = next(entry for entry in legacy["items"]
                             if entry.get("anchor") == "release.whats_new" and entry["type"] == "text")
                title["text"] = {
                    "en": "New in version 1.4.6", "ru": "Что нового в версии 1,4,6 - 1,4,7",
                    "ja": "バージョン 1.4.6 の新機能", "zh-CN": "版本 1.4.6 的新功能"}[language]
                before = copy.deepcopy(legacy)
                refreshed, changed = bg_guide._upgrade_document(legacy, language)
                assert changed and legacy == before, "Migration mutated the saved board"
                new_frame = next(entry for entry in refreshed["items"]
                                 if entry.get("anchor") == "guide.help" and entry["type"] == "rect")
                assert (new_frame["x"], new_frame["y"]) == (frame["x"], frame["y"])
                assert note in refreshed["items"]
                assert all(entry in refreshed["items"] for entry in legacy["items"]
                           if entry.get("anchor") not in ("guide.help", "release.whats_new"))
                assert not bg_guide._upgrade_document(refreshed, language)[1]

                click(app, ui.btn_help)
                guide = ui._guide_window
                assert guide.isVisible() and guide.topic_box.currentData() == "guide.help"
                assert ui._help_mode, "Opening instructions disarmed context help"
                assert guide.language == language
                frame_item = next(item for item in guide.scene.items()
                                  if item.data(0) == "rect" and item.data(1) == "guide.help")
                visible = guide.view.mapToScene(guide.view.viewport().rect()).boundingRect()
                assert visible.contains(frame_item.sceneBoundingRect()), "Instructions are clipped"
                click(app, ui.target)
                assert ui._guide_window is guide and guide.topic_box.currentData() == "hp.analyze"
                assert ui.invoked == 0 and not ui._help_mode
                assert app.overrideCursor() is None
                click(app, ui.btn_help)
                assert ui._guide_window is guide and guide.topic_box.currentData() == "guide.help"
                click(app, ui.btn_help)
                assert guide.close()
                app.sendPostedEvents(None, bg_guide.QtCore.QEvent.DeferredDelete)
                app.processEvents()
                click(app, ui.btn_help)
                assert ui._guide_window is not guide
                assert ui._guide_window.topic_box.currentData() == "guide.help"
                assert ui._guide_window._serialize()["help_card_revision"] == 1
                ui.cancel_help_mode()
                ui._guide_window.close()
                app.sendPostedEvents(None, bg_guide.QtCore.QEvent.DeferredDelete)
                app.processEvents()
        finally:
            ui.cancel_help_mode()
            app.removeEventFilter(ui)
            ui.close()
            bg_guide._document_path, bg_guide._language = original_path, original_language
    print("Guide context-help card/navigation/migration passed in four languages")


if __name__ == "__main__":
    run()
