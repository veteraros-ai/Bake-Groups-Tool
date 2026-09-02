# -*- coding: utf-8 -*-
"""Privacy-minimal version telemetry for the Bake Groups Tool.

On startup the tool sends one pseudonymous ping the first time it runs on a new
version.  Maya and Blender share the same client UUID but keep independent
per-product version state, so installing one product never suppresses the other.
No names, hostnames, paths or scene data are sent.

Design / safety:
* The random id lives in ``~/.bake_groups_tool/client.json`` - OUTSIDE the
  versioned install tree, so it survives updates (and reinstalls) and stays the
  same per OS user, which is what makes the "unique users" count meaningful.
* A ping is sent ONLY when the running version differs from the last one we
  successfully reported (or on the very first run). Steady-state launches do NO
  network at all - they just read one local file.
* Everything runs in a daemon thread with a short timeout and is fully wrapped
  in try/except: telemetry can never block Maya, never hang when offline, and
  never raise into the tool.
* ``maya.cmds`` is touched ONLY on the main thread (Maya's API is not
  thread-safe); the Maya version and language are gathered before the worker
  thread starts and passed in as plain strings.
* Off switches: set ``TELEMETRY_ENABLED = False`` here (takes effect next
  release), OR simply turn OFF "Accepting responses" on the Google Form - every
  client then fails silently with no code change.
"""
from __future__ import print_function, division, absolute_import

import json
import os
import platform
import re
import sys
import threading
import uuid
from datetime import datetime

try:
    from urllib.request import Request, urlopen
    from urllib.parse import urlencode
    from html.parser import HTMLParser
except ImportError:  # Python 2 (older Maya)
    from urllib2 import Request, urlopen
    from urllib import urlencode
    from HTMLParser import HTMLParser

import bg_version

# --- Master switch (baked per release) ------------------------------------
TELEMETRY_ENABLED = True

# --- Google Form endpoint + field ids (from the pre-filled link) ----------
FORM_URL = ("https://docs.google.com/forms/d/e/"
            "1FAIpQLScd-eeYmLD-6S9fNBnOfWHYcwu9r3cIE5lfGHGpqGjG8yyCGA/formResponse")
FORM_VIEW_URL = FORM_URL.rsplit("/", 1)[0] + "/viewform"
FIELD_CLIENT_ID = "entry.262576988"
FIELD_PRODUCT = "entry.1531946019"
FIELD_VERSION = "entry.849043429"
FIELD_EVENT = "entry.228195919"
FIELD_HOST_APP = "entry.1291035306"
FIELD_HOST_VERSION = "entry.303840386"
FIELD_LANG = "entry.2055707732"
FIELD_PLATFORM = "entry.1726983759"
FIELD_SCHEMA_VERSION = "entry.463430998"

PRODUCT_KEY = "maya"
SCHEMA_VERSION = "2"

_POST_TIMEOUT = 5  # seconds
_FORM_CONTEXT_FIELDS = (
    "fvv", "partialResponse", "pageHistory", "fbzx",
    "submissionTimestamp",
)


# ==========================================================================
# Persistent per-user state (outside the versioned install tree)
# ==========================================================================
def _state_dir():
    return os.path.join(os.path.expanduser("~"), ".bake_groups_tool")


def _state_path():
    return os.path.join(_state_dir(), "client.json")


