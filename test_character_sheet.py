#!/usr/bin/env python3
"""Character-sheet v1 gate — generation core, routes, record surfacing.

Non-GPU by design: the image engine's `generate` is replaced with a recorder
that writes tiny PNGs, so what IS exercised is everything the panel owns —
per-view prompt construction (the wardrobe pin), sequential rendering, the
REAL compositor, the atomic sheet.png swap, the bundle.json preview
null-guard, the containment checks on both routes, and the sheet_image_*
record fields beside the sample_image_* pair.

Route tests follow the suite's convention (see test_character_roundtrip):
`Handler.__new__` with a stub transport, then the REAL do_GET / do_POST — a
route only a socket can reach is a route that regresses quietly.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

from PIL import Image  # noqa: E402


@contextmanager
def sheet_env(*, with_bundle=True, preview=None, with_ref=True,
              engine_error_on_call=None):
    """One fully-patched character world in a temp dir.

    Yields a namespace with the temp paths, the synthetic character record,
    and `calls` — every engine invocation the core made, in order.
    `engine_error_on_call=N` makes the fake engine raise on its Nth call so
    the mid-sheet failure path can be asserted.
    """
    calls: list[dict] = []
    with tempfile.TemporaryDirectory() as td_raw:
        td = Path(td_raw)
        chars = td / "characters"
        lab = td / "lab"
        char_dir = chars / "bizarrotrn"
        char_dir.mkdir(parents=True)
        lab.mkdir()
        ref = char_dir / "avatar.png"
        if with_ref:
            Image.new("RGB", (96, 96), (10, 200, 30)).save(ref)
        if with_bundle:
            (char_dir / "bundle.json").write_text(json.dumps({
                "schema": "phosphene/character_bundle@1",
                "id": "bizarrotrn", "name": "Bizarro",
                "preview": preview,
            }), encoding="utf-8")
        record = {"id": "bizarrotrn", "trigger": "bizarrotrn",
                  "name": "Bizarro",
                  "sample_image_path": str(ref) if with_ref else None}

        def fake_generate(*, prompt, n, aspect, output_dir, base_seed=None,
                          refs=None, config=None, on_log=None):
            calls.append({"prompt": prompt, "n": n, "aspect": aspect,
                          "output_dir": str(output_dir),
                          "base_seed": base_seed, "refs": list(refs or []),
                          "kind": getattr(config, "kind", None)})
            if (engine_error_on_call is not None
                    and len(calls) >= engine_error_on_call):
                raise RuntimeError("engine exploded (synthetic)")
            # Distinguishable tiny PNGs so the composited sheet is real.
            shade = (40 * len(calls)) % 256
            png = Path(output_dir) / "cand_00_fake.png"
            Image.new("RGB", (64, 48), (shade, 80, 120)).save(png)
            return [{"png_path": str(png),
                     "seed": base_seed if base_seed is not None else 777,
                     "engine": "fake", "width": 64, "height": 48}]

        saved = (P.list_characters, P._CHARACTERS_CACHE_PATH,
                 P.LORA_LAB_ROOT, P._preflight_image_job, P.push,
                 P.agent_image_engine.generate)
        P.list_characters = lambda: [record]
        P._CHARACTERS_CACHE_PATH = chars
        P.LORA_LAB_ROOT = lab
        P._preflight_image_job = lambda *a, **k: None
        P.push = lambda line: None
        P.agent_image_engine.generate = fake_generate
        try:
            yield SimpleNamespace(td=td, chars=chars, char_dir=char_dir,
                                  ref=ref, record=record, calls=calls)
        finally:
            (P.list_characters, P._CHARACTERS_CACHE_PATH, P.LORA_LAB_ROOT,
             P._preflight_image_job, P.push,
             P.agent_image_engine.generate) = saved


WARDROBE_PIN = "wearing exactly the same clothes as in the reference image"


class TestGenerationCore(unittest.TestCase):
    def test_default_three_views_render_sequentially_with_the_wardrobe_pin(self):
        with sheet_env() as env:
            out = P.generate_character_sheet("bizarrotrn",
                                             engine_override="hidream_inline")
            self.assertEqual(len(env.calls), 3)
            # Catalogue order IS render order.
            for call, key in zip(env.calls, P.CHARACTER_SHEET_VIEWS):
                self.assertIn(P.CHARACTER_SHEET_VIEWS[key], call["prompt"])
                # Identity framing + wardrobe pin on EVERY view — wardrobe
                # drift is the residual defect this exists to fight.
                self.assertIn("Keep this person exactly as they are",
                              call["prompt"])
                self.assertIn(WARDROBE_PIN, call["prompt"])
                self.assertEqual(call["n"], 1)
                self.assertEqual(call["aspect"], "1:1")
                # The core hands the engine the RESOLVED ref (the same
                # path its containment check vetted) — on macOS that means
                # /private/var, not the tempdir's /var alias. Views after
                # the first ALSO chain the first rendered view as a second
                # anchor ref (side-angle drift fix — see the ref-chaining
                # comment in generate_character_sheet).
                self.assertEqual(call["refs"][0], str(env.ref.resolve()))
            first_png = out["views"][0]["png_path"]
            self.assertEqual(env.calls[0]["refs"],
                             [str(env.ref.resolve())])
            for call in env.calls[1:]:
                self.assertEqual(call["refs"],
                                 [str(env.ref.resolve()), first_png])
                self.assertEqual(call["kind"], "hidream")
            self.assertTrue(out["ok"])
            self.assertEqual([v["key"] for v in out["views"]],
                             list(P.CHARACTER_SHEET_VIEWS))

    def test_wardrobe_string_is_appended_when_provided(self):
        with sheet_env() as env:
            P.generate_character_sheet("bizarrotrn",
                                       wardrobe="a red flight jacket")
            for call in env.calls:
                self.assertIn(WARDROBE_PIN, call["prompt"])
                self.assertIn("a red flight jacket", call["prompt"])

    def test_no_wardrobe_clause_when_empty(self):
        with sheet_env() as env:
            P.generate_character_sheet("bizarrotrn")
            for call in env.calls:
                self.assertIn(WARDROBE_PIN, call["prompt"])
                self.assertNotIn("They are wearing", call["prompt"])

    def test_seed_derives_per_view(self):
        with sheet_env() as env:
            P.generate_character_sheet("bizarrotrn", seed=100)
            self.assertEqual([c["base_seed"] for c in env.calls],
                             [100, 101, 102])
        with sheet_env() as env:
            P.generate_character_sheet("bizarrotrn", seed=-1)
            self.assertEqual([c["base_seed"] for c in env.calls],
                             [None, None, None])

    def test_views_are_deduped_order_preserving(self):
        with sheet_env() as env:
            P.generate_character_sheet(
                "bizarrotrn", views=["front", "front", "profile_left"])
            self.assertEqual(len(env.calls), 2)
            self.assertIn(P.CHARACTER_SHEET_VIEWS["front"],
                          env.calls[0]["prompt"])
            self.assertIn(P.CHARACTER_SHEET_VIEWS["profile_left"],
                          env.calls[1]["prompt"])

    def test_unknown_view_refused(self):
        with sheet_env() as env:
            with self.assertRaises(ValueError):
                P.generate_character_sheet("bizarrotrn", views=["bogus"])
            self.assertEqual(env.calls, [])

    def test_empty_views_refused(self):
        with sheet_env():
            with self.assertRaises(ValueError):
                P.generate_character_sheet("bizarrotrn", views=[])

    def test_non_ref_engine_refused(self):
        # ideogram4 is text-only and "auto" could resolve to anything — a
        # sheet from either would render a stranger. Both must 400.
        with sheet_env() as env:
            for bad in ("ideogram4_inline", "auto", "nonsense"):
                with self.assertRaises(ValueError):
                    P.generate_character_sheet("bizarrotrn",
                                               engine_override=bad)
            self.assertEqual(env.calls, [])

    def test_unknown_character_is_lookup_error(self):
        with sheet_env():
            P.list_characters = lambda: []
            with self.assertRaises(LookupError):
                P.generate_character_sheet("bizarrotrn")

    def test_invalid_character_id_is_value_error(self):
        with sheet_env():
            with self.assertRaises(ValueError):
                P.generate_character_sheet("../etc")

    def test_missing_reference_is_file_not_found(self):
        with sheet_env(with_ref=False) as env:
            with self.assertRaises(FileNotFoundError):
                P.generate_character_sheet("bizarrotrn")
            self.assertEqual(env.calls, [])

    def test_reference_outside_known_roots_refused(self):
        with sheet_env() as env:
            stray = env.td / "outside.png"
            Image.new("RGB", (8, 8)).save(stray)
            env.record["sample_image_path"] = str(stray)
            with self.assertRaises(FileNotFoundError):
                P.generate_character_sheet("bizarrotrn")
            self.assertEqual(env.calls, [])

    def test_busy_when_gpu_lock_held(self):
        with sheet_env() as env:
            self.assertTrue(P._GPU_LOCK.acquire(blocking=False))
            try:
                with self.assertRaises(P.CharacterSheetBusyError):
                    P.generate_character_sheet("bizarrotrn")
            finally:
                P._GPU_LOCK.release()
            self.assertEqual(env.calls, [])

    def test_gpu_lock_released_after_success_and_failure(self):
        with sheet_env():
            P.generate_character_sheet("bizarrotrn")
            self.assertTrue(P._GPU_LOCK.acquire(blocking=False))
            P._GPU_LOCK.release()
        with sheet_env(engine_error_on_call=2):
            with self.assertRaises(RuntimeError):
                P.generate_character_sheet("bizarrotrn")
            self.assertTrue(P._GPU_LOCK.acquire(blocking=False))
            P._GPU_LOCK.release()


class TestSheetArtifacts(unittest.TestCase):
    def test_sheet_png_is_the_real_composite_and_the_write_is_atomic(self):
        with sheet_env() as env:
            out = P.generate_character_sheet("bizarrotrn")
            sheet = env.char_dir / "sheet.png"
            self.assertEqual(out["sheet_path"], str(sheet))
            with Image.open(sheet) as im:
                # 3 views → the row compositor: 1 row x 3 cells at height
                # 1024, each fake 64x48 view scaled to 1365x1024, 12px
                # gutters → (3*1365 + 4*12) x (1024 + 2*12). Not a
                # pass-through of any single view.
                self.assertEqual(im.size, (3 * 1365 + 4 * 12, 1024 + 2 * 12))
            # Atomic swap left no temp file behind.
            self.assertEqual(list(env.char_dir.glob(".sheet.*")), [])

    def test_single_view_is_written_through_unchanged(self):
        with sheet_env() as env:
            P.generate_character_sheet("bizarrotrn", views=["front"])
            with Image.open(env.char_dir / "sheet.png") as im:
                self.assertEqual(im.size, (64, 48))

    def test_sheet_json_sidecar_schema(self):
        with sheet_env() as env:
            out = P.generate_character_sheet("bizarrotrn", seed=5,
                                             wardrobe="a bowler hat")
            meta = json.loads(
                (env.char_dir / "sheet.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["schema"], "phosphene/character_sheet@1")
            self.assertEqual(meta["character_id"], "bizarrotrn")
            self.assertEqual(meta["engine"], "hidream_inline")
            self.assertEqual(meta["reference"], str(env.ref.resolve()))
            self.assertEqual(meta["wardrobe"], "a bowler hat")
            self.assertEqual(meta["seed"], 5)
            self.assertEqual(len(meta["views"]), 3)
            for i, v in enumerate(meta["views"]):
                self.assertEqual(v["seed"], 5 + i)
                self.assertIn(WARDROBE_PIN, v["prompt"])
                self.assertTrue(Path(v["png_path"]).is_file())
            self.assertIn("created_at", meta)
            self.assertEqual(out["sheet_url"], "/characters/bizarrotrn/sheet")

    def test_engine_failure_leaves_no_sheet_and_no_temp(self):
        with sheet_env(engine_error_on_call=2) as env:
            with self.assertRaises(RuntimeError):
                P.generate_character_sheet("bizarrotrn")
            self.assertFalse((env.char_dir / "sheet.png").exists())
            self.assertFalse((env.char_dir / "sheet.json").exists())
            self.assertEqual(list(env.char_dir.glob(".sheet.*")), [])


class TestBundlePreviewGuard(unittest.TestCase):
    def test_null_preview_is_pointed_at_the_sheet(self):
        with sheet_env(preview=None) as env:
            out = P.generate_character_sheet("bizarrotrn")
            self.assertTrue(out["preview_updated"])
            bundle = json.loads(
                (env.char_dir / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["preview"], "sheet.png")
            # Merge-update, not replace — the rest of the bundle survives.
            self.assertEqual(bundle["name"], "Bizarro")

    def test_absent_preview_key_counts_as_null(self):
        with sheet_env() as env:
            (env.char_dir / "bundle.json").write_text(
                json.dumps({"id": "bizarrotrn", "name": "Bizarro"}),
                encoding="utf-8")
            out = P.generate_character_sheet("bizarrotrn")
            self.assertTrue(out["preview_updated"])
            bundle = json.loads(
                (env.char_dir / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["preview"], "sheet.png")

    def test_curated_preview_is_never_clobbered(self):
        with sheet_env(preview="curated.png") as env:
            out = P.generate_character_sheet("bizarrotrn")
            self.assertFalse(out["preview_updated"])
            bundle = json.loads(
                (env.char_dir / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["preview"], "curated.png")

    def test_no_bundle_is_not_created(self):
        with sheet_env(with_bundle=False) as env:
            out = P.generate_character_sheet("bizarrotrn")
            self.assertFalse(out["preview_updated"])
            self.assertFalse((env.char_dir / "bundle.json").exists())


class TestRecordSurfacing(unittest.TestCase):
    """sheet_image_path / sheet_image_url on the REAL list_characters()."""

    @contextmanager
    def _chars_env(self, *, with_sheet: bool):
        with tempfile.TemporaryDirectory() as td_raw:
            td = Path(td_raw)
            loras = td / "loras"
            chars = td / "characters"
            lab = td / "lab"
            for d in (loras, chars / "bizarrotrn", lab):
                d.mkdir(parents=True)
            (loras / "bizarrotrn_v2.safetensors").write_bytes(b"\0" * 8)
            if with_sheet:
                Image.new("RGB", (32, 32)).save(
                    chars / "bizarrotrn" / "sheet.png")
            saved = (P._safe_loras_dir, P._CHARACTERS_CACHE_PATH,
                     P.LORA_LAB_ROOT, P._ltx_lora_compatibility)
            P._safe_loras_dir = lambda: loras
            P._CHARACTERS_CACHE_PATH = chars
            P.LORA_LAB_ROOT = lab
            # Compatibility probing is not under test here — stub it so the
            # fixture doesn't need a synthetic transformer checkpoint.
            P._ltx_lora_compatibility = lambda p: {
                "ltx_compatible": True, "ltx_compat_reason": "",
                "ltx_fusion_tally": None}
            try:
                yield chars
            finally:
                (P._safe_loras_dir, P._CHARACTERS_CACHE_PATH,
                 P.LORA_LAB_ROOT, P._ltx_lora_compatibility) = saved

    def test_sheet_fields_present_when_sheet_exists(self):
        with self._chars_env(with_sheet=True) as chars:
            recs = P.list_characters()
            self.assertEqual(len(recs), 1)
            rec = recs[0]
            self.assertEqual(rec["sheet_image_path"],
                             str(chars / "bizarrotrn" / "sheet.png"))
            self.assertEqual(rec["sheet_image_url"],
                             "/characters/bizarrotrn/sheet")
            # The pair sits beside the sample pair — both shapes present.
            self.assertIn("sample_image_path", rec)
            self.assertIn("sample_image_url", rec)

    def test_sheet_fields_null_without_a_sheet(self):
        with self._chars_env(with_sheet=False):
            rec = P.list_characters()[0]
            self.assertIsNone(rec["sheet_image_path"])
            self.assertIsNone(rec["sheet_image_url"])


class TestSheetServeRoute(unittest.TestCase):
    def _get(self, path: str) -> dict:
        h = P.Handler.__new__(P.Handler)          # no socket — stub transport
        h.path = path
        h.headers = {}
        h._is_local_request = lambda: True
        out: dict = {}
        h._ok = lambda data, ctype="text/html; charset=utf-8": out.update(
            status=200, data=data, ctype=ctype)
        h.send_error = lambda code, msg=None: out.update(status=code, msg=msg)
        h._json = lambda payload, status=200: out.update(
            status=status, payload=payload)
        h.do_GET()
        return out

    @contextmanager
    def _serve_env(self, *, with_sheet=True, symlink_outside=False):
        with tempfile.TemporaryDirectory() as td_raw:
            td = Path(td_raw)
            chars = td / "characters"
            (chars / "bizarrotrn").mkdir(parents=True)
            sheet = chars / "bizarrotrn" / "sheet.png"
            if with_sheet:
                Image.new("RGB", (128, 96), (200, 40, 40)).save(sheet)
            elif symlink_outside:
                evil = td / "evil.png"
                Image.new("RGB", (8, 8)).save(evil)
                sheet.symlink_to(evil)
            saved = (P._CHARACTERS_CACHE_PATH, P._THUMBCACHE, P.push)
            P._CHARACTERS_CACHE_PATH = chars
            P._THUMBCACHE = td / "thumbcache"
            P.push = lambda line: None
            try:
                yield sheet
            finally:
                (P._CHARACTERS_CACHE_PATH, P._THUMBCACHE, P.push) = saved

    def test_serves_the_sheet_png(self):
        with self._serve_env() as sheet:
            out = self._get("/characters/bizarrotrn/sheet")
            self.assertEqual(out["status"], 200)
            self.assertEqual(out["ctype"], "image/png")
            self.assertEqual(out["data"], sheet.read_bytes())

    def test_404_when_no_sheet(self):
        with self._serve_env(with_sheet=False):
            out = self._get("/characters/bizarrotrn/sheet")
            self.assertEqual(out["status"], 404)

    def test_404_on_traversal_id(self):
        with self._serve_env():
            out = self._get("/characters/../sheet")
            self.assertEqual(out["status"], 404)

    def test_404_when_sheet_resolves_outside_the_characters_root(self):
        with self._serve_env(with_sheet=False, symlink_outside=True):
            out = self._get("/characters/bizarrotrn/sheet")
            self.assertEqual(out["status"], 404)

    def test_w_param_serves_a_thumbnail(self):
        with self._serve_env():
            out = self._get("/characters/bizarrotrn/sheet?w=32")
            self.assertEqual(out["status"], 200)
            self.assertEqual(out["ctype"], "image/jpeg")
            with Image.open(io.BytesIO(out["data"])) as im:
                self.assertLessEqual(im.width, 32)


class TestSheetGenerateRoute(unittest.TestCase):
    def _post(self, path: str, payload=None, *, raw: bytes | None = None,
              length: int | None = None, ctype="application/json",
              effect=None) -> tuple[dict, list]:
        """POST through the real do_POST with generate_character_sheet
        replaced by a recorder. `effect` raising is the status-mapping lane."""
        body = raw if raw is not None else (
            json.dumps(payload).encode() if payload is not None else b"")
        h = P.Handler.__new__(P.Handler)          # no socket — stub transport
        h.path = path
        h.headers = {"Content-Type": ctype,
                     "Content-Length": str(len(body) if length is None
                                           else length)}
        h.rfile = io.BytesIO(body)
        h._is_local_request = lambda: True
        reply: dict = {}
        h._json = lambda payload, status=200: reply.update(
            payload=payload, status=status)

        calls: list[dict] = []

        def fake_sheet(cid, *, engine_override, views, wardrobe, seed):
            calls.append({"cid": cid, "engine_override": engine_override,
                          "views": views, "wardrobe": wardrobe, "seed": seed})
            if effect is not None:
                raise effect
            return {"ok": True, "character_id": cid}

        saved = (P.generate_character_sheet, P.list_characters, P.push)
        P.generate_character_sheet = fake_sheet
        P.list_characters = lambda: []
        P.push = lambda line: None
        try:
            h.do_POST()
        finally:
            (P.generate_character_sheet, P.list_characters, P.push) = saved
        return reply, calls

    def test_empty_body_uses_the_documented_defaults(self):
        reply, calls = self._post("/characters/bizarrotrn/sheet/generate")
        self.assertEqual(reply["status"], 200, reply)
        # An empty engine is the contract now: generate_character_sheet
        # picks HiDream only where its lab checkout exists, else Qwen.
        self.assertEqual(calls, [{"cid": "bizarrotrn",
                                  "engine_override": "",
                                  "views": None, "wardrobe": "", "seed": -1}])
        self.assertTrue(reply["payload"]["ok"])

    def test_body_fields_are_forwarded(self):
        reply, calls = self._post(
            "/characters/bizarrotrn/sheet/generate",
            {"engine_override": "mock_inline", "views": ["front"],
             "wardrobe": "a trench coat", "seed": 7})
        self.assertEqual(reply["status"], 200)
        self.assertEqual(calls[0], {"cid": "bizarrotrn",
                                    "engine_override": "mock_inline",
                                    "views": ["front"],
                                    "wardrobe": "a trench coat", "seed": 7})

    def test_invalid_json_is_400_and_never_renders(self):
        reply, calls = self._post("/characters/bizarrotrn/sheet/generate",
                                  raw=b"{nope")
        self.assertEqual(reply["status"], 400)
        self.assertEqual(calls, [])

    def test_non_object_json_is_400(self):
        reply, calls = self._post("/characters/bizarrotrn/sheet/generate",
                                  raw=b"[1, 2]")
        self.assertEqual(reply["status"], 400)
        self.assertEqual(calls, [])

    def test_oversize_body_is_413(self):
        reply, calls = self._post("/characters/bizarrotrn/sheet/generate",
                                  length=65537)
        self.assertEqual(reply["status"], 413)
        self.assertEqual(calls, [])

    def test_exception_to_status_mapping(self):
        for exc, status in ((ValueError("bad"), 400),
                            (LookupError("gone"), 404),
                            (FileNotFoundError("no ref"), 404),
                            (P.CharacterSheetBusyError("busy"), 429),
                            (RuntimeError("boom"), 500)):
            with self.subTest(exc=type(exc).__name__):
                reply, calls = self._post(
                    "/characters/bizarrotrn/sheet/generate", effect=exc)
                self.assertEqual(reply["status"], status, reply)
                self.assertEqual(len(calls), 1)
                self.assertIn("error", reply["payload"])

    def test_sheet_route_does_not_shadow_the_render_route(self):
        # "<id>/sheet/generate" also endswith "/generate" — the sheet lane
        # must win for its own path AND stay out of the way of the render
        # lane's. A plain /generate with an empty character list answers the
        # render lane's 404 and never touches the sheet recorder.
        reply, calls = self._post(
            "/characters/bizarrotrn/generate", raw=b"",
            ctype="application/x-www-form-urlencoded")
        self.assertEqual(reply["status"], 404, reply)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
