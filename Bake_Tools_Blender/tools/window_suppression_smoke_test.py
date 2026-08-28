"""Pure regression for reusable manager suppression state."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

from Bake_Tools_Blender.addon.bake_tools_blender import blender_bridge  # noqa: E402


window = SimpleNamespace(
    _bt_suppression_reasons={"sidebar_tab", "blender_header_interaction"},
    _bt_native_suppressed=True,
    _bt_header_mouse_down=True,
    _bt_header_popup_guard=True,
    _bt_header_popup_expire=99.0,
    _bt_header_popup_closing=88.0,
    _bt_header_popup_anchor=(100, 20),
)
calls = []
original = blender_bridge._restore_qt_window_native_visibility


def restore_stub(target, base_exstyle=None):
    calls.append((base_exstyle, set(target._bt_suppression_reasons)))
    target._bt_native_suppressed = False
    return True


try:
    blender_bridge._restore_qt_window_native_visibility = restore_stub
    assert blender_bridge.reset_qt_window_suppression(window)
finally:
    blender_bridge._restore_qt_window_native_visibility = original

assert calls == [(None, set())]
assert window._bt_suppression_reasons == set()
assert not window._bt_native_suppressed
assert not window._bt_header_mouse_down
assert not window._bt_header_popup_guard
assert window._bt_header_popup_expire == 0.0
assert window._bt_header_popup_closing == 0.0
assert window._bt_header_popup_anchor is None
print("BAKE_TOOLS_WINDOW_SUPPRESSION_OK reset_on_reopen=1 stale_guard=0")