def _load_state():
    """Return the client state dict, creating a fresh anonymous id on first run.
    Always returns a dict with a valid ``client_id``; persists any new id."""
    path = _state_path()
    state = {}
    try:
        if os.path.exists(path):
            with open(path, "r") as handle:
                state = json.load(handle) or {}
    except Exception:
        state = {}

    changed = False
    if not state.get("client_id"):
        state["client_id"] = str(uuid.uuid4())
        state.setdefault("created_at", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        changed = True

    products = state.get("products")
    if not isinstance(products, dict):
        products = {}
        state["products"] = products
        changed = True
    if PRODUCT_KEY not in products and state.get("last_reported_version"):
        products[PRODUCT_KEY] = {
            "last_reported_version": state.get("last_reported_version"),
            "last_reported_at": state.get("last_reported_at"),
        }
        changed = True
    if changed:
        _save_state(state)
    return state


def _save_state(state):
    try:
        d = _state_dir()
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(_state_path(), "w") as handle:
            json.dump(state, handle, indent=2)
    except Exception:
        pass


# ==========================================================================
# Main-thread context gathering (Maya API is NOT thread-safe)
# ==========================================================================
def _safe_maya_version():
    try:
        import maya.cmds as cmds
        raw = str(cmds.about(version=True))
        m = re.search(r"\d{4}", raw)
        return m.group(0) if m else raw
    except Exception:
        return ""


def _safe_lang():
    try:
        import bg_localization as bg_l10n
        return str(bg_l10n.current_language() or "")
    except Exception:
        return ""


def _safe_platform():
    system = str(platform.system() or sys.platform or "unknown").lower()
    if system.startswith("win"):
        system = "windows"
    elif system.startswith("darwin"):
        system = "macos"
    machine = str(platform.machine() or "unknown").lower()
    if machine in ("amd64", "x86_64"):
        machine = "x64"
    elif machine in ("aarch64", "arm64"):
        machine = "arm64"
    return "{}-{}".format(system, machine)


# ==========================================================================
# Network (worker thread only: file I/O + urllib, both thread-safe)
# ==========================================================================
class _HiddenInputParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        if str(tag).lower() != "input":
            return
        values = dict(attrs)
        if str(values.get("type") or "").lower() != "hidden":
            return
        name = values.get("name")
        if name:
            self.fields[str(name)] = str(values.get("value") or "")


def _user_agent():
    return "Bake-Groups-Tool/{}".format(bg_version.__version__)


def _read_response(response):
    try:
        try:
            code = response.getcode()
        except Exception:
            code = 200
        try:
            final_url = response.geturl()
        except Exception:
            final_url = ""
        body = response.read()
        if not isinstance(body, str):
            body = body.decode("utf-8", "replace")
        return code, str(final_url or ""), body
    finally:
        try:
            response.close()
        except Exception:
            pass


def _fetch_form_context():
    """Fetch Google's per-request hidden fields required by the live form."""
    request = Request(FORM_VIEW_URL, headers={"User-Agent": _user_agent()})
    code, _final_url, body = _read_response(
        urlopen(request, timeout=_POST_TIMEOUT))
    if code != 200:
        raise RuntimeError("Google Form context request failed: HTTP {}".format(code))

    parser = _HiddenInputParser()
    parser.feed(body)
    context = {
        name: parser.fields[name]
        for name in _FORM_CONTEXT_FIELDS
        if name in parser.fields
    }
    missing = [name for name in _FORM_CONTEXT_FIELDS if name not in context]
    if missing:
        raise RuntimeError(
            "Google Form context is incomplete: {}".format(", ".join(missing)))
    return context


def _is_submission_confirmation(final_url, body):
    """Reject HTTP 200 responses that merely redisplay the input form."""
    if "/formResponse" not in str(final_url or ""):
        return False
    lowered = str(body or "").lower()
    if not lowered.strip():
        return False
    if 'id="mg61hd"' in lowered or 'name="entry.' in lowered:
        return False
    return True


def _post(client_id, version, event, maya_ver, lang, platform_name):
    """POST one row and return True only for a real confirmation page."""
    payload = _fetch_form_context()
    payload.update({
        FIELD_CLIENT_ID: client_id,
        FIELD_PRODUCT: PRODUCT_KEY,
        FIELD_VERSION: version,
        FIELD_EVENT: event,
        FIELD_HOST_APP: "Maya",
        FIELD_HOST_VERSION: maya_ver,
        FIELD_LANG: lang,
        FIELD_PLATFORM: platform_name,
        FIELD_SCHEMA_VERSION: SCHEMA_VERSION,
    })
    data = urlencode(payload).encode("utf-8")
    request = Request(
        FORM_URL,
        data=data,
        headers={
            "User-Agent": _user_agent(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    code, final_url, body = _read_response(
        urlopen(request, timeout=_POST_TIMEOUT))
    return code == 200 and _is_submission_confirmation(final_url, body)


def _report(maya_ver, lang):
    if not TELEMETRY_ENABLED:
        return
    current_version = str(bg_version.__version__)
    state = _load_state()
    products = state.setdefault("products", {})
    product_state = products.setdefault(PRODUCT_KEY, {})
    previous_version = product_state.get("last_reported_version")
    if previous_version == current_version:
        return  # already reported this version -> no network

    event = "install" if not previous_version else "update"
    ok = _post(
        state["client_id"], current_version, event, maya_ver, lang,
        _safe_platform(),
    )
    if ok:
        product_state["last_reported_version"] = current_version
        product_state["last_reported_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        # Keep legacy keys until all installed Maya builds understand schema 2.
        state["last_reported_version"] = current_version
        state["last_reported_at"] = product_state["last_reported_at"]
        _save_state(state)


def _report_safe(maya_ver, lang):
    try:
        _report(maya_ver, lang)
    except Exception:
        pass


# ==========================================================================
# Public entry point (call once on startup, from the main thread)
# ==========================================================================
def report_async():
    """Fire-and-forget anonymous version ping. Safe to call every startup:
    it only touches the network when the version changed. Never raises."""
    try:
        if not TELEMETRY_ENABLED:
            return
        maya_ver = _safe_maya_version()   # main thread (maya.cmds)
        lang = _safe_lang()               # main thread
        t = threading.Thread(
            target=_report_safe, args=(maya_ver, lang), name="BGTelemetry")
        t.daemon = True
        t.start()
    except Exception:
        pass
