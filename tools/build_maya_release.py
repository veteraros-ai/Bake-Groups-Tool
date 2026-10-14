"""Build a clean, deterministic Maya release ZIP from Git's runtime inventory."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile


def build(version, output):
    root = Path(__file__).resolve().parents[1]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a numeric x.y.z version")
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    module = ast.parse((root / "Bake_Groups/bg_version.py").read_text(encoding="utf-8-sig"))
    source_version = next(ast.literal_eval(node.value) for node in module.body
                          if isinstance(node, ast.Assign) and
                          any(isinstance(target, ast.Name) and target.id == "__version__"
                              for target in node.targets))
    if source_version != version:
        raise ValueError("Source version does not match release version")
    inventory = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "Bake_Groups"], cwd=str(root)).decode("utf-8")
    names = sorted(name for name in inventory.split("\0") if name)
    required = {"Bake_Groups/bg_cage_undo.py", "Bake_Groups/bg_guide_translations.py",
                "Bake_Groups/bin/bg_obj_subdivider.exe",
                "Bake_Groups/toolbag_plugin/Bake Groups Bridge/__main__.py"}
    required.update("Bake_Groups/bin/{}/bg_math_core.pyd".format(year)
                    for year in range(2022, 2028))
    if not required.issubset(names):
        raise ValueError("Missing runtime files: " + str(sorted(required.difference(names))))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(output), "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for name in names:
            if any(part in ("__pycache__", "out", "backups", "versions")
                   for part in Path(name).parts) or name.endswith((".pyc", ".log", ".bak")):
                raise ValueError("Unexpected runtime file: " + name)
            payload = (root / name).read_bytes()
            if name.endswith(".py"):
                ast.parse(payload.decode("utf-8-sig"), filename=name)
            if name.endswith(".json"):
                value = json.loads(payload.decode("utf-8-sig"))
                if name == "Bake_Groups/update_manifest.json":
                    if value["latest_version"] != version:
                        raise ValueError("Manifest version mismatch")
                    expected = "/{}/Bake_Groups_{}.zip".format(version, version)
                    if not value["package_url"].endswith(expected):
                        raise ValueError("Manifest download URL mismatch")
                    # The remote manifest records this ZIP's hash afterwards;
                    # embedding the ZIP's own hash would be self-referential.
                    value["package_sha256"] = ""
                    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload, compresslevel=9)
    with zipfile.ZipFile(str(output)) as archive:
        if archive.testzip() is not None or archive.namelist() != names:
            raise ValueError("Archive verification failed")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(json.dumps({"archive": str(output), "files": len(names),
                      "bytes": output.stat().st_size, "sha256": digest}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    build(args.version, args.output)
