"""Delete to Trash never overwrites something already in the Trash (Codex UI-05).

Same-seed image batches share basenames (`cand_42_mflux.png`). The delete route
checked only the plain name, then fell back to ONE timestamp-suffixed name it
never checked: two deletions in the same second overwrote the first trashed
image. HOME is a temp dir here — nothing reaches the real ~/.Trash.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P                                            # noqa: E402

routes_files = sys.modules["panel.routes_files"]


class _H:
    def __init__(self, target):
        self.target, self.status, self.payload = target, None, None

    def _read_form_body(self):
        return ("", {"path": [str(self.target)]})

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


class TrashNeverOverwrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.home, self.out = base / "home", base / "out"
        (self.home / ".Trash").mkdir(parents=True)
        self.out.mkdir()
        self._patches = [
            mock.patch("pathlib.Path.home", lambda *a: self.home),
            mock.patch.object(P, "OUTPUT", self.out),
            mock.patch.object(P, "UPLOADS", base / "up"),
            mock.patch.object(P.time, "strftime", lambda *a, **k: "20260924-120000"),
            mock.patch.object(P, "set_hidden", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def _batch(self, n: int) -> Path:
        d = self.out / f"batch{n}"
        d.mkdir()
        f = d / "cand_42_mflux.png"
        f.write_bytes(f"image {n}".encode())
        Path(str(f) + ".json").write_text(f'{{"batch": {n}}}')
        return f

    def test_three_same_named_deletes_in_one_second_all_survive(self):
        files = [self._batch(i) for i in range(3)]
        routes_files.post_output_delete(_H(files[0]), "/output/delete", {}, "")
        hs = [_H(f) for f in files[1:]]
        ts = [threading.Thread(target=routes_files.post_output_delete,
                               args=(h, "/output/delete", {}, "")) for h in hs]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        for h in hs:
            self.assertEqual(h.status, 200, h.payload)
        trash = self.home / ".Trash"
        pngs = sorted(p.read_bytes() for p in trash.glob("cand_42_mflux*.png"))
        self.assertEqual(pngs, [b"image 0", b"image 1", b"image 2"])
        sidecars = sorted(p.read_text() for p in trash.glob("*.json"))
        self.assertEqual(len(sidecars), 3, sidecars)


if __name__ == "__main__":
    unittest.main()
