# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.join(ROOT, "Bake_Groups")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from bg_worker_hp import HPGroupingWorker


def _worker():
    return HPGroupingWorker({}, {}, {}, {}, {}, 15.0, 12)


def run():
    failed = []
    worker = _worker()
    worker.failed.connect(lambda message, details: failed.append((message, details)))

    def fail():
        raise RuntimeError("expected worker failure")

    worker._run_analysis = fail
    worker.run()
    assert failed and failed[0][0] == "expected worker failure"
    assert "RuntimeError" in failed[0][1]

    cancelled = []
    worker = _worker()
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker._run_analysis = lambda: worker.stop()
    worker.run()
    assert cancelled == [True]

    worker = _worker()

    def wait_for_cancel():
        while not worker.is_cancelled:
            time.sleep(0.01)

    worker._run_analysis = wait_for_cancel
    worker.start()
    time.sleep(0.05)
    started = time.perf_counter()
    worker.stop()
    assert time.perf_counter() - started < 0.2
    assert worker.wait(2000)
    print("HP worker lifecycle tests passed")


if __name__ == "__main__":
    run()
