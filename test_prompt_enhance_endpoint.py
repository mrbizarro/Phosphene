#!/usr/bin/env python3
"""Regression gate for the real /prompt/enhance HTTP + helper IPC lane.

The test boots mlx_ltx_panel.py on the reserved test port 8262 with isolated
state/output/upload directories. A tiny newline-JSON helper double verifies
that the panel gives rendering Gemma 4 while independently giving enhancement
Gemma 3, then returns success and failure terminal events. This exercises the
real Handler, WarmHelper subprocess round-trip, and JSON response serializer
without loading model weights or touching the GPU.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
PORT = 8262
BASE_URL = f"http://127.0.0.1:{PORT}"
VALID_PROMPT = "a knight rides through a foggy forest at dawn"


def _emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def _run_fake_helper() -> None:
    """Speak the warm helper protocol while checking the two Gemma seams."""
    render_gemma = os.environ.get("LTX_GEMMA", "")
    enhance_gemma = os.environ.get("LTX_ENHANCE_GEMMA", "")
    expected_render = os.environ["PHOSPHENE_TEST_RENDER_GEMMA"]
    expected_enhance = os.environ["PHOSPHENE_TEST_ENHANCE_GEMMA"]
    _emit({
        "event": "ready",
        "model": os.environ.get("LTX_MODEL"),
        "gemma": render_gemma,
        "low_memory": True,
    })
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
            action = msg.get("action")
            if action == "enhance_prompt":
                job_id = msg.get("id")
                prompt = (msg.get("params") or {}).get("prompt", "")
                if prompt == "force helper error":
                    _emit({"event": "error", "id": job_id,
                           "error": "forced helper failure"})
                elif prompt == "force invalid result":
                    _emit({"event": "done", "id": job_id,
                           "enhanced": None, "elapsed_sec": 0.01})
                elif prompt == "force helper timeout":
                    continue
                elif render_gemma != expected_render:
                    _emit({"event": "error", "id": job_id,
                           "error": f"render encoder mismatch: {render_gemma}"})
                elif enhance_gemma != expected_enhance:
                    _emit({"event": "error", "id": job_id,
                           "error": f"enhancer encoder mismatch: {enhance_gemma}"})
                else:
                    _emit({
                        "event": "done",
                        "id": job_id,
                        "original": prompt,
                        "enhanced": f"Cinematic detail: {prompt}",
                        "elapsed_sec": 0.01,
                    })
            elif action == "exit":
                _emit({"event": "exit", "reason": "requested"})
                return
            else:
                _emit({"event": "error", "id": msg.get("id"),
                       "error": f"unsupported test action: {action}"})
        except Exception as exc:  # pragma: no cover - test helper safety net
            _emit({"event": "error", "error": str(exc)})


def _request(path: str, fields: dict[str, str] | None = None):
    data = None if fields is None else urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(BASE_URL + path, data=data)
    return urllib.request.urlopen(request, timeout=10)


class PromptEnhanceEndpointTest(unittest.TestCase):
    def test_endpoint_uses_gemma3_and_always_returns_json(self) -> None:
        # Refuse to interfere with any process already using the requested test
        # port. Cleanup below only signals the exact Popen PID saved here.
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", PORT))
        except PermissionError as exc:
            self.skipTest(f"sandbox forbids binding localhost: {exc}")
        except OSError as exc:
            self.fail(f"test port {PORT} is already in use: {exc}")
        finally:
            probe.close()

        with tempfile.TemporaryDirectory(prefix=".enhance-endpoint-", dir=ROOT) as tmp:
            scratch = Path(tmp)
            models = scratch / "models"
            gemma3 = models / "gemma-3-12b-it-4bit"
            gemma4 = models / "gemma4-12b-ltx25-q4"
            gemma3.mkdir(parents=True)
            gemma4.mkdir(parents=True)

            # WarmHelper invokes LTX_HELPER_PYTHON with mlx_warm_helper.py as
            # argv[1]. This interpreter shim intentionally ignores that argv
            # and re-enters this file in fake-helper mode.
            shim = scratch / "fake-helper-python"
            shim.write_text(
                "#!/bin/sh\n"
                "exec " + " ".join(shlex.quote(v) for v in (
                    sys.executable, str(Path(__file__).resolve()), "--fake-helper"
                )) + "\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "LTX_PORT": str(PORT),
                "LTX_STATE_DIR": str(scratch / "state"),
                "LTX_OUTPUT_DIR": str(scratch / "outputs"),
                "LTX_UPLOADS_DIR": str(scratch / "uploads"),
                "LTX_MODELS_DIR": str(models),
                "LTX_GEMMA_PATH": str(gemma3),
                "LTX_HELPER_PYTHON": str(shim),
                "LTX_MODEL_VERSION": "ltx25",
                "PHOSPHENE_TEST_RENDER_GEMMA": str(gemma4),
                "PHOSPHENE_TEST_ENHANCE_GEMMA": str(gemma3),
                "PHOSPHENE_DISABLE_VERSION_CHECK": "1",
                "PHOSPHENE_ANALYTICS_DISABLED": "1",
                "LTX_PROMPT_ENHANCE_TIMEOUT": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "mlx_ltx_panel.py")],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                deadline = time.time() + 30
                while time.time() < deadline:
                    if proc.poll() is not None:
                        output = proc.stdout.read() if proc.stdout else ""
                        self.fail(f"test panel exited during boot:\n{output}")
                    try:
                        with _request("/status") as response:
                            if response.status == 200:
                                break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.1)
                else:
                    self.fail("test panel did not listen on port 8262 within 30s")

                with _request("/prompt/enhance", {"prompt": VALID_PROMPT}) as response:
                    body = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertGreater(len(body), 0)
                    payload = json.loads(body)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["original"], VALID_PROMPT)
                self.assertEqual(payload["enhanced"], f"Cinematic detail: {VALID_PROMPT}")

                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request("/prompt/enhance", {"prompt": "force helper error"})
                self.assertEqual(caught.exception.code, 500)
                error_body = caught.exception.read()
                self.assertGreater(len(error_body), 0)
                self.assertEqual(json.loads(error_body)["error"], "forced helper failure")

                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request("/prompt/enhance", {"prompt": "force invalid result"})
                self.assertEqual(caught.exception.code, 500)
                invalid_body = caught.exception.read()
                self.assertGreater(len(invalid_body), 0)
                self.assertIn("invalid enhanced prompt", json.loads(invalid_body)["error"])

                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request("/prompt/enhance", {"prompt": "force helper timeout"})
                self.assertEqual(caught.exception.code, 500)
                timeout_body = caught.exception.read()
                self.assertGreater(len(timeout_body), 0)
                self.assertIn("timed out", json.loads(timeout_body)["error"])
            finally:
                # Kill the helper through its endpoint first, then terminate
                # only the panel PID this test saved. Never use pkill here.
                if proc.poll() is None:
                    try:
                        with _request("/helper/restart", {}) as response:
                            response.read()
                    except Exception:
                        pass
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()

    def test_handler_and_helper_round_trip_without_a_socket(self) -> None:
        """Restricted sandboxes still execute the same Handler + IPC path.

        The port-8262 case above remains the primary regression and runs on a
        normal host/CI worker. This companion invokes the real Handler object
        directly so a sandbox that returns EPERM from bind(2) still proves the
        success body, helper-error body, and split Gemma environment.
        """
        with tempfile.TemporaryDirectory(prefix=".enhance-handler-", dir=ROOT) as tmp:
            scratch = Path(tmp)
            models = scratch / "models"
            gemma3 = models / "gemma-3-12b-it-4bit"
            gemma4 = models / "gemma4-12b-ltx25-q4"
            gemma3.mkdir(parents=True)
            gemma4.mkdir(parents=True)
            shim = scratch / "fake-helper-python"
            shim.write_text(
                "#!/bin/sh\n"
                "exec " + " ".join(shlex.quote(v) for v in (
                    sys.executable, str(Path(__file__).resolve()), "--fake-helper"
                )) + "\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)

            overrides = {
                "LTX_PORT": str(PORT),
                "LTX_STATE_DIR": str(scratch / "state"),
                "LTX_OUTPUT_DIR": str(scratch / "outputs"),
                "LTX_UPLOADS_DIR": str(scratch / "uploads"),
                "LTX_MODELS_DIR": str(models),
                "LTX_GEMMA_PATH": str(gemma3),
                "LTX_HELPER_PYTHON": str(shim),
                "LTX_MODEL_VERSION": "ltx25",
                "PHOSPHENE_TEST_RENDER_GEMMA": str(gemma4),
                "PHOSPHENE_TEST_ENHANCE_GEMMA": str(gemma3),
                "PHOSPHENE_DISABLE_VERSION_CHECK": "1",
                "PHOSPHENE_ANALYTICS_DISABLED": "1",
                "LTX_PROMPT_ENHANCE_TIMEOUT": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            previous = {key: os.environ.get(key) for key in overrides}
            os.environ.update(overrides)
            panel = None
            try:
                spec = importlib.util.spec_from_file_location(
                    "prompt_enhance_panel_under_test", ROOT / "mlx_ltx_panel.py"
                )
                panel = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = panel
                spec.loader.exec_module(panel)

                def invoke(prompt: str) -> tuple[int, bytes]:
                    body = urllib.parse.urlencode({"prompt": prompt}).encode()
                    handler = object.__new__(panel.Handler)
                    handler.path = "/prompt/enhance"
                    handler.command = "POST"
                    handler.request_version = "HTTP/1.1"
                    handler.requestline = "POST /prompt/enhance HTTP/1.1"
                    handler.client_address = ("127.0.0.1", 0)
                    handler.headers = {
                        "Host": f"127.0.0.1:{PORT}",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Content-Length": str(len(body)),
                    }
                    handler.rfile = io.BytesIO(body)
                    handler.wfile = io.BytesIO()
                    handler.do_POST()
                    wire = handler.wfile.getvalue()
                    head, response_body = wire.split(b"\r\n\r\n", 1)
                    status = int(head.splitlines()[0].split()[1])
                    return status, response_body

                status, body = invoke(VALID_PROMPT)
                self.assertEqual(status, 200)
                self.assertGreater(len(body), 0)
                payload = json.loads(body)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["enhanced"], f"Cinematic detail: {VALID_PROMPT}")

                status, body = invoke("force helper error")
                self.assertEqual(status, 500)
                self.assertGreater(len(body), 0)
                self.assertEqual(json.loads(body)["error"], "forced helper failure")

                status, body = invoke("force invalid result")
                self.assertEqual(status, 500)
                self.assertGreater(len(body), 0)
                self.assertIn("invalid enhanced prompt", json.loads(body)["error"])

                # Keep the Popen reference because the timeout path correctly
                # clears WarmHelper.proc after killing it; close the captured
                # test-double pipes explicitly so ResourceWarning stays quiet.
                timeout_helper_proc = panel.HELPER.proc
                status, body = invoke("force helper timeout")
                self.assertEqual(status, 500)
                self.assertGreater(len(body), 0)
                self.assertIn("timed out", json.loads(body)["error"])
                if timeout_helper_proc is not None:
                    for stream in (timeout_helper_proc.stdin, timeout_helper_proc.stdout):
                        if stream is not None and not stream.closed:
                            stream.close()
            finally:
                if panel is not None:
                    helper_proc = panel.HELPER.proc
                    panel.HELPER.kill()
                    if helper_proc is not None:
                        for stream in (helper_proc.stdin, helper_proc.stdout):
                            if stream is not None and not stream.closed:
                                stream.close()
                sys.modules.pop("prompt_enhance_panel_under_test", None)
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == "__main__":
    if "--fake-helper" in sys.argv:
        _run_fake_helper()
    else:
        unittest.main(verbosity=2)
