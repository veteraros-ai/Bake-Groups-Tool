"""Guide translations and live language switching (Maya mayapy, offscreen)."""
import os
import sys
import tempfile
import copy

source = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, source)

import bg_guide
import bg_guide_manual
import bg_guide_translations as translations
import bg_localization
import bg_main_window


def _text(document, key, prefix=None):
    texts = [entry["text"] for entry in document["items"]
             if entry.get("anchor") == key and entry.get("type") == "text"]
    if prefix:
        return next((text for text in texts if text.startswith(prefix)), None)
    return texts[0] if texts else None


class LanguageHarness(object):
    set_language_ui = bg_main_window.BakeManagerUI.set_language_ui

    def __init__(self, guide):
        self._guide_window = guide
        self.active_root_id = None
        self.refresh_count = 0

    def refresh_localized_ui(self):
        self.refresh_count += 1


def run():
    app = bg_guide.QtWidgets.QApplication.instance() or bg_guide.QtWidgets.QApplication([])
    keys = {card[0] for section in bg_guide_manual.SECTIONS for card in section[5]}
    captions = {figure[1] for section in bg_guide_manual.SECTIONS
                for card in section[5] for row in card[5] for figure in row if figure[1]}
    for code, title_prefix, card_title in (
            ("ja", "バージョン", "メッシュを結合"),
            ("zh-CN", "版本", "合并网格")):
        assert set(translations.CARDS[code]) == keys
        assert set(translations.CAPTIONS[code]) == captions
        assert len(translations.RELEASE[code]["features"]) == 6
        document = bg_guide_manual.build_document(code)
        assert _text(document, "release.whats_new", title_prefix)
        assert _text(document, "hp.prepare.combine") == card_title
        assert _text(document, "chapter.create") != _text(
            bg_guide_manual.build_document("en"), "chapter.create")
        previous = copy.deepcopy(document)
        heading = next(item for item in previous["items"] if item.get("type") == "text"
                       and item.get("anchor") == "release.whats_new"
                       and item.get("text", "").startswith(title_prefix))
        heading["text"] = heading["text"].replace(bg_guide_manual.CURRENT_VERSION,
                                                  "0.0.0")
        refreshed, changed = bg_guide._upgrade_document(previous, code)
        assert changed and _text(refreshed, "release.whats_new", title_prefix) == (
            _text(document, "release.whats_new", title_prefix))

    with tempfile.TemporaryDirectory(prefix="bake_guide_languages_") as folder:
        original_path = bg_guide._document_path
        original_message = getattr(bg_main_window.cmds, "inViewMessage", None)
        previous_language = bg_localization.current_language()
        bg_guide._document_path = lambda language: os.path.join(folder, language + ".json")
        bg_main_window.cmds.inViewMessage = lambda **kwargs: None
        guide = None
        try:
            bg_localization.set_language("en")
            guide = bg_guide.GuideWindow(initial_topic="hp.prepare.combine")
            harness = LanguageHarness(guide)
            assert guide.isVisible()
            guide.edit_action.setChecked(True)
            guide.add_kind("text")
            note_count = len(guide.scene.items())
            for language, expected_title in (("ja", "メッシュを結合"),
                                             ("zh-CN", "合并网格"),
                                             ("ru", "Объединить меши"),
                                             ("en", "Combine meshes")):
                harness.set_language_ui(language)
                app.processEvents()
                assert harness._guide_window is guide
                assert guide.isVisible() and guide.language == language
                assert guide.topic_box.currentData() == "hp.prepare.combine"
                assert guide.topic_box.currentText() == expected_title
                assert harness.refresh_count > 0
                assert os.path.isfile(os.path.join(folder, "en.json"))
            assert len(guide.scene.items()) == note_count
            assert any(item.data(0) == "text" and
                       item.toPlainText().startswith("Double-click to edit")
                       for item in guide.scene.items())
        finally:
            if guide is not None:
                guide.close()
                app.processEvents()
            bg_guide._document_path = original_path
            if original_message is None:
                del bg_main_window.cmds.inViewMessage
            else:
                bg_main_window.cmds.inViewMessage = original_message
            bg_localization.set_language(previous_language)
    print("Guide Japanese/Chinese translation and live switch passed")


if __name__ == "__main__":
    run()
