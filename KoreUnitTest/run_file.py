"""Run one test file: classic unittest cases plus pytest-style module-level test_ functions."""
from __future__ import annotations

import importlib.util
import inspect
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    path = Path(argv[1]).resolve()
    os.environ["KORESTACK_ROOT"] = str(ROOT)
    sys.path[:0] = [str(ROOT), str(path.parent)]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    for name, member in vars(module).items():
        if name.startswith("test_") and inspect.isfunction(member) and member.__module__ == module.__name__:
            suite.addTest(unittest.FunctionTestCase(member, description=name))
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    if result.testsRun == 0:
        print("No tests were found in " + path.name, file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))