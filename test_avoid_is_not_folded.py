"""Avoid terms never ride inside the positive prompt (#81).

The distilled Q4 path has no guidance branch, so the helper used to append
"Avoid: <terms>" to the prompt to give the box an effect. A text encoder has
no negation: "Avoid: rain poncho" is a prompt about a rain poncho, and the
render leaned into the avoided thing. The function now returns the prompt
untouched; the CFG paths keep the real negative conditioning.

The helper is not imported (it talks to MLX at import); the function is lifted
out of the source by AST and run on its own, the way the other helper tests do.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _lift(name: str):
    src = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            mod = ast.Module(body=[node], type_ignores=[])
            ns = {"_clean_text": lambda v: str(v or "").strip()}
            exec(compile(mod, "<helper>", "exec"), ns)
            return ns[name]
    raise AssertionError(f"{name} not found in mlx_warm_helper.py")


class AvoidStaysOutOfThePrompt(unittest.TestCase):
    def test_terms_are_not_appended(self):
        fn = _lift("_prompt_with_soft_negative")
        prompt = "A man walks through the rain in a grey coat."
        self.assertEqual(fn(prompt, "rain poncho, umbrella"), prompt)
        self.assertEqual(fn(prompt, ""), prompt)
        self.assertNotIn("Avoid", fn(prompt, "rain poncho"))

    def test_the_log_line_says_ignored_not_active(self):
        src = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")
        self.assertNotIn("folds them into the positive prompt", src)
        self.assertIn("Avoid terms ignored on this quality", src)


if __name__ == "__main__":
    unittest.main()
