"""Run with Maya mayapy and QT_QPA_PLATFORM=offscreen."""
import json
import os
import sys
import tempfile

source = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, source)

import bg_guide


def run():
    app = bg_guide.QtWidgets.QApplication.instance() or bg_guide.QtWidgets.QApplication([])
    with tempfile.TemporaryDirectory(prefix="bake_guide_test_") as temp:
        bg_guide._document_path = lambda language: os.path.join(temp, "guide.json")
        window = bg_guide.GuideWindow()
        assert len(window.scene.items()) >= len(bg_guide.TOPICS) * 3
        starter_count = len([item for item in window.scene.items() if item.data(0)])
        window.go_to("hp.analyze")
        assert window.topic_box.currentData() == "hp.analyze"
        window.edit_action.setChecked(True)
        for kind in ("text", "line", "arrow", "dashed", "wavy", "rect", "ellipse"):
            window.add_kind(kind)
        image = bg_guide.QtGui.QImage(32, 16, bg_guide.QtGui.QImage.Format_RGB32)
        image.fill(bg_guide.QtGui.QColor("#e08040"))
        assert window._add_image(image)
        window._commit()
        with open(window.path, encoding="utf-8") as stream:
            data = json.load(stream)
        kinds = {item["type"] for item in data["items"]}
        assert {"text", "image", "line", "arrow", "dashed", "wavy", "rect", "ellipse"} <= kinds
        window.undo()
        assert len([item for item in window.scene.items() if item.data(0)]) == starter_count
        window.redo()
        assert len([item for item in window.scene.items() if item.data(0)]) == len(data["items"])
        assert not window.ensure_context_card("control.custom", "Custom", "From a Maya tooltip")
        window.go_to("control.custom")
        assert window.topic_box.currentData() == "overview"
        window._commit()
        window.close()
        app.processEvents()
        restored = bg_guide.GuideWindow()
        assert not any(item.data(1) == "control.custom" for item in restored.scene.items())
        assert restored.topic_box.findData("control.custom") < 0
        restored.close()
        app.processEvents()
    print("Guide Qt smoke passed")


if __name__ == "__main__":
    run()
