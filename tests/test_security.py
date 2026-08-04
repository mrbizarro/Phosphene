from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from phosphene_security import (
    SECURITY_HEADERS,
    is_trusted_loopback_request,
    sanitized_subprocess_env,
    validated_bfl_base_url,
)
from scripts.download_h3_models import SELECTIONS as H3_MODEL_SELECTIONS


class LoopbackRequestTests(unittest.TestCase):
    def test_same_origin_requests_are_allowed(self) -> None:
        self.assertTrue(
            is_trusted_loopback_request(
                "127.0.0.1:8198", "http://127.0.0.1:8198", 8198
            )
        )
        self.assertTrue(
            is_trusted_loopback_request(
                "localhost:8198", "http://localhost:8198", 8198
            )
        )
        self.assertTrue(
            is_trusted_loopback_request("[::1]:8198", "http://[::1]:8198", 8198)
        )

    def test_local_cli_without_origin_is_allowed(self) -> None:
        self.assertTrue(is_trusted_loopback_request("127.0.0.1:8198", "", 8198))
        self.assertTrue(is_trusted_loopback_request("", "", 8198))

    def test_dns_rebinding_and_cross_port_origins_are_rejected(self) -> None:
        rejected = (
            ("attacker.example:8198", "", 8198),
            ("localhost:9999", "", 8198),
            ("127.0.0.1:8198", "https://attacker.example", 8198),
            ("127.0.0.1:8198", "http://127.0.0.1:9000", 8198),
            ("127.0.0.1:8198", "http://localhost:8198", 8198),
            ("localhost:8198", "http://localhost:8198/path", 8198),
            ("127.0.0.1:8198", "null", 8198),
            ("user@localhost:8198", "", 8198),
        )
        for host, origin, port in rejected:
            with self.subTest(host=host, origin=origin):
                self.assertFalse(is_trusted_loopback_request(host, origin, port))


class BrowserHeaderTests(unittest.TestCase):
    def test_headers_block_framing_and_mime_sniffing(self) -> None:
        self.assertEqual(SECURITY_HEADERS["X-Frame-Options"], "DENY")
        self.assertEqual(SECURITY_HEADERS["X-Content-Type-Options"], "nosniff")
        self.assertIn(
            "frame-ancestors 'none'", SECURITY_HEADERS["Content-Security-Policy"]
        )
        self.assertIn("object-src 'none'", SECURITY_HEADERS["Content-Security-Policy"])


class SubprocessEnvironmentTests(unittest.TestCase):
    def test_unrelated_credentials_are_removed(self) -> None:
        source = {
            "PATH": "/usr/bin",
            "HOME": "/Users/test",
            "LTX_MODEL": "/models/ltx",
            "LTX_API_KEY": "custom-secret",
            "HF_HOME": "/cache/hf",
            "HF_HUB_TOKEN": "hf-secret-alias",
            "PYTHONPATH": "/tmp/injected-module-path",
            "GH_TOKEN": "github-secret",
            "GITHUB_TOKEN": "github-secret-2",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "OPENAI_API_KEY": "openai-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "BFL_API_KEY": "bfl-secret",
            "CIVITAI_API_KEY": "civitai-secret",
            "MallocStackLogging": "1",
        }
        env = sanitized_subprocess_env(source)
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["LTX_MODEL"], "/models/ltx")
        self.assertEqual(env["HF_HOME"], "/cache/hf")
        self.assertNotIn("LTX_API_KEY", env)
        self.assertNotIn("HF_HUB_TOKEN", env)
        self.assertNotIn("PYTHONPATH", env)
        for secret_name in source.keys() - env.keys():
            if secret_name in {"GH_TOKEN", "GITHUB_TOKEN"} or secret_name.endswith(
                ("_KEY", "_TOKEN")
            ):
                self.assertNotIn(secret_name, env)
        self.assertNotIn("MallocStackLogging", env)

    def test_secret_requires_explicit_allowlist(self) -> None:
        source = {"PATH": "/usr/bin", "HF_TOKEN": "hf_secret"}
        self.assertNotIn("HF_TOKEN", sanitized_subprocess_env(source))
        self.assertEqual(
            sanitized_subprocess_env(source, allow_secrets=("HF_TOKEN",))["HF_TOKEN"],
            "hf_secret",
        )


