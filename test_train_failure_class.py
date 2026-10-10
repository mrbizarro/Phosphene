#!/usr/bin/env python3
"""A failed training run says WHY, as one closed word (4.19.1).

4.19.0 had nine failed trainings on two Macs and every one reached the fleet
as "training exited with code 1 — see Logs for stack trace": memory, a
missing file, a full disk and a broken venv all looked the same. The trainer
held the exception and emitted an `error` event with its stage and message,
but the panel dropped that event into the generic log branch.

Pinned here:
  * lora_lab.failure.classify_exception — type first, then MLX's fixed
    allocation-failure fragments, through the cause chain;
  * the trainer's `error` event carries `error_class`;
  * the panel keeps only a word from the closed list (an unknown or
    path-shaped value becomes `other`), a SIGKILL with no word is `oom`,
    and its own pre-trainer refusals name their class;
  * render_failed carries `train_error` for training jobs only;
  * the job's error text is unchanged, so the error_class series is too.
The panel half drives the REAL run_train_job_inner against a stand-in
trainer script; no weights, no GPU.
"""
from __future__ import annotations

import errno
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ["LTX_STATE_DIR"] = tempfile.mkdtemp(prefix="phos-train-fail-")
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8316")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402
from lora_lab import failure as F  # noqa: E402


class ClassifyException(unittest.TestCase):
    def test_closed_list_matches_the_panel(self) -> None:
        self.assertEqual(F.TRAIN_ERROR_CLASSES, P.TRAIN_ERROR_CLASSES)

    def test_mlx_allocation_failures_are_oom(self) -> None:
        for text in (
            "[metal::malloc] Attempting to allocate 21474836480 bytes which is greater "
            "than the maximum allowed buffer size of 17179869184 bytes.",
            "[metal::malloc] Resource limit (499000) exceeded.",
            "[METAL] Command buffer execution failed: Insufficient Memory "
            "(00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)",
        ):
            self.assertEqual(F.classify_exception(RuntimeError(text)), "oom", text)
        self.assertEqual(F.classify_exception(MemoryError()), "oom")

    def test_disk_full(self) -> None:
        self.assertEqual(F.classify_exception(OSError(errno.ENOSPC, "x")), "disk_full")
        self.assertEqual(F.classify_exception(
            RuntimeError("Error while serializing: IoError(Os { code: 28, kind: "
                         "StorageFull, message: \"No space left on device\" })")), "disk_full")

    def test_missing_file_through_the_dataset_loaders_wrapper(self) -> None:
        try:
            try:
                raise FileNotFoundError(2, "No such file or directory")
            except FileNotFoundError as e:
                raise RuntimeError("Failed to load latents from /x: missing") from e
        except RuntimeError as wrapped:
            self.assertEqual(F.classify_exception(wrapped), "missing_file")
        self.assertEqual(F.classify_exception(FileNotFoundError(
            "transformer-dev.safetensors not found in /a or /b")), "missing_file")

    def test_import_nan_caption_other(self) -> None:
        self.assertEqual(F.classify_exception(ModuleNotFoundError("No module named 'x'")),
                         "import_error")
        self.assertEqual(F.classify_exception(F.NonFiniteLoss("nan")), "nan_loss")
        self.assertEqual(F.classify_exception(F.CaptionCheckError("x")), "caption_check")
        self.assertEqual(F.classify_exception(ValueError("bad")), "other")
        self.assertEqual(F.classify_exception(None), "other")

    def test_a_path_or_trigger_never_becomes_the_class(self) -> None:
        cls = F.classify_exception(RuntimeError("/Users/someone/zqxtrn_v2.safetensors"))
        self.assertIn(cls, F.TRAIN_ERROR_CLASSES)
        self.assertEqual(cls, "other")


