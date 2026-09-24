"""Recent uploads lists the user's images, not the panel's own crops (Codex H3-06).

H3 writes a fitted first frame per job into the uploads folder as a dot-file.
list_uploads() filtered only by type and extension, so after 24 H3 renders the
Recent-uploads strip held nothing but crops and the original was gone from it.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P                                            # noqa: E402


class RecentUploads(unittest.TestCase):
    def test_internal_h3_crops_never_displace_the_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            up = Path(tmp)
            orig = up / "1727000000000_portrait.png"
            orig.write_bytes(b"x")
            old = time.time() - 3600
            os.utime(orig, (old, old))
            for i in range(40):
                (up / f".h3_job{i}_firstframe_1280x704.png").write_bytes(b"x")
            with mock.patch.object(P, "UPLOADS", up):
                got = P.list_uploads(limit=24)
        self.assertEqual([u["path"] for u in got], [str(orig)])
        self.assertEqual(got[0]["name"], "portrait.png")


if __name__ == "__main__":
    unittest.main()