class BflEndpointTests(unittest.TestCase):
    def test_official_endpoint_is_canonicalized(self) -> None:
        self.assertEqual(
            validated_bfl_base_url("https://api.bfl.ml/v1/"),
            "https://api.bfl.ml/v1",
        )

    def test_credential_redirect_endpoints_are_rejected(self) -> None:
        bad = (
            "http://api.bfl.ml/v1",
            "https://attacker.example/v1",
            "https://api.bfl.ml.attacker.example/v1",
            "https://user@api.bfl.ml/v1",
            "https://api.bfl.ml/v2",
            "https://api.bfl.ml/v1?redirect=https://attacker.example",
        )
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validated_bfl_base_url(value)


class RepoStatsCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state_dir = tempfile.mkdtemp(prefix="phosphene-security-test-")
        os.environ["LTX_STATE_DIR"] = cls.state_dir
        import mlx_ltx_panel

        cls.panel = mlx_ltx_panel

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.state_dir, ignore_errors=True)

    def test_generic_github_credentials_are_ignored(self) -> None:
        with mock.patch.object(self.panel, "REPO_STATS_ENABLED", True), mock.patch.dict(
            os.environ,
            {
                "GH_TOKEN": "generic-gh",
                "GITHUB_TOKEN": "generic-github",
                "PHOSPHENE_REPO_STATS_TOKEN": "",
            },
            clear=False,
        ):
            self.assertEqual(self.panel._resolve_github_token(), "")

    def test_dedicated_repo_stats_token_is_accepted_only_when_enabled(self) -> None:
        with mock.patch.dict(
            os.environ, {"PHOSPHENE_REPO_STATS_TOKEN": "dedicated"}, clear=False
        ):
            with mock.patch.object(self.panel, "REPO_STATS_ENABLED", False):
                self.assertEqual(self.panel._resolve_github_token(), "")
            with mock.patch.object(self.panel, "REPO_STATS_ENABLED", True):
                self.assertEqual(self.panel._resolve_github_token(), "dedicated")

    def test_fetcher_rejects_generic_github_credentials(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts/fetch_repo_stats.py"
        cp = subprocess.run(
            [sys.executable, str(script), "--dry-run"],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "GH_TOKEN": "generic-gh",
                "GITHUB_TOKEN": "generic-github",
            },
            timeout=10,
        )
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("PHOSPHENE_REPO_STATS_TOKEN", cp.stderr)


class SupplyChainPinTests(unittest.TestCase):
    def test_required_model_repositories_use_immutable_revisions(self) -> None:
        manifest = json.loads(
            (Path(__file__).resolve().parents[1] / "required_files.json").read_text()
        )
        self.assertTrue(manifest["repos"])
        for repo in manifest["repos"]:
            with self.subTest(repo=repo["repo_id"]):
                self.assertRegex(repo.get("revision", ""), r"^[0-9a-f]{40}$")

    def test_h3_model_repositories_use_immutable_revisions(self) -> None:
        self.assertTrue(H3_MODEL_SELECTIONS)
        for selection in H3_MODEL_SELECTIONS:
            with self.subTest(repo=selection["repo"]):
                self.assertTrue(
                    re.fullmatch(r"[0-9a-f]{40}", selection["revision"])
                )

    def test_curated_remote_loras_use_immutable_revisions(self) -> None:
        for selection in RepoStatsCredentialTests.panel.CURATED_LORAS.values():
            with self.subTest(repo=selection["repo_id"]):
                self.assertRegex(selection.get("revision", ""), r"^[0-9a-f]{40}$")

    def test_constraint_files_are_exact_and_unique(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for filename in ("runtime-constraints.txt", "h3-constraints.txt"):
            seen: set[str] = set()
            for raw in (root / filename).read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                with self.subTest(file=filename, line=line):
                    self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[^\s]+$")
                    name = re.sub(r"[-_.]+", "-", line.split("==", 1)[0].lower())
                    self.assertNotIn(name, seen)
                    seen.add(name)


class ZipArchiveLimitTests(unittest.TestCase):
    def test_zip_bomb_and_oversized_entry_are_rejected(self) -> None:
        panel = RepoStatsCredentialTests.panel
        bomb = zipfile.ZipInfo("image_001.png")
        bomb.file_size = panel.TRAIN_BUNDLE_MAX_UNCOMPRESSED_BYTES + 1
        self.assertIn("1 GB", panel._train_bundle_size_error([bomb]))

        oversized = zipfile.ZipInfo("image_002.png")
        oversized.file_size = panel.TRAIN_MAX_BYTES_PER_IMAGE + 1
        self.assertIn("per-file", panel._train_bundle_size_error([oversized]))


if __name__ == "__main__":
    unittest.main()
