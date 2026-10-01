"""Run with Maya mayapy and QT_QPA_PLATFORM=offscreen."""
import json
import os
import sys
import tempfile

source = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, source)

import bg_guide
import bg_guide_manual


def frames(document):
    return {entry.get("anchor"): entry for entry in document["items"]
            if entry.get("type") == "rect" and entry.get("anchor")}


def run():
    app = bg_guide.QtWidgets.QApplication.instance() or bg_guide.QtWidgets.QApplication([])
    existing = os.environ.get("BG_GUIDE_SAVED")
    if existing and os.path.isfile(existing):
        with open(existing, encoding="utf-8") as stream:
            saved_board = json.load(stream)
        language = saved_board.get("language", "en")
        upgraded, changed = bg_guide._upgrade_document(saved_board, language)
        assert changed
        assert "control.combine" not in frames(upgraded)
        assert "control.separate" not in frames(upgraded)
        assert "control.algorithm" not in frames(upgraded)
        assert "control.create-group" not in frames(upgraded)
        shipped = frames(bg_guide_manual.build_document(language))
        for key in ("hp.prepare.combine", "hp.prepare.separate", "hp.algorithm",
                    "groups.create"):
            assert frames(upgraded)[key]["y"] == shipped[key]["y"]
        print("Existing saved board migration checked (read-only)")
    for language in ("en", "ru"):
        bundled = bg_guide_manual.build_document(language)
        legacy = json.loads(json.dumps(bundled))
        legacy.pop("manual_version")
        obsolete = [
            {"type": "rect", "anchor": "control.combine", "x": 80, "y": 20000,
             "w": 390, "h": 246},
            {"type": "text", "anchor": "control.combine", "x": 100, "y": 20016,
             "w": 350, "text": "Combine"},
            {"type": "text", "anchor": "control.combine", "x": 100, "y": 20069,
             "w": 350, "text": "Combine"},
        ]
        legacy["items"].extend(obsolete)
        legacy["items"].append({"type": "rect", "anchor": "my.reference", "x": 20,
                                "y": 21000, "w": 400, "h": 250, "color": "#fff"})
        legacy["items"].append({"type": "text", "anchor": "my.reference", "x": 40,
                                "y": 21020, "w": 360, "text": "Keep my note"})
        legacy["items"].append({"type": "arrow", "anchor": "hp.prepare.combine",
                                "x": 200, "y": 500, "w": 80, "h": 20})
        migrated, changed = bg_guide._upgrade_document(legacy, language)
        assert changed and migrated["manual_version"] == 5
        assert migrated == bundled
        assert "control.combine" not in frames(migrated)
        assert frames(migrated)["hp.prepare.combine"]["y"] == frames(bundled)["hp.prepare.combine"]["y"]
        assert "my.reference" not in frames(migrated)
        assert not any(entry.get("text") == "Keep my note" for entry in migrated["items"])
        again, changed = bg_guide._upgrade_document(migrated, language)
        assert not changed and again == migrated

        # A saved 1.4.5 board has the current manual schema, but its release
        # heading must still follow the installed plugin version.
        previous = json.loads(json.dumps(bundled))
        release_title = next(entry for entry in previous["items"]
                             if entry.get("anchor") == "release.whats_new"
                             and entry.get("type") == "text"
                             and ("New in version" in entry.get("text", "")
                                  or "Что нового в версии" in entry.get("text", "")))
        release_title["text"] = ("Что нового в версии 1.4.5" if language == "ru"
                                 else "New in version 1.4.5")
        previous["items"].append({"type": "text", "anchor": "my.note",
                                  "text": "Keep this custom note", "x": 40, "y": 9000,
                                  "w": 300, "size": 12})
        refreshed, changed = bg_guide._upgrade_document(previous, language)
        assert changed
        assert release_title["text"].endswith("1.4.5")  # input is not mutated
        assert any(entry.get("anchor") == "release.whats_new" and
                   entry.get("text", "").endswith("1.4.6")
                   for entry in refreshed["items"])
        assert refreshed["items"][-1] == previous["items"][-1]
        assert not bg_guide._upgrade_document(refreshed, language)[1]

        with tempfile.TemporaryDirectory(prefix="bake_guide_migrate_") as folder:
            path = os.path.join(folder, "guide.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(legacy, stream)
            bg_guide._language = lambda chosen=language: chosen
            bg_guide._document_path = lambda chosen, p=path: p
            window = bg_guide.GuideWindow()
            assert window._migration_pending
            window.go_to("hp.prepare.combine")
            assert window.topic_box.currentData() == "hp.prepare.combine"
            assert window.close()
            app.processEvents()
            with open(path, encoding="utf-8") as stream:
                saved = json.load(stream)
            assert saved["manual_version"] == 5
            assert "control.combine" not in frames(saved)
            assert os.path.isfile(path + ".pre_manual_v5.json")
    print("Guide migration passed")


if __name__ == "__main__":
    run()
