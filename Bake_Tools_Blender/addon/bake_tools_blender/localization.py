"""JSON-backed runtime localization shared by the Blender Qt manager.

English source text remains the stable key.  Widgets remember that key in a Qt
dynamic property, so switching Scene language can relocalize the existing tree
without rebuilding it or reverse-translating its current caption.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from string import Formatter


_CATALOG_DIR = Path(__file__).resolve().parent / "localization"
_CACHE = {}
_REVERSE_CACHE = None
_RUNTIME_TEMPLATE_CACHE = None
_CODE_MAP = {
    "EN": "en", "RU": "ru", "JA": "ja", "ZH_CN": "zh-CN",
    "ENGLISH": "en", "RUSSIAN": "ru", "JAPANESE": "ja", "CHINESE": "zh-CN",
}
_STATE_CODE_MAP = {"en": "EN", "ru": "RU", "ja": "JA", "zh-CN": "ZH_CN"}
_RUNTIME_SOURCE_KEYS = (
    "Picked {} {}: {}", "Created chapter: {} | LP materials: {}", "Added subgroup: {}",
    "Analyze HP: {} HP -> {} group(s); LP matched {}, unmatched {}",
    "Assign LP: matched {} of {} mesh(es); unmatched {}", "Combine: {} mesh(es) -> {}",
    "Separate: {} source mesh(es) -> {} part(s)",
    "Find ZBrush: selected {} HP mesh(es), threshold {}%, best {:.1f}%",
    "Find ZBrush: no HP meshes with {}%+ triangular faces",
    "Check Mesh: {} issue mesh(es) found and selected",
    "ZBrush layer: {} selected mesh(es), {} newly added",
    "ZBrush layer: selected {} remembered mesh(es)",
    "Matching HP to LP: {}", "Fast LP match: {}", "Precise LP match: {}",
    "Creating chapter: {}", "Reading geometry: {}", "Matching: {}",
    "Found {} mesh(es)", "Checking ZBrush topology: {}", "Separating: {}",
    "Checking mesh: {}", "Restoring: {}", "Smoothing: {}",
    "Exporting: {}", "Exported {} file(s)",
)


def canonical_language(value):
    raw = str(value or "en").strip()
    return _CODE_MAP.get(raw.upper(), raw if raw in _STATE_CODE_MAP else "en")


def state_language(value):
    return _STATE_CODE_MAP.get(canonical_language(value), "EN")


def available_languages():
    path = _CATALOG_DIR / "languages.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        data = {}
    result = []
    for item in data.get("languages", ()):
        code = canonical_language(item.get("code"))
        result.append({
            "label": str(item.get("label") or code),
            "code": code,
            "state_code": state_language(code),
            "file": str(item.get("file") or (code + ".json")),
        })
    return tuple(result) or ({"label": "English", "code": "en", "state_code": "EN", "file": "en.json"},)


def load_language(language):
    code = canonical_language(language)
    if code in _CACHE:
        return _CACHE[code]
    file_name = next(
        (item["file"] for item in available_languages() if item["code"] == code),
        code + ".json",
    )
    try:
        loaded = json.loads((_CATALOG_DIR / file_name).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        loaded = {}
    catalog = {
        "texts": dict(loaded.get("texts") or {}),
        "tooltips": dict(loaded.get("tooltips") or {}),
    }
    # Keep the original Maya dictionaries intact and layer Blender-only UI
    # terminology on top. This also makes future catalog updates mergeable.
    try:
        overrides = json.loads((_CATALOG_DIR / "blender_overrides.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        overrides = {}
    language_overrides = overrides.get(code) or {}
    catalog["texts"].update(language_overrides.get("texts") or {})
    catalog["tooltips"].update(language_overrides.get("tooltips") or {})
    # Publication-only captions live separately so the large Maya-derived
    # dictionaries remain easy to compare and update.
    try:
        publication = json.loads(
            (_CATALOG_DIR / "publication_overrides.json").read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError, TypeError):
        publication = {}
    publication_overrides = publication.get(code) or {}
    catalog["texts"].update(publication_overrides.get("texts") or {})
    catalog["tooltips"].update(publication_overrides.get("tooltips") or {})
    _CACHE[code] = catalog
    return catalog


def _format(value, replacements):
    if not replacements:
        return value
    try:
        return str(value).format(**replacements)
    except (KeyError, IndexError, ValueError):
        return str(value)


def text(key, language="EN", default=None, **replacements):
    key = str(key or "")
    value = load_language(language)["texts"].get(key, default if default is not None else key)
    return _format(value, replacements)


def tooltip(key, language="EN", default=None, **replacements):
    key = str(key or "")
    value = load_language(language)["tooltips"].get(key, default if default is not None else "")
    return _format(value, replacements)


def _reverse_text_map():
    global _REVERSE_CACHE
    if _REVERSE_CACHE is not None:
        return _REVERSE_CACHE
    reverse = {}
    for language in available_languages():
        for key, value in load_language(language["code"])["texts"].items():
            if value:
                reverse.setdefault(str(value), str(key))
    _REVERSE_CACHE = reverse
    return reverse


def source_key_from_value(value):
    return _reverse_text_map().get(str(value or ""), str(value or ""))


def _fallback_label(key):
    key = str(key)
    for prefix in ("placeholder:", "combo:"):
        if key.startswith(prefix):
            return key[len(prefix):]
    return key


# Blender uses shorter visible labels than the Maya window in several places.
# Tooltips must still come from the original Maya help keys so both editions
# describe the same action instead of showing only the abbreviated caption.
_BUTTON_TOOLTIP_ALIASES = {
    "Create Pair": " Create Pair from Picked",
    "Analyze HP": " Analyze HP",
    "Assign LP": " Assign LP Meshes",
    "Check Mesh": "Check Before Analyze",
    "Save": "Save Session",
    "Groups Visible": "Groups Vis",
    "Strict Geo Check": "Strict Geo Check (Resolve Overlaps)",
    "Link adjacent vertices": "Adjacent Vertex Link",
    "▶  Algorithm": "Algorithm",
    "▼  Algorithm": "Algorithm",
    "Delete subgroup": "X",
    "Increase smooth": "+",
    "Decrease smooth": "-",
}


def _source_key(obj, current, property_name="bt_i18n_key"):
    existing = obj.property(property_name) if hasattr(obj, "property") else None
    if existing:
        return str(existing)
    key = source_key_from_value(current)
    if hasattr(obj, "setProperty"):
        obj.setProperty(property_name, key)
    return key


def localize_action(action, language):
    if action is None:
        return
    current = action.text()
    if not current:
        return
    key = _source_key(action, current)
    action.setText(text(key, language, _fallback_label(key)))
    help_text = tooltip(key, language)
    if help_text:
        action.setToolTip(help_text)
        action.setStatusTip(help_text)


def localize_widget(widget, language, qt_modules=None):
    if widget is None:
        return
    if qt_modules is None:
        from .dependencies import enable_pyside6
        qt_modules = enable_pyside6()
    QtCore, QtGui, QtWidgets = qt_modules

    if hasattr(widget, "windowTitle") and hasattr(widget, "setWindowTitle"):
        current = widget.windowTitle()
        if current:
            key = _source_key(widget, current, "bt_i18n_window_key")
            widget.setWindowTitle(text(key, language, _fallback_label(key)))

    if hasattr(widget, "title") and hasattr(widget, "setTitle"):
        try:
            current = widget.title()
            if current:
                key = _source_key(widget, current, "bt_i18n_title_key")
                widget.setTitle(text(key, language, _fallback_label(key)))
        except (AttributeError, RuntimeError, TypeError):
            pass

    # QAbstractButton also covers radio buttons used by material/name dialogs.
    text_widgets = (QtWidgets.QAbstractButton, QtWidgets.QLabel)
    if isinstance(widget, text_widgets):
        current = widget.text()
        if current:
            key = _source_key(widget, current)
            widget.setText(text(key, language, _fallback_label(key)))
            if isinstance(widget, QtWidgets.QAbstractButton):
                help_key = _BUTTON_TOOLTIP_ALIASES.get(str(key), str(key))
                help_text = tooltip(help_key, language)
                if help_text:
                    widget.setToolTip(help_text)
                    widget.setStatusTip(help_text)
                    widget.setProperty("bt_i18n_tooltip_key", help_key)

    if hasattr(widget, "toolTip") and hasattr(widget, "setToolTip"):
        current_tip = widget.toolTip()
        if current_tip:
            key = _source_key(widget, current_tip, "bt_i18n_tooltip_key")
            translated = tooltip(key, language) or text(key, language, current_tip)
            widget.setToolTip(translated)

    if isinstance(widget, QtWidgets.QLineEdit):
        current = widget.placeholderText()
        if current:
            key = _source_key(widget, current, "bt_i18n_placeholder_key")
            if not key.startswith("placeholder:"):
                key = "placeholder:" + key
                widget.setProperty("bt_i18n_placeholder_key", key)
            widget.setPlaceholderText(text(key, language, _fallback_label(key)))

    if isinstance(widget, QtWidgets.QComboBox):
        role = int(QtCore.Qt.ItemDataRole.UserRole) + 37
        for index in range(widget.count()):
            key = widget.itemData(index, role)
            if not key:
                current = widget.itemText(index)
                source = source_key_from_value(current)
                key = source if source.startswith("combo:") else "combo:" + source
                widget.setItemData(index, key, role)
            widget.setItemText(index, text(str(key), language, _fallback_label(key)))

    if isinstance(widget, QtWidgets.QDialogButtonBox):
        for button in widget.buttons():
            current = button.text().replace("&", "")
            key = _source_key(button, current)
            button.setText(text(key, language, current))

    for action in getattr(widget, "actions", lambda: ())():
        localize_action(action, language)


def localize_widget_tree(root, language, qt_modules=None):
    if root is None:
        return root
    if qt_modules is None:
        from .dependencies import enable_pyside6
        qt_modules = enable_pyside6()
    _QtCore, QtGui, QtWidgets = qt_modules
    localize_widget(root, language, qt_modules)
    for widget in root.findChildren(QtWidgets.QWidget):
        localize_widget(widget, language, qt_modules)
    for action in root.findChildren(QtGui.QAction):
        localize_action(action, language)
    return root


def localize_menu(menu, language, qt_modules=None):
    return localize_widget_tree(menu, language, qt_modules)


def _runtime_templates():
    global _RUNTIME_TEMPLATE_CACHE
    if _RUNTIME_TEMPLATE_CACHE is not None:
        return _RUNTIME_TEMPLATE_CACHE
    templates = []
    for key in _RUNTIME_SOURCE_KEYS:
        parsed = list(Formatter().parse(str(key)))
        fields = [field for _literal, field, _spec, _conversion in parsed if field is not None]
        if not fields:
            continue
        pattern = "^" + "".join(
            re.escape(literal) + ("(.+?)" if field is not None else "")
            for literal, field, _spec, _conversion in parsed
        ) + "$"
        literal_size = sum(len(literal) for literal, _field, _spec, _conversion in parsed)
        templates.append((literal_size, str(key), re.compile(pattern)))
    _RUNTIME_TEMPLATE_CACHE = tuple(sorted(templates, reverse=True))
    return _RUNTIME_TEMPLATE_CACHE


def runtime_text(value, language="EN"):
    """Translate one persisted English log line, including formatted values."""
    value = str(value or "")
    catalog = load_language(language)["texts"]
    if value in catalog:
        return str(catalog[value])
    for _size, source, pattern in _runtime_templates():
        match = pattern.match(value)
        if not match:
            continue
        translated = str(catalog.get(source, source))
        replacements = iter(match.groups())
        return re.sub(r"\{[^{}]*\}", lambda _found: next(replacements, ""), translated)
    return value


def runtime_block(value, language="EN"):
    return "\n".join(runtime_text(line, language) for line in str(value or "").splitlines())
