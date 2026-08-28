"""Opt-in, privacy-minimal installation telemetry shared with the Maya tool."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import sys
import threading
import urllib.parse
import urllib.request
from uuid import uuid4


FORM_URL = (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLScd-eeYmLD-6S9fNBnOfWHYcwu9r3cIE5lfGHGpqGjG8yyCGA/formResponse"
)
FIELD_CLIENT_ID = "entry.262576988"
FIELD_PRODUCT = "entry.1531946019"
FIELD_PLUGIN_VERSION = "entry.849043429"
FIELD_EVENT = "entry.228195919"
FIELD_HOST_APP = "entry.1291035306"
FIELD_HOST_VERSION = "entry.303840386"
FIELD_LANGUAGE = "entry.2055707732"
FIELD_PLATFORM = "entry.1726983759"
FIELD_SCHEMA_VERSION = "entry.463430998"

PRODUCT_KEY = "blender"
PLUGIN_VERSION = "1.0.0"
SCHEMA_VERSION = "2"
_POST_TIMEOUT = 5.0
_STATE_LOCK = threading.RLock()


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_path():
    """Use Maya's existing state file so one artist keeps one client UUID."""
    return Path.home() / ".bake_groups_tool" / "client.json"


def _save_state_unlocked(state):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _load_state_unlocked():
    path = state_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError, TypeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    changed = False
    if not state.get("client_id"):
        state["client_id"] = str(uuid4())
        state.setdefault("created_at", _utc_now())
        changed = True
    products = state.get("products")
    if not isinstance(products, dict):
        products = {}
        state["products"] = products
        changed = True
    # Migrate the original Maya-only state without deleting keys old builds use.
    if "maya" not in products and state.get("last_reported_version"):
        products["maya"] = {
            "last_reported_version": state.get("last_reported_version"),
            "last_reported_at": state.get("last_reported_at"),
        }
        changed = True
    consents = state.get("telemetry_consent")
    if not isinstance(consents, dict):
        state["telemetry_consent"] = {}
        changed = True
    if changed:
        _save_state_unlocked(state)
    return state


def load_state():
    with _STATE_LOCK:
        return _load_state_unlocked()


def consent_value():
    """Return True, False, or None when the Blender artist has not decided."""
    state = load_state()
    value = state.get("telemetry_consent", {}).get(PRODUCT_KEY)
    return value if isinstance(value, bool) else None


def set_consent(enabled):
    with _STATE_LOCK:
        state = _load_state_unlocked()
        state.setdefault("telemetry_consent", {})[PRODUCT_KEY] = bool(enabled)
        state["telemetry_consent_updated_at"] = _utc_now()
        _save_state_unlocked(state)


def _platform_name():
    system = (platform.system() or sys.platform or "unknown").lower()
    if system.startswith("win"):
        system = "windows"
    elif system.startswith("darwin"):
        system = "macos"
    machine = (platform.machine() or "unknown").lower()
    if machine in {"amd64", "x86_64"}:
        machine = "x64"
    elif machine in {"aarch64", "arm64"}:
        machine = "arm64"
    return "{}-{}".format(system, machine)


def _online_access_allowed():
    try:
        import bpy
        return bool(bpy.app.online_access)
    except (ImportError, AttributeError):
        return True


def _post(client_id, event, host_version, language):
    payload = urllib.parse.urlencode({
        FIELD_CLIENT_ID: client_id,
        FIELD_PRODUCT: PRODUCT_KEY,
        FIELD_PLUGIN_VERSION: PLUGIN_VERSION,
        FIELD_EVENT: event,
        FIELD_HOST_APP: "Blender",
        FIELD_HOST_VERSION: host_version,
        FIELD_LANGUAGE: language,
        FIELD_PLATFORM: _platform_name(),
        FIELD_SCHEMA_VERSION: SCHEMA_VERSION,
    }).encode("utf-8")
    request = urllib.request.Request(
        FORM_URL,
        data=payload,
        headers={
            "User-Agent": "BakeTools-Blender/{}".format(PLUGIN_VERSION),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(request, timeout=_POST_TIMEOUT) as response:
        return int(getattr(response, "status", 200)) == 200


def _report_safe(host_version, language):
    try:
        with _STATE_LOCK:
            state = _load_state_unlocked()
            if state.get("telemetry_consent", {}).get(PRODUCT_KEY) is not True:
                return
            products = state.setdefault("products", {})
            product_state = products.setdefault(PRODUCT_KEY, {})
            previous = product_state.get("last_reported_version")
            if previous == PLUGIN_VERSION:
                return
            client_id = state["client_id"]
        event = "install" if not previous else "update"
        if not _post(client_id, event, host_version, language):
            return
        with _STATE_LOCK:
            state = _load_state_unlocked()
            product_state = state.setdefault("products", {}).setdefault(PRODUCT_KEY, {})
            product_state["last_reported_version"] = PLUGIN_VERSION
            product_state["last_reported_at"] = _utc_now()
            _save_state_unlocked(state)
    except Exception:
        # Telemetry is never allowed to affect Blender startup or tool usage.
        return


def report_async(language=""):
    """Send once per version, only after explicit Blender-product consent."""
    if consent_value() is not True or not _online_access_allowed():
        return False
    try:
        import bpy
        host_version = ".".join(str(value) for value in bpy.app.version[:3])
    except (ImportError, AttributeError):
        host_version = ""
    worker = threading.Thread(
        target=_report_safe,
        args=(host_version, str(language or "")),
        name="BakeToolsTelemetry",
        daemon=True,
    )
    worker.start()
    return True