class MissingTrainingWeights(unittest.TestCase):
    """The panel e2e on a fresh 4.19.1 install with no LTX-2.3 pack: the run
    died 9 s in with HFValidationError ("Repo id must be in the form ...", the
    pack's own path read as a repo id) and the class came out `other`."""

    def test_the_hub_shape_of_a_missing_pack_is_missing_file(self) -> None:
        HFValidationError = type("HFValidationError", (ValueError,), {})
        e = HFValidationError("Repo id must be in the form 'repo_name' or "
                              "'namespace/repo_name': '/x/mlx_models/ltx-2.3-mlx-q4'.")
        self.assertEqual(F.classify_exception(e), "missing_file")

    def test_what_is_missing_is_said_before_any_work(self) -> None:
        try:
            from lora_lab import train as lab_train
        except ImportError:
            self.skipTest("needs the trainer venv")
        with tempfile.TemporaryDirectory() as td:
            models = Path(td)
            q4 = models / "ltx-2.3-mlx-q4"
            self.assertIn("base pack", lab_train.training_weights_problem(q4))
            q4.mkdir()
            self.assertIn("dev transformer", lab_train.training_weights_problem(q4))
            small = q4 / "transformer-dev.safetensors"      # the quantized 11 GB copy (#35)
            with open(small, "wb") as f:
                f.truncate(11 * 1000**3)
            self.assertIn("dev transformer", lab_train.training_weights_problem(q4))
            (models / "ltx-2.3-mlx-q8").mkdir()
            with open(models / "ltx-2.3-mlx-q8" / "transformer-dev.safetensors", "wb") as f:
                f.truncate(21 * 1000**3)                     # sparse: no disk used
            self.assertIsNone(lab_train.training_weights_problem(q4))

    def test_the_trainer_stops_at_once_with_the_class(self) -> None:
        try:
            import lora_lab.train_character as tc
        except ImportError:
            self.skipTest("needs the trainer venv")
        with tempfile.TemporaryDirectory() as td:
            spec = Path(td) / "spec.json"
            spec.write_text(json.dumps({"job_id": "j", "trigger": "zqxtrn", "preset": "quick",
                                        "images_dir": str(Path(td) / "images"),
                                        "output_path": str(Path(td) / "out.safetensors")}))
            buf = io.StringIO()
            with mock.patch.object(tc, "DEFAULT_MODEL_PATH", str(Path(td) / "ltx-2.3-mlx-q4")), \
                    mock.patch.object(tc, "_REAL_STDOUT", buf), self.assertRaises(SystemExit):
                tc.run_pipeline(spec)
            ev = json.loads(buf.getvalue().strip().splitlines()[-1])
            self.assertEqual((ev["event"], ev["stage"], ev["error_class"]),
                             ("error", "models", "missing_file"))


class TrainerErrorEvent(unittest.TestCase):
    def _emitted(self, mod, **kw) -> dict:
        buf = io.StringIO()
        with mock.patch.object(mod, "_REAL_STDOUT", buf), self.assertRaises(SystemExit):
            mod.emit_error_and_exit(**kw)
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_face_and_voice_trainers_send_the_class(self) -> None:
        import lora_lab.train_audio as ta
        import lora_lab.train_character as tc
        for mod in (tc, ta):
            ev = self._emitted(mod, stage="train", message="m",
                               exc=RuntimeError("[metal::malloc] Resource limit (499000) exceeded."))
            self.assertEqual((ev["event"], ev["stage"], ev["error_class"]), ("error", "train", "oom"))
            ev = self._emitted(mod, stage="config", message="m")
            self.assertEqual(ev["error_class"], "other")


def _fake_trainer(body: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="phos-fake-trainer-"))
    sh = d / "lora_lab_run.sh"
    sh.write_text("#!/bin/bash\n" + body + "\n")
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC)
    return sh


