"""Fast publication checks that do not require Blender or Qt."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blender_telemetry():
    module = load_module(
        "publication_blender_telemetry",
        ROOT / "addon" / "bake_tools_blender" / "telemetry.py",
    )
    with tempfile.TemporaryDirectory(prefix="BakeToolsTelemetryTest-") as temporary:
        state_path = Path(temporary) / "client.json"
        state_path.write_text(json.dumps({
            "client_id": "test-client",
            "last_reported_version": "1.4.3",
            "last_reported_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        module.state_path = lambda: state_path
        assert module.consent_value() is None
        module.set_consent(True)
        captured = {}

        def fake_post(client_id, event, host_version, language):
            captured.update({
                "client_id": client_id,
                "event": event,
                "host_version": host_version,
                "language": language,
            })
            return True

        module._post = fake_post
        module._report_safe("5.1.0", "RU")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["products"]["maya"]["last_reported_version"] == "1.4.3"
        assert state["products"]["blender"]["last_reported_version"] == "1.0.0"
        assert captured["event"] == "install"


def test_update_manifest():
    module = load_module(
        "publication_update_service",
        ROOT / "addon" / "bake_tools_blender" / "update_service.py",
    )
    current = module._manifest_from_text(json.dumps({"latest_version": "1.0.0"}))
    assert current["is_update_available"] is False
    try:
        module._manifest_from_text(json.dumps({"latest_version": "1.0.1"}))
    except ValueError:
        pass
    else:
        raise AssertionError("A newer manifest without SHA-256 was accepted")
    future = module._manifest_from_text(json.dumps({
        "latest_version": "1.0.1",
        "package_url": "https://example.invalid/package.zip",
        "package_sha256": "a" * 64,
    }))
    assert future["is_update_available"] is True


def test_maya_telemetry():
    sys.path.insert(0, str(REPO / "Bake_Groups"))
    try:
        module = load_module(
            "publication_maya_telemetry",
            REPO / "Bake_Groups" / "bg_telemetry.py",
        )
    finally:
        sys.path.pop(0)
    with tempfile.TemporaryDirectory(prefix="BakeGroupsTelemetryTest-") as temporary:
        module._state_dir = lambda: temporary
        captured = {}

        def fake_post(client_id, version, event, host_version, language, platform_name):
            captured.update({"version": version, "event": event})
            return True

        module._post = fake_post
        module._report("2026", "RU")
        state = json.loads((Path(temporary) / "client.json").read_text(encoding="utf-8"))
        assert state["products"]["maya"]["last_reported_version"] == module.bg_version.__version__
        assert state["last_reported_version"] == module.bg_version.__version__
        assert captured["event"] == "install"


if __name__ == "__main__":
    test_blender_telemetry()
    test_update_manifest()
    test_maya_telemetry()
    print("publication-smoke-ok")
