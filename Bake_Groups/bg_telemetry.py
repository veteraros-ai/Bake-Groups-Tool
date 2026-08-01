# -*- coding: utf-8 -*-
"""Anonymous update telemetry for the Bake Groups Tool.

On startup the tool sends ONE tiny, fully anonymous ping the first time it runs
on a new version: a random per-user id (uuid4, not tied to any identity), the
tool version, whether this is a fresh install or an update, the Maya version and
the UI language. Nothing else - no names, no hostnames, no paths, no scene data.

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
import re
import threading
import uuid
from datetime import datetime

try:
    from urllib.request import Request, urlopen
    from urllib.parse import urlencode
except ImportError:  # Python 2 (older Maya)
    from urllib2 import Request, urlopen
    from urllib import urlencode

import bg_version

# --- Master switch (baked per release) ------------------------------------
TELEMETRY_ENABLED = True

# --- Google Form endpoint + field ids (from the pre-filled link) ----------
FORM_URL = ("https://docs.google.com/forms/d/e/"
            "1FAIpQLScd-eeYmLD-6S9fNBnOfWHYcwu9r3cIE5lfGHGpqGjG8yyCGA/formResponse")
FIELD_CLIENT_ID = "entry.262576988"
FIELD_VERSION = "entry.849043429"
FIELD_EVENT = "entry.228195919"
FIELD_MAYA = "entry.303840386"
FIELD_LANG = "entry.2055707732"

_POST_TIMEOUT = 5  # seconds


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

    if not state.get("client_id"):
        state["client_id"] = str(uuid.uuid4())
        state.setdefault("created_at", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        state.setdefault("last_reported_version", None)
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


# ==========================================================================
# Network (worker thread only: file I/O + urllib, both thread-safe)
# ==========================================================================
def _post(client_id, version, event, maya_ver, lang):
    """POST one anonymous row to the Google Form. Return True on HTTP 200."""
    data = urlencode({
        FIELD_CLIENT_ID: client_id,
        FIELD_VERSION: version,
        FIELD_EVENT: event,
        FIELD_MAYA: maya_ver,
        FIELD_LANG: lang,
    }).encode("utf-8")
    request = Request(
        FORM_URL,
        data=data,
        headers={
            "User-Agent": "Bake-Groups-Tool/{}".format(bg_version.__version__),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    response = urlopen(request, timeout=_POST_TIMEOUT)
    try:
        code = response.getcode()
    except Exception:
        code = 200
    return code == 200


def _report(maya_ver, lang):
    if not TELEMETRY_ENABLED:
        return
    current_version = str(bg_version.__version__)
    state = _load_state()
    if state.get("last_reported_version") == current_version:
        return  # already reported this version -> no network

    event = "install" if not state.get("last_reported_version") else "update"
    ok = _post(state["client_id"], current_version, event, maya_ver, lang)
    if ok:
        state["last_reported_version"] = current_version
        state["last_reported_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
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