class PanelKeepsTheWord(unittest.TestCase):
    TRIGGER = "zqxtrn"

    def _job(self, sh: Path) -> dict:
        from PIL import Image
        jid = "tfail" + os.urandom(4).hex()
        images = P._train_dataset_dir(jid) / "images"
        images.mkdir(parents=True, exist_ok=True)
        for i in range(P.TRAIN_MIN_IMAGES):
            Image.new("RGB", (64, 64), (i * 9, 40, 80)).save(images / f"char_{i:03d}.png")
        self._sh = sh
        return {"id": "q-" + jid, "params": {"mode": "train", "train_job_id": jid,
                                              "trigger": self.TRIGGER, "preset": "quick",
                                              "steps": 10, "rank": 4}}

    def _run(self, body: str) -> tuple[dict, str]:
        sh = _fake_trainer(body)
        job = self._job(sh)
        with mock.patch.object(P, "LORA_LAB_RUN_SH", sh):
            with self.assertRaises(RuntimeError) as cm:
                P.run_train_job_inner(job)
        return job, str(cm.exception)

    def test_the_trainers_class_reaches_the_job(self) -> None:
        ev = {"event": "error", "stage": "train", "error_class": "oom",
              "message": "[metal::malloc] Resource limit (499000) exceeded."}
        job, err = self._run(f"echo '{json.dumps(ev)}'\nexit 1")
        self.assertEqual(job["train_error"], "oom")
        # Same sentence as 4.19.0: the error_class / error_signature series
        # stay comparable across the release.
        self.assertEqual(err, "training exited with code 1 — see Logs for stack trace")

    def test_an_unknown_word_is_not_forwarded(self) -> None:
        ev = {"event": "error", "stage": "train", "error_class": "/Users/x/zqxtrn", "message": "m"}
        job, _ = self._run(f"echo '{json.dumps(ev)}'\nexit 1")
        self.assertEqual(job["train_error"], "other")

    def test_no_event_exit_1_is_other_and_sigkill_is_oom(self) -> None:
        job, _ = self._run("echo boom\nexit 1")
        self.assertEqual(job["train_error"], "other")
        job, _ = self._run("kill -9 $$")
        self.assertEqual(job["train_error"], "oom")

    def test_the_caption_refusal_names_itself(self) -> None:
        sh = _fake_trainer("exit 0")
        job = self._job(sh)
        caps = P._train_dataset_dir(job["params"]["train_job_id"]) / "captions"
        caps.mkdir(parents=True, exist_ok=True)
        for i in range(P.TRAIN_MIN_IMAGES):
            (caps / f"char_{i:03d}.txt").write_text("[VISUAL]: othertrn, a man\n")
        with mock.patch.object(P, "LORA_LAB_RUN_SH", sh), self.assertRaises(RuntimeError):
            P.run_train_job_inner(job)
        self.assertEqual(job["train_error"], "caption_check")

    def test_the_render_helper_is_released_before_training(self) -> None:
        killed = []
        with mock.patch.object(P.HELPER, "is_alive", lambda: True), \
                mock.patch.object(P.HELPER, "kill", lambda: killed.append(1)):
            self._run("exit 1")
        self.assertEqual(killed, [1])

    def test_the_trainers_log_line_is_shown(self) -> None:
        pushed = []
        ev = {"event": "log", "line": "recipe: adam_bias_correction=False"}
        with mock.patch.object(P, "push", lambda s: pushed.append(s)):
            self._run(f"echo '{json.dumps(ev)}'\nexit 1")
        self.assertIn("[train] recipe: adam_bias_correction=False", pushed)


class TheEventCarriesIt(unittest.TestCase):
    def _props(self, job: dict) -> dict:
        sent = []
        with mock.patch.object(P, "_analytics_capture", lambda e, p: sent.append((e, p))), \
                mock.patch.object(P, "get_settings", lambda: {"analytics_first_render_reported": True}):
            P._analytics_render_event(job)
        return sent[0][1]

    def test_train_failures_only(self) -> None:
        err = "training exited with code 1 — see Logs for stack trace"
        p = self._props({"status": "failed", "error": err, "train_error": "oom",
                         "params": {"mode": "train", "width": 512, "height": 512}})
        self.assertEqual(p["train_error"], "oom")
        self.assertEqual(p["error_class"], "helper_exit")
        p = self._props({"status": "failed", "error": err, "train_error": "nonsense",
                         "params": {"mode": "train"}})
        self.assertEqual(p["train_error"], "other")
        p = self._props({"status": "failed", "error": "boom", "params": {"mode": "t2v"}})
        self.assertNotIn("train_error", p)
        p = self._props({"status": "done", "params": {"mode": "train"}})
        self.assertNotIn("train_error", p)


if __name__ == "__main__":
    unittest.main()
