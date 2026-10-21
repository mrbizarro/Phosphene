"""The modules split out of mlx_ltx_panel.py in 4.19.6 (#110 civitai_client,
#111 usage_stats, #112 train_support, all by @blackest; version_check after them) stay wired.

Each module spells panel globals as `P.<name>` and is handed the running panel
module at import time. Two ways that could silently break later:
  * the hand-over is lost (P stays None) - every call then dies with
    "'NoneType' object has no attribute ..." the first time a user reaches it;
  * a re-export drifts from the module's own object - a test patching
    `P.<name>` then no longer reaches the code that runs.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="split-"))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")

import mlx_ltx_panel as P  # noqa: E402

SPLIT = ("civitai_client", "usage_stats", "train_support", "version_check")


class TheSplitModulesAreWired(unittest.TestCase):
    def test_each_module_holds_the_running_panel(self):
        for name in SPLIT:
            mod = sys.modules[name]
            self.assertIsInstance(mod.P, types.ModuleType, name)
            self.assertIs(mod.P, sys.modules[P.__name__], name)

    def test_every_public_function_is_reexported_as_the_same_object(self):
        for name in SPLIT:
            mod = sys.modules[name]
            for attr, val in vars(mod).items():
                if attr.startswith("__") or attr == "P" or isinstance(val, types.ModuleType):
                    continue
                if getattr(val, "__module__", None) != name:
                    continue                     # imports, not definitions
                self.assertTrue(hasattr(P, attr), f"{name}.{attr} is not re-exported")
                self.assertIs(getattr(P, attr), val, f"{name}.{attr} drifted")

    def test_no_split_module_imports_the_panel_by_name(self):
        # Importing it by name would execute the whole panel a second time
        # (it usually runs as __main__) - the hand-over is the only route.
        import ast
        for name in SPLIT:
            tree = ast.parse((ROOT / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""]
                else:
                    continue
                self.assertNotIn("mlx_ltx_panel", mods, f"{name}:{node.lineno}")


if __name__ == "__main__":
    unittest.main()
