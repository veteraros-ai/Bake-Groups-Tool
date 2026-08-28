"""Qt window lifecycle and native visibility state for Bake Tools.

The design borrows the small, useful part of BQt's window manager: keep one
registry for Qt widgets, reuse a window by ``objectName`` and centralize the Qt
application/timer lifecycle.  It deliberately does *not* wrap Blender in Qt or
reparent Qt widgets into Blender's non-Qt GHOST window.

Win32 ownership, Sidebar geometry and popup detection remain in
``blender_bridge``.  Native transparent/click-through state lives here so every
suppression source composes through one state record instead of writing ad-hoc
attributes from several bridge paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import sys
import weakref

import bpy


@dataclass
class _WindowRecord:
    token: int
    name: str
    role: str
    reference: object
    suppression_reasons: set[str] = field(default_factory=set)
    native_suppressed: bool = False
    native_exstyle: int | None = None

    def widget(self):
        try:
            return self.reference()
        except TypeError:
            return None


class QtWindowManager:
    """Own the reusable Qt manager, dialog registry and Blender timer pump."""

    def __init__(self, primary_name="BakeToolsBlenderWindow"):
        self.primary_name = str(primary_name)
        self._application = None
        self._records: dict[int, _WindowRecord] = {}
        self._names: dict[str, int] = {}
        self._primary_token: int | None = None
        self._pump_callback = None
        self._pump_active = False

    @staticmethod
    def _widget_name(widget, fallback):
        try:
            value = str(widget.objectName() or "").strip()
        except (AttributeError, RuntimeError, TypeError):
            value = ""
        return value or fallback

    @staticmethod
    def _widget_alive(widget):
        if widget is None:
            return False
        try:
            # Accessing a Qt property raises RuntimeError after C++ destruction.
            widget.objectName()
            return True
        except (AttributeError, RuntimeError, TypeError):
            # Test doubles and non-Qt records can still participate in native
            # suppression without exposing QObject.objectName().
            return not hasattr(widget, "objectName")

    def _forget_token(self, token):
        record = self._records.pop(int(token), None)
        if record is None:
            return
        if self._names.get(record.name) == record.token:
            self._names.pop(record.name, None)
        if self._primary_token == record.token:
            self._primary_token = None

    def _make_reference(self, widget, token):
        try:
            return weakref.ref(widget, lambda _ref, key=token: self._forget_token(key))
        except TypeError:
            # Simple test doubles may not support weak references. Their records
            # are short-lived and are explicitly cleared by reset/shutdown.
            return lambda value=widget: value

    def register(self, widget, *, name=None, role="window", unique=True):
        """Register ``widget`` and return the existing unique widget if any."""
        if widget is None:
            return None
        fallback = f"{role}_{id(widget)}"
        resolved_name = str(name or self._widget_name(widget, fallback))
        existing = self.get(resolved_name) if unique else None
        if existing is not None and existing is not widget:
            return existing

        token = id(widget)
        record = self._records.get(token)
        created_record = record is None or record.widget() is not widget
        if created_record:
            record = _WindowRecord(
                token=token,
                name=resolved_name,
                role=str(role),
                reference=self._make_reference(widget, token),
            )
            self._records[token] = record
        else:
            record.name = resolved_name
            record.role = str(role)
        self._names[resolved_name] = token
        if role == "primary":
            self._primary_token = token
        self._adopt_legacy_state(widget, record)
        if created_record:
            try:
                widget.destroyed.connect(
                    lambda *_args, key=token: self._forget_token(key)
                )
            except (AttributeError, RuntimeError, TypeError):
                pass
        return widget

    def unregister(self, widget):
        if widget is not None:
            self._forget_token(id(widget))

    def get(self, name):
        token = self._names.get(str(name))
        record = self._records.get(token) if token is not None else None
        widget = record.widget() if record is not None else None
        if not self._widget_alive(widget):
            if token is not None:
                self._forget_token(token)
            return None
        return widget

    def primary_window(self):
        record = self._records.get(self._primary_token)
        widget = record.widget() if record is not None else None
        if not self._widget_alive(widget):
            if record is not None:
                self._forget_token(record.token)
            return None
        return widget

    def get_or_create_primary(self, factory):
        existing = self.primary_window() or self.get(self.primary_name)
        if existing is not None:
            self.register(existing, name=self.primary_name, role="primary")
            return existing, False
        created = factory()
        registered = self.register(
            created, name=self.primary_name, role="primary", unique=True
        )
        if registered is not created:
            try:
                created.deleteLater()
            except (AttributeError, RuntimeError):
                pass
            return registered, False
        return created, True

    def is_primary_visible(self):
        window = self.primary_window()
        try:
            return bool(window is not None and window.isVisible())
        except RuntimeError:
            return False

    def ensure_application(self, QtWidgets, argv=None):
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication(list(argv or ["BakeToolsBlender"]))
        app.setQuitOnLastWindowClosed(False)
        self._application = app
        return app

    @property
    def application(self):
        return self._application

    @property
    def pump_active(self):
        return bool(self._pump_active)

    def start_pump(self, callback, first_interval=0.02):
        if callback is None:
            return False
        if self._pump_callback is not None and self._pump_callback is not callback:
            self.stop_pump()
        self._pump_callback = callback
        if not bpy.app.timers.is_registered(callback):
            bpy.app.timers.register(callback, first_interval=float(first_interval))
        self._pump_active = True
        return True

    def mark_pump_stopped(self, callback=None):
        if callback is None or callback is self._pump_callback:
            self._pump_active = False

    def stop_pump(self):
        callback = self._pump_callback
        if callback is not None and bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
        self._pump_active = False
        return callback is not None

    def _state_for(self, widget):
        if widget is None:
            return None
        token = id(widget)
        record = self._records.get(token)
        if record is None or record.widget() is not widget:
            self.register(widget, role="native", unique=False)
            record = self._records.get(token)
        return record

    @staticmethod
    def _adopt_legacy_state(widget, record):
        try:
            record.suppression_reasons.update(
                str(item) for item in getattr(widget, "_bt_suppression_reasons", set())
            )
            record.native_suppressed = bool(
                getattr(widget, "_bt_native_suppressed", record.native_suppressed)
            )
            saved = getattr(widget, "_bt_native_exstyle", record.native_exstyle)
            record.native_exstyle = int(saved) if saved is not None else None
        except (AttributeError, TypeError, ValueError):
            pass
        QtWindowManager._mirror_legacy_state(widget, record)

    @staticmethod
    def _mirror_legacy_state(widget, record):
        # Keep these attributes as a compatibility/diagnostics surface. The
        # record is authoritative; bridge code no longer mutates them directly.
        try:
            widget._bt_suppression_reasons = set(record.suppression_reasons)
            widget._bt_native_suppressed = bool(record.native_suppressed)
            if record.native_exstyle is not None:
                widget._bt_native_exstyle = int(record.native_exstyle)
        except (AttributeError, RuntimeError, TypeError):
            pass

    def suppression_reasons(self, widget):
        record = self._state_for(widget)
        return set(record.suppression_reasons) if record is not None else set()

    def is_suppressed(self, widget):
        record = self._state_for(widget)
        return bool(record is not None and record.native_suppressed)

    def native_exstyle(self, widget):
        record = self._state_for(widget)
        return record.native_exstyle if record is not None else None

    def adopt_native_suppression(self, widget, base_exstyle=None):
        record = self._state_for(widget)
        if record is None:
            return False
        if base_exstyle is not None:
            record.native_exstyle = int(base_exstyle)
        record.native_suppressed = True
        self._mirror_legacy_state(widget, record)
        return True

    def set_suppression_reason(self, widget, reason, enabled):
        """Update one composable reason and apply the resulting native state."""
        record = self._state_for(widget)
        if record is None:
            return False
        reason = str(reason)
        if enabled:
            record.suppression_reasons.add(reason)
        else:
            record.suppression_reasons.discard(reason)
        should_suppress = bool(record.suppression_reasons)
        if should_suppress != record.native_suppressed:
            if should_suppress:
                self._hide_native(widget, record)
            else:
                self._restore_native(widget, record)
        self._mirror_legacy_state(widget, record)
        return should_suppress

    def clear_suppression(self, widget):
        """Clear every reason and physically restore the native HWND."""
        record = self._state_for(widget)
        if record is None:
            return False
        record.suppression_reasons.clear()
        restored = self._restore_native(widget, record, force=True)
        self._mirror_legacy_state(widget, record)
        return restored

    def clear_suppression_reasons(self, widget):
        record = self._state_for(widget)
        if record is None:
            return False
        record.suppression_reasons.clear()
        self._mirror_legacy_state(widget, record)
        return True

    def mark_native_visible(self, widget, base_exstyle=None):
        record = self._state_for(widget)
        if record is None:
            return False
        if base_exstyle is not None:
            record.native_exstyle = int(base_exstyle)
        record.native_suppressed = False
        self._mirror_legacy_state(widget, record)
        return True

    def restore_native(self, widget, base_exstyle=None):
        """Force the HWND visible without changing unrelated reason records."""
        record = self._state_for(widget)
        if record is None:
            return False
        if base_exstyle is not None:
            record.native_exstyle = int(base_exstyle)
        restored = self._restore_native(widget, record, force=True)
        self._mirror_legacy_state(widget, record)
        return restored

    @staticmethod
    def _hide_native(widget, record):
        if sys.platform != "win32":
            record.native_suppressed = False
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = wintypes.HWND(int(widget.winId()))
            original = int(user32.GetWindowLongW(hwnd, -20))
            record.native_exstyle = original
            user32.SetWindowLongW(
                hwnd, -20, original | 0x00080000 | 0x00000020
            )  # WS_EX_LAYERED | WS_EX_TRANSPARENT
            user32.SetLayeredWindowAttributes(hwnd, 0, 0, 0x00000002)
            record.native_suppressed = True
            return True
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False

    @staticmethod
    def _restore_native(widget, record, *, force=False):
        if sys.platform != "win32":
            record.native_suppressed = False
            return True
        if not force and not record.native_suppressed:
            return True
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = wintypes.HWND(int(widget.winId()))
            current = int(user32.GetWindowLongW(hwnd, -20))
            base = record.native_exstyle
            if base is None:
                base = current & ~0x00080000 & ~0x00000020
            # Alpha first also repairs HWNDs hidden directly by the input hook
            # before Blender's timer could adopt the state.
            user32.SetLayeredWindowAttributes(hwnd, 0, 255, 0x00000002)
            user32.SetWindowLongW(hwnd, -20, int(base))
            record.native_exstyle = int(base)
            record.native_suppressed = False
            return True
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False

    def shutdown(self):
        self.stop_pump()
        self._application = None
        self._pump_callback = None
        self._pump_active = False
        self._records.clear()
        self._names.clear()
        self._primary_token = None


window_manager = QtWindowManager()
