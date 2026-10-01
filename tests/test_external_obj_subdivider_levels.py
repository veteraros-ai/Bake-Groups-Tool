"""CLI regression test for mixed per-mesh subdivision levels.

Run with:
    py -3 test_external_obj_subdivider_levels.py <helper.exe> <sample.fbx>
"""
from __future__ import print_function

import os
import subprocess
import sys
import tempfile


def _run(command):
    result = subprocess.run(command, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    if result.returncode:
        raise RuntimeError("Command failed: {}\n{}\n{}".format(
            command, result.stdout, result.stderr))
    return result.stdout


def _face_counts(obj_path):
    counts = {}
    current = None
    with open(obj_path, "r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("o "):
                current = line[2:].strip()
                counts.setdefault(current, 0)
            elif line.startswith("f ") and current is not None:
                counts[current] += 1
    return counts


def main(helper, source_fbx):
    with tempfile.TemporaryDirectory(prefix="bg_obj_levels_test_") as temp_dir:
        base_obj = os.path.join(temp_dir, "baseline.obj")
        mixed_obj = os.path.join(temp_dir, "mixed.obj")
        level_map = os.path.join(temp_dir, "levels.tsv")

        _run([helper, "--input", source_fbx, "--output", base_obj, "--level", "0"])
        baseline = _face_counts(base_obj)
        names = list(baseline)
        if len(names) < 2:
            raise AssertionError("The FBX fixture must contain at least two mesh objects.")

        with open(level_map, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("1\t{}\n".format(names[0]))
            stream.write("0\t{}\n".format(names[1]))

        _run([helper, "--input", source_fbx, "--output", mixed_obj,
              "--level", "0", "--levels-file", level_map])
        mixed = _face_counts(mixed_obj)
        if names[0] not in mixed or mixed[names[0]] <= baseline[names[0]]:
            raise AssertionError("The mapped level-1 mesh was not subdivided.")
        if names[1] not in mixed or mixed[names[1]] != baseline[names[1]]:
            raise AssertionError("The mapped level-0 mesh was unexpectedly subdivided.")
        print("PASS: per-mesh levels produce one OBJ; level 1 subdivides and level 0 stays unchanged.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: test_external_obj_subdivider_levels.py <helper.exe> <sample.fbx>")
    main(os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]))
