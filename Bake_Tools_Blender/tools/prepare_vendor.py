"""Download and unpack the exact Windows Qt runtime used by release builds."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile


PACKAGES = ("PySide6_Essentials==6.11.1", "shiboken6==6.11.1")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="BakeToolsWheels-") as temporary_name:
        wheels = Path(temporary_name)
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "--only-binary=:all:",
             "--dest", str(wheels), *PACKAGES],
            check=True,
        )
        archives = sorted(wheels.glob("*.whl"))
        if len(archives) < 2:
            raise RuntimeError("PySide6 Essentials/shiboken6 wheels were not downloaded")
        for archive in archives:
            with ZipFile(archive, "r") as wheel:
                wheel.extractall(output)
    required = (
        output / "PySide6" / "QtCore.pyd",
        output / "PySide6" / "Qt6Core.dll",
        output / "shiboken6" / "Shiboken.pyd",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("Incomplete Qt runtime: {}".format(", ".join(missing)))


if __name__ == "__main__":
    main()
