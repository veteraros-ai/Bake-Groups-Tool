"""Run with Maya mayapy and QT_QPA_PLATFORM=offscreen."""
import json
import os
import sys
import tempfile

source = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, source)

import bg_guide
import bg_guide_manual


def run():
    app = bg_guide.QtWidgets.QApplication.instance() or bg_guide.QtWidgets.QApplication([])
    for language in ("ru", "en"):
        document = bg_guide_manual.build_document(language)
        assert document["manual_version"] == 5
        assert any(entry.get("anchor") == "release.whats_new"
                   and entry.get("type") == "rect" for entry in document["items"])
        assert len(document["items"]) > 100
        frame_by_anchor = {entry["anchor"]: entry for entry in document["items"]
                           if entry["type"] == "rect" and entry.get("anchor")}
        image_count = {}
        for entry in document["items"]:
            if entry["type"] == "image":
                assert os.path.isfile(os.path.join(source, "guide_assets", entry["asset"]))
                image_count[entry["anchor"]] = image_count.get(entry["anchor"], 0) + 1
                frame = frame_by_anchor[entry["anchor"]]
                assert frame["x"] <= entry["x"] < entry["x"] + entry["w"] <= frame["x"] + frame["w"]
                assert frame["y"] <= entry["y"] < entry["y"] + entry["h"] <= frame["y"] + frame["h"]
        assert image_count["hp.find_similar"] == 2
        assert image_count["export.settings"] == 2
        assert image_count["chapter.create"] >= 4
        assert image_count["groups.row"] >= 4
        assert {entry["asset"] for entry in document["items"]
                if entry.get("anchor") == "release.whats_new" and entry["type"] == "image"} == {
                    "release_help.png", "release_standard_fbx.png",
                    "release_marmoset_target.png", "release_bridge_setup.png",
                    "release_bridge_controls.png"}
    with tempfile.TemporaryDirectory(prefix="bake_guide_manual_") as folder:
        test_language = os.environ.get("BG_GUIDE_TEST_LANGUAGE")
        if test_language in ("ru", "en"):
            bg_guide._language = lambda chosen=test_language: chosen
        bg_guide._document_path = lambda language: os.path.join(folder, "guide.json")
        host = bg_guide.QtWidgets.QMainWindow()
        owned = bg_guide.GuideWindow(parent=host)
        assert owned.parentWidget() is host
        assert bool(owned.windowFlags() & bg_guide.QtCore.Qt.Tool)
        assert not bool(owned.windowFlags() & bg_guide.QtCore.Qt.WindowStaysOnTopHint)
        assert owned.windowModality() == bg_guide.QtCore.Qt.NonModal
        owned.close()
        window = bg_guide.GuideWindow()
        window.go_to("release.whats_new")
        app.processEvents()
        release = next(item for item in window.scene.items()
                       if item.data(1) == "release.whats_new" and item.data(0) == "rect")
        view_scene = window.view.mapToScene(window.view.viewport().rect()).boundingRect()
        assert view_scene.contains(release.sceneBoundingRect()), "Release card must fit the viewport"
        window.go_to("overview")
        preview_path = os.environ.get("BG_GUIDE_PREVIEW")
        if preview_path:
            window.show()
            window.go_to(os.environ.get("BG_GUIDE_PREVIEW_TOPIC", "overview"))
            app.processEvents()
            assert window.grab().save(preview_path)
        assert window.scene.itemsBoundingRect().height() > 2000
        assert any(item.data(3) for item in window.scene.items() if item.data(0) == "image")
        by_anchor = {}
        for item in window.scene.items():
            if item.data(1):
                by_anchor.setdefault(item.data(1), []).append(item)
        for key, elements in by_anchor.items():
            frames = [item for item in elements if item.data(0) == "rect"]
            if len(frames) != 1:
                continue  # extra user-created context cards are tested separately
            frame_rect = frames[0].sceneBoundingRect()
            images = [item for item in elements if item.data(0) == "image"]
            for item in elements:
                if item is frames[0]:
                    continue
                assert item.sceneBoundingRect().bottom() <= frame_rect.bottom() + 1, key
            for image in images:
                for other in elements:
                    if other is not image and other is not frames[0]:
                        assert not image.sceneBoundingRect().intersects(other.sceneBoundingRect()), key
        close_hint = bg_guide.QtCore.Qt.WindowCloseButtonHint
        assert bool(window.windowFlags() & close_hint)
        assert all(not bar.isVisible() for bar in
                   window.findChildren(bg_guide.QtWidgets.QToolBar))
        assert not bool(window.windowFlags() & bg_guide.QtCore.Qt.WindowStaysOnTopHint)
        for pinned in (True, False, True, False):
            window.pin_action.setChecked(pinned)
            app.processEvents()
            assert bool(window.windowFlags() & close_hint)
            assert window.isEnabled()
        window.edit_action.setChecked(True)
        window.add_kind("image")
        app.processEvents()
        assert window._dialogs and not window._dialogs[-1].isModal()
        assert window.isEnabled()
        window._dialogs[-1].close()
        app.processEvents()
        target = next(item for item in window.scene.items() if item.data(0) == "rect")
        target.setSelected(True)
        window.change_color()
        app.processEvents()
        assert window._dialogs and not window._dialogs[-1].isModal()
        assert window.isEnabled()
        window.add_kind("text")
        window._commit()
        with open(window.path, encoding="utf-8") as stream:
            saved = json.load(stream)
        assert any("asset" in entry and "png" not in entry for entry in saved["items"])
        assert window.close(), "Guide must close even while its color dialog is open"
        app.processEvents()
        restored = bg_guide.GuideWindow()
        restored.go_to("hp.find_similar")
        assert restored.topic_box.currentData() == "hp.find_similar"
        assert restored.close()
        app.processEvents()
        settings = bg_guide.QtCore.QSettings(
            os.path.join(folder, "seen.ini"), bg_guide.QtCore.QSettings.IniFormat)
        settings.setValue("whats_new_seen_version", "1.4.5")
        assert bg_guide.whats_new_due("1.4.5", settings)
        bg_guide.mark_whats_new_seen("1.4.5", settings)
        assert not bg_guide.whats_new_due("1.4.5", settings)
        assert bg_guide.whats_new_due("1.4.6", settings)
    print("Guide manual and close-button smoke passed")


if __name__ == "__main__":
    run()
