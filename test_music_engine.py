"""YuE2 integration contracts. CPU only, fake packs, no model inference."""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="music-test-state-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
import mlx_ltx_panel as P
from scripts.pinokio import music_fetch as F
from scripts.extract_panel_js import extract_function


@pytest.fixture
def pack(tmp_path, monkeypatch):
    models = tmp_path / "models"
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    (engine / ".venv/bin").mkdir(parents=True)
    (engine / ".venv/pyvenv.cfg").write_text("fixture\n")
    (engine / ".venv/bin/python").symlink_to(sys.executable)
    for part, names, manifest in (("generator", F.GENERATOR_FILES, "conversion.json"),
                                  ("vae", ("model.safetensors", "config.json", "LICENSE", "THIRD_PARTY_NOTICES.md", "licenses/notice.txt"), "weights_manifest.json")):
        recs = {}
        for name in names:
            target = models / part / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(("fixture: " + name).encode())
            recs[name] = {"bytes": target.stat().st_size, "sha256": F.digest(target)}
        if part == "vae":
            monkeypatch.setattr(F, "VAE_SHA256", recs["model.safetensors"]["sha256"])
            monkeypatch.setattr(F, "VAE_BYTES", recs["model.safetensors"]["bytes"])
        (models / part / manifest).write_text(json.dumps({"files": recs}))
    (models / "pack_source.json").write_text(json.dumps({"sources": F.SOURCES,
        "files": {"generator/config.json": {"bytes": (models / "generator/config.json").stat().st_size,
                                              "sha256": F.digest(models / "generator/config.json")}}}))
    monkeypatch.setattr(P, "MUSIC_ROOT", engine)
    monkeypatch.setattr(P, "MUSIC_MODELS", models)
    monkeypatch.setattr(P, "SYSTEM_RAM_GB", 24.0)
    monkeypatch.setattr(P, "OUTPUT", tmp_path / "outputs")
    P.OUTPUT.mkdir()
    monkeypatch.setattr(P, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(P, "MUSIC_GPU_DIR_LOCK", tmp_path / "gpu-dir")
    monkeypatch.setattr(P, "MUSIC_GPU_FILE_LOCK", tmp_path / "gpu-file")
    monkeypatch.setattr(P, "_PROC_GUARDS", {"music": (tmp_path / "music_running.json", "yue2_run.py")})
    monkeypatch.setitem(P.STATE, "queue", [])
    monkeypatch.setitem(P.STATE, "history", [])
    monkeypatch.setitem(P.STATE, "current", None)
    monkeypatch.setitem(P.STATE, "music_pgid", None)
    monkeypatch.setitem(P.STATE, "pid", None)
    monkeypatch.setattr(P, "persist_queue", lambda: None)
    return models, engine


@pytest.mark.parametrize("ram,capable", [(16, False), (24, True), (64, True)])
def test_status_vocabulary_and_capability(pack, monkeypatch, ram, capable):
    monkeypatch.setattr(P, "SYSTEM_RAM_GB", ram)
    status = P.music_status()
    assert {"capable", "available", "installed", "reason", "repairable", "missing", "root", "min_ram_gb"} <= status.keys()
    assert status["capable"] is capable
    assert status["available"] is status["installed"] is True
    assert status["reason"] == "ok" and status["missing"] == []
    assert status["eta_measured"] is False
    assert status["estimates"]["final"]["240"]["eta_sec"] == round((P.MUSIC_FIXED_SEC + 240 * 0.93) * P._hw_speed_factor("ltx"))
    assert P.music_estimate("draft", 60)["eta_sec"] < P.music_estimate("draft", 120)["eta_sec"] < P.music_estimate("final", 120)["eta_sec"]


def test_install_repair_missing_matrix(pack, monkeypatch):
    models, engine = pack
    (engine / ".venv/bin/python").unlink()
    s = P.music_status()
    assert s["reason"] == "missing_venv" and s["repairable"] and s["venv_broken"]
    (engine / ".venv/bin/python").symlink_to(sys.executable)
    (engine / "pyproject.toml").unlink()
    assert P.music_status()["reason"] == "missing_runner"
    (models / "generator/ar-8bit.safetensors").unlink()
    assert P.music_status()["reason"] == "missing_weights"
    monkeypatch.setattr(P, "MUSIC_ROOT", engine / "absent")
    assert P.music_status()["reason"] == "not_installed"


@pytest.fixture
def http_server(pack):
    # Exercise BaseHTTPRequestHandler's real parsing + route dispatch without
    # reserving a port or starting the panel worker (which could launch jobs).
    class Connection:
        def __init__(self, data):
            self.data = data
            self.sent = bytearray()

        def makefile(self, *args):
            return io.BytesIO(self.data)

        def sendall(self, data):
            self.sent.extend(data)

    def request(method, path, data=None, headers=None):
        body = data.encode() if isinstance(data, str) else (data or b"")
        hdr = {"Host": "127.0.0.1:8411", "Content-Length": str(len(body)), **(headers or {})}
        raw = f"{method} {path} HTTP/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdr.items()) + "\r\n"
        conn = Connection(raw.encode() + body)
        P.Handler(conn, ("127.0.0.1", 12345), SimpleNamespace(server_port=8411))
        result = http.client.HTTPResponse(Connection(bytes(conn.sent)))
        result.begin()
        status, hdr, body = result.status, dict(result.getheaders()), result.read()
        return status, hdr, body
    return request


def test_real_form_urlencoded_queue_allowlist(http_server):
    values = {"mode": "music", "engine": "music", "music_lyrics": "[Verse]\nWords\n\n[Chorus]\nMore",
              "music_style": "English piano pop", "music_mode": "melody", "music_instrumental": "off",
              "music_max_seconds": "165", "music_seed": "42", "music_quality": "draft"}
    for instrumental in ("off", "on"):
        values["music_instrumental"] = instrumental
        code, _, body = http_server("POST", "/queue/add", urlencode(values), {"Content-Type": "application/x-www-form-urlencoded"})
        assert code == 200, body
        p = P.STATE["queue"][-1]["params"]
        for key in values.keys() - {"engine", "mode"}:
            assert key in p, key
        assert p["music_instrumental"] is (instrumental == "on")
        assert p["music_lyrics"] == ("" if instrumental == "on" else values["music_lyrics"])
        assert p["music_style"] == values["music_style"]
        assert p["music_mode"] == "melody" and p["music_quality"] == "draft"
        assert p["music_seed"] == 42 and p["music_max_seconds"] == 165


def test_status_bootstrap_and_own_progress(http_server):
    job = P.make_job({"mode": "music", "music_style": "piano"})
    job["progress"] = P.music_progress("[music] synth 4/8")
    P.STATE["current"] = job
    code, _, body = http_server("GET", "/status")
    payload = json.loads(body)
    assert code == 200 and payload["music"]["available"]
    assert payload["current"]["progress"] == job["progress"]
    code, _, body = http_server("GET", "/")
    assert code == 200 and b'id="musicComposePane" hidden' in body
    boot = json.loads(body.decode().split("const BOOT = ", 1)[1].split(";</script>", 1)[0])
    assert boot["music"]["available"] and boot["music"]["min_ram_gb"] == 24


def test_music_does_not_reprice_video_history(pack, monkeypatch):
    monkeypatch.setitem(P.STATE, "history", [
        {"status": "done", "elapsed_sec": 2, "params": {"engine": "music", "mode": "music"}},
        {"status": "done", "elapsed_sec": 600, "params": {"engine": "ltx", "mode": "t2v"}},
    ])
    assert P._avg_elapsed("video") == 600


def test_storage_removal_is_guarded(http_server):
    row = next(r for r in P.storage_rows() if r["key"] == "music")
    assert row["bytes"] > 0 and row["removable"]
    P.STATE["queue"] = [P.make_job({"mode": "music", "music_style": "piano"})]
    code, _, _ = http_server("POST", "/models/remove", urlencode({"repo_key": "music"}), {"Content-Type": "application/x-www-form-urlencoded"})
    assert code == 409 and P.MUSIC_MODELS.exists()
    P.STATE["queue"] = []
    code, _, body = http_server("POST", "/models/remove", urlencode({"repo_key": "music"}), {"Content-Type": "application/x-www-form-urlencoded"})
    assert code == 200, body
    assert not P.MUSIC_MODELS.exists() and not P.music_status()["available"]


@pytest.mark.parametrize("quality,steps", [("draft", "8"), ("final", "32")])
@pytest.mark.parametrize("length,clamped", [(1, "8"), (240, "240"), (999, "360")])
def test_argv_contract(pack, quality, steps, length, clamped):
    job = P.make_job({"mode": "music", "music_instrumental": "on", "music_lyrics": "not sent",
                      "music_style": "warm piano", "music_quality": quality, "music_max_seconds": str(length),
                      "music_mode": "off", "music_seed": "41"})
    out = P.OUTPUT / "song.wav"
    argv = P.music_argv(job, P.music_paths(), out)
    val = lambda flag: argv[argv.index(flag) + 1]
    assert "--instrumental" in argv and val("--lyrics") == ""
    assert val("--mode") == "off" and val("--steps") == steps
    assert val("--max-seconds") == clamped and val("--seed") == "41"
    assert val("--precision") == "8bit" and "--duration" not in argv
    assert val("--sidecar") == str(out) + ".json"
    extra = json.loads(val("--extra-json"))
    assert extra["job_id"] == job["id"] and extra["engine"] == "music"
    assert extra["engine_pin"] == (ROOT / "scripts/music/engine_pin.txt").read_text().strip()
    assert extra["pack_sources"] == F.SOURCES


def test_progress_protocol():
    prev = None
    for line, pct, label in (("[music] stage loading", 2, "Loading YuE2"),
                              ("[music] stage planning", 5, "Writing score"),
                              ("[music] plan 37", 5, "37 tokens"),
                              ("[music] song 50/100", 37.5, "Writing song"),
                              ("[music] synth 4/8", 75, "Synthesizing music"),
                              ("[music] decode 1/2", 94, "Decoding audio"),
                              ("[music] done 48.25 /tmp/track.wav", 100, "Song ready")):
        prev = P.music_progress(line, prev)
        assert prev["pct"] == pct and label in prev["phase_label"]
    assert P.music_progress("arbitrary stdout") is None
    assert P.music_progress("[music] synth 1/0") is None
    assert P.music_progress("[music] error memory guard")['phase_label'] == 'memory guard'
    assert P.music_progress("[music] stage stopping", prev)["pct"] == 100


@pytest.mark.parametrize("ext,mime", [("wav", "audio/wav"), ("mp4", "video/mp4"), ("png", "image/png")])
def test_file_mime_and_range(http_server, ext, mime):
    p = P.OUTPUT / f"sample.{ext}"
    p.write_bytes(b"0123456789")
    query = "/file?" + urlencode({"path": str(p)})
    code, hdr, body = http_server("GET", query)
    assert (code, hdr["Content-Type"], body) == (200, mime, b"0123456789")
    code, hdr, body = http_server("GET", query, headers={"Range": "bytes=2-5"})
    assert (code, hdr["Content-Type"], body) == (206, mime, b"2345")
    assert hdr["Content-Range"] == "bytes 2-5/10"


def test_wav_outputs_sidecar_and_trash(http_server, monkeypatch, tmp_path):
    p = P.OUTPUT / "music_song.wav"
    p.write_bytes(b"fixture")
    sidecar = Path(str(p) + ".json")
    meta = {"engine": "music", "model": "YuE2-3B", "audio_seconds": 123.4, "wall_seconds": 98,
            "style": "warm piano", "lyrics": "[Verse]\nHello", "score_abc": "X:1\nK:C\nCDEF",
            "ended_naturally": True, "seed": 17}
    sidecar.write_text(json.dumps(meta))
    os.utime(p, (time.time() - 5, time.time() - 5))
    row = next(o for o in P.list_outputs() if o["path"] == str(p))
    assert row["kind"] == "audio" and row["clip_sec"] == 123.4 and row["elapsed_sec"] == 98
    assert row["url"].startswith("/file?")
    code, _, body = http_server("GET", "/sidecar?" + urlencode({"path": str(p)}))
    assert code == 200 and json.loads(body) == meta
    # Redirect Trash to this fixture; never touch the actual Trash or outputs.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    code, _, body = http_server("POST", "/output/delete", urlencode({"path": str(p)}), {"Content-Type": "application/x-www-form-urlencoded"})
    assert code == 200, body
    assert not p.exists() and not sidecar.exists()
    assert (tmp_path / "home/.Trash" / p.name).exists()
    assert (tmp_path / "home/.Trash" / sidecar.name).exists()


def test_fetch_check_and_strict_tree(pack, capsys):
    models, _ = pack
    assert F.main(["--models", str(models), "--check"]) == 0
    f = models / "generator/config.json"
    f.write_bytes(b"x" * f.stat().st_size)
    assert F.main(["--models", str(models), "--check"]) == 1
    assert "sha256 mismatch" in capsys.readouterr().out
    (models / "generator/.cache").mkdir()
    assert any(".cache: unexpected entry" in e for e in F.pack_problems(models))
    result = subprocess.run([sys.executable, str(ROOT / "scripts/pinokio/music_fetch.py"), "--models", str(models), "--check"], capture_output=True, text=True)
    assert result.returncode == 1 and "sha256 mismatch" in result.stdout


def test_fetch_sources_are_the_pinned_public_repos():
    sources = F.choose_sources()
    assert sources == F.SOURCES
    assert sources["generator"] == {"repo": "vanch007/mlx-Yue2-3B",
                                    "revision": "fa66d203dd56d7e033e05ee8b32269768984c4cc", "prefix": ""}
    assert sources["vae"] == {"repo": "m-a-p/YuE2-Vae",
                              "revision": "95535e72a97bc0f09b8ada125d26b4009428c0e8", "prefix": ""}
    assert not hasattr(F, "MIRROR_REPO")


def test_fetch_stages_moves_and_skips_intact(pack, monkeypatch):
    models, _ = pack
    # Force one broken file and a prior direct-download cache; the fake hub
    # serves only fixture bytes, and a healthy second pass makes zero calls.
    originals = {str(p.relative_to(models)): p.read_bytes() for d in (models / "generator", models / "vae") for p in d.rglob("*") if p.is_file()}
    (models / "generator/ar-8bit.safetensors").unlink()
    (models / "generator/.cache").mkdir()
    calls = []
    def download(repo_id, revision, filename, local_dir, force_download):
        part = "generator" if repo_id == F.SOURCES["generator"]["repo"] else "vae"
        assert ".staging" in str(local_dir) and filename != "README.md"
        calls.append((part, filename))
        p = Path(local_dir) / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        (Path(local_dir) / ".cache").mkdir(exist_ok=True)
        p.write_bytes(originals[part + "/" + filename])
        return str(p)
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    monkeypatch.setattr(F, "choose_sources", lambda api: F.SOURCES)
    monkeypatch.setattr(huggingface_hub.HfApi, "list_repo_files", lambda *a, **kw: ["licenses/notice.txt"])
    F.fetch_pack(models, 0)
    assert not (models / "generator/.cache").exists()
    assert ("generator", "ar-bf16.safetensors") not in calls
    assert not F.pack_problems(models, hashes=True)
    calls.clear()
    F.fetch_pack(models, 14)
    assert calls == []


def test_runner_help_and_empty_input():
    music_python = Path.home() / "AI/projects/yue2-mlx/.venv/bin/python"
    runner = ROOT / "scripts/music/yue2_run.py"
    result = subprocess.run([sys.executable, str(runner), "--model-dir", "/missing", "--vae-dir", "/missing", "--output", "/tmp/no-song.wav"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and "Give it something" in result.stdout
    assert "stage loading" not in result.stdout
    if not music_python.exists():
        pytest.skip("music venv unavailable for --help")
    result = subprocess.run([str(music_python), str(runner), "--help"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and "--max-seconds" in result.stdout and "--instrumental" in result.stdout


def test_invalid_pack_metadata_is_unavailable(pack):
    (P.MUSIC_MODELS / "generator/conversion.json").write_text("[]")
    assert not P.music_status()["available"]
    assert P.music_status()["reason"] == "missing_weights"


def test_dispatch_stub_process_and_guard_cleanup(pack, monkeypatch, tmp_path):
    stub = tmp_path / "stub_runner.py"
    stub.write_text('''import sys,json,pathlib
args=sys.argv
out=pathlib.Path(args[args.index('--output')+1])
print('[music] stage loading',flush=True)
print('[music] synth 8/8',flush=True)
out.write_bytes(b'fixture WAV')
pathlib.Path(str(out)+'.json').write_text(args[args.index('--extra-json')+1])
print('[music] done 5 '+str(out),flush=True)
''')
    monkeypatch.setattr(P, "MUSIC_RUNNER", stub)
    killed = []
    monkeypatch.setattr(P.HELPER, "kill", lambda: killed.append(True))
    job = P.make_job({"mode": "music", "music_style": "test"})
    P.STATE["current"] = job
    P.run_job_inner(job)
    assert killed and Path(job["output_path"]).is_file()
    assert job["progress"]["pct"] == 100
    assert P.STATE["music_pgid"] is None
    assert not P._PROC_GUARDS["music"][0].exists()
    # The panel never creates a shared lock.
    assert not P.MUSIC_GPU_FILE_LOCK.exists() and not P.MUSIC_GPU_DIR_LOCK.exists()


def test_stop_escalates_only_the_owned_group(pack, monkeypatch):
    signals = []
    monkeypatch.setattr(P.os, "killpg", lambda pg, sig: signals.append((pg, sig)))
    P.STATE["music_pgid"] = 987654
    P._stop_music_proc(grace=0.01)
    deadline = time.monotonic() + 1
    while len(signals) < 2 and time.monotonic() < deadline:
        time.sleep(.01)
    assert signals == [(987654, signal.SIGTERM), (987654, signal.SIGKILL)]
    signals.clear()
    P._stop_music_proc(grace=0.05)
    P.STATE["music_pgid"] = None
    time.sleep(.1)
    assert signals == [(987654, signal.SIGTERM)]


def node_eval(source):
    result = subprocess.run(["node", "-e", source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_video_switcher_byte_identical_and_music_surface():
    current = (ROOT / "webapp/js/engines.js").read_text()
    before = subprocess.check_output(["git", "show", "d616662:webapp/js/engines.js"], cwd=ROOT, text=True)
    names = ("engineStatus", "engineServesMode", "_currentSurface", "engineOnSurface", "engineRenderable", "_engineRowVisible", "_engineTooltip", "renderEngineSwitch")
    def render(source, engines, workflow, capable=True):
        funcs = '\n'.join(extract_function(n, source) for n in names)
        return node_eval('''const box={innerHTML:'',hidden:false,querySelector:()=>null};
const divider={hidden:false}; const menu={innerHTML:'',querySelectorAll:()=>[]};
const document={body:{dataset:{workflow:WORKFLOW}},getElementById:id=>id==='engineSwitch'?box:divider};
const window={_ENGINE_PROBES:{h3:{capable:CAPABLE,available:true},music:{capable:true,available:true}}};
const ENGINES=REGISTRY; const currentMode='t2v';
const currentEngine=()=> 'ltx'; const musicComposeActive=()=>true;
const escapeHtml=s=>String(s||''); const _engineMenuEl=()=>menu; const closeEngineMenu=()=>{};
'''.replace('WORKFLOW', json.dumps(workflow)).replace('CAPABLE', json.dumps(capable)).replace('REGISTRY', json.dumps(engines)) + funcs + '\nrenderEngineSwitch();console.log(JSON.stringify({box,divider,menu}));')
    registry = P.engines_payload()
    old_registry = [e for e in registry if e["id"] != "music"]
    for capable in (False, True):
        assert render(before, old_registry, "manual", capable) == render(current, registry, "manual", capable)
    music = render(current, registry, "audio")
    assert music["box"]["hidden"] is False and "YuE2" in music["box"]["innerHTML"]
    assert "Hailuo H3" not in music["menu"]["innerHTML"]


def test_compose_payload_and_chain_do_not_submit():
    src = (ROOT / "webapp/js/characters.js").read_text()
    result = node_eval('''let submits=0,mode='',workflow='';
const els=Object.fromEntries(Object.entries({musicLyrics:'kept lyrics',musicStyle:'warm pop',musicMode:'full',musicMaxSeconds:'240',musicSeed:'-1',musicQuality:'final'}).map(([k,value])=>[k,{value}]));
els.musicInstrumental={checked:true};
const document={getElementById:id=>els[id]}; const AUDIO_STUDIO={};
const findOutputByPath=()=>({clip_sec:120}); const audioModeSet=m=>{mode=m};
const workflowSwitch=w=>{workflow=w};const audioStudioRenderSlots=()=>{}; const phosToast=()=>{};
const fetch=()=>{submits++};
''' + extract_function("musicFormParams", src) + extract_function("useTrackInA2V", src) + '''
const payload=Object.fromEntries(musicFormParams());useTrackInA2V('/tmp/song.wav');
console.log(JSON.stringify({payload,lyrics:els.musicLyrics.value,AUDIO_STUDIO,submits,mode,workflow}));''')
    assert result["payload"]["music_instrumental"] == "on" and result["payload"]["music_lyrics"] == ""
    assert result["lyrics"] == "kept lyrics"
    assert result["mode"] == "drive" and result["workflow"] == "audio" and result["submits"] == 0
    assert result["AUDIO_STUDIO"]["audioPath"] == "/tmp/song.wav"


def test_compose_visibility_tracks_install_state():
    src = (ROOT / "webapp/js/characters.js").read_text()
    result = node_eval('''let audioModeChoice=null,musicBusy=false;
const window={_ENGINE_PROBES:{music:{available:false,capable:true}}};
const els=Object.fromEntries(['audioModeGroup','musicComposePane','audioDrivePane','musicInstrumental','musicLyrics','musicInstrumentalPill','musicMaxSeconds','musicMaxSecondsVal','musicQuality','musicEstimate','musicGenBtn'].map(id=>[id,{hidden:false,classList:{toggle:()=>{}}}]));
els.musicInstrumental.checked=false;els.musicMaxSeconds.value='240';els.musicQuality.value='final';
const document={body:{dataset:{workflow:'audio'}},getElementById:id=>els[id],querySelectorAll:()=>[]};
const renderEngineSwitch=()=>{};
''' + extract_function("updateMusicAvailability", src) + extract_function("musicFormChanged", src) + '''
const states=[];
for(const available of [false,true,false]) {
 updateMusicAvailability({music:{available,capable:true,estimates:{}}});
 states.push({strip:els.audioModeGroup.hidden,compose:els.musicComposePane.hidden,drive:els.audioDrivePane.hidden});
}
console.log(JSON.stringify(states));''')
    assert result == [{"strip": True, "compose": True, "drive": False},
                      {"strip": False, "compose": False, "drive": True},
                      {"strip": True, "compose": True, "drive": False}]


# ---- review fixes (2026-09-17) ------------------------------------------------

def test_progress_eta_is_total_and_remaining_is_separate():
    src = Path(P.__file__).read_text()
    body = src[src.index("def run_music_job_inner"):src.index("ENGINE_DEFAULT = ")]
    assert 'eta_sec=music_estimate(p["music_quality"], p["music_max_seconds"])["eta_sec"],' in body
    assert "remaining_sec=max(0," in body


def test_runner_reports_decode_and_never_publishes_after_stop():
    src = (ROOT / "scripts/music/yue2_run.py").read_text()
    assert "_vae.OobleckDecoder.decode = decode_with_progress" in src
    assert 'say("decode"' in src
    call = src.index("result = pipe(**kw)")
    assert src.index("if stop.is_set():", call) < src.index("sf.write(", call)


def test_music_sync_makes_the_checkout_absolute(tmp_path):
    script = ROOT / "scripts/pinokio/music_sync.sh"
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    (fakebin / "uv").write_text('#!/bin/sh\necho "ENV=$UV_PROJECT_ENVIRONMENT"\necho "ARGS=$*"\n')
    (fakebin / "uv").chmod(0o755)
    (tmp_path / "engines/yue2").mkdir(parents=True)
    out = subprocess.run(["bash", str(script), "engines/yue2"], cwd=tmp_path, capture_output=True, text=True,
                         env={**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}"}, timeout=30)
    assert out.returncode == 0, out.stderr
    real = (tmp_path / "engines/yue2").resolve()
    assert f"ENV={real}/.venv" in out.stdout and f"--project {real}" in out.stdout
    assert "music_sync.sh" in (ROOT / "install_music.js").read_text()
    assert "music_sync.sh" in (ROOT / "scripts/post_update.sh").read_text()


def test_install_music_is_reachable_while_the_panel_runs():
    probe = (
        "const path=require('path'),fs=require('fs');const root=process.argv[1];"
        "const mod=require(path.join(root,'pinokio.js'));"
        "const info={path:(...a)=>path.join(root,...a),exists:p=>fs.existsSync(path.join(root,p)),"
        "running:s=>s==='start.js',local:s=>s==='start.js'?{url:'http://127.0.0.1:1'}:{}};"
        "mod.menu({},info).then(m=>console.log(JSON.stringify(m.map(i=>i.text))))"
    )
    out = subprocess.run(["node", "-e", probe, str(ROOT)], capture_output=True, text=True, timeout=30)
    if out.returncode != 0 or not out.stdout.strip():
        pytest.skip("pinokio.js menu not evaluable here: " + out.stderr[-200:])
    items = json.loads(out.stdout)
    if "Open Panel" not in items:
        pytest.skip("this checkout is not in the installed/running menu state")
    assert any("music engine" in t for t in items) or P.SYSTEM_RAM_GB < 24


def test_music_estimate_is_calibrated_and_scaled(monkeypatch):
    monkeypatch.setenv("PHOSPHENE_SPEED_FACTOR", "1")
    final = P.music_estimate("final", 180)
    # 177.8 s of audio took 180.2 s on the M4 Max.
    assert 170 <= final["eta_sec"] <= 190
    assert 60 <= P.music_estimate("draft", 120)["eta_sec"] <= 68
    assert final["eta_measured"] is False  # a fit, never a measurement
    monkeypatch.setenv("PHOSPHENE_SPEED_FACTOR", "2")
    assert abs(P.music_estimate("final", 180)["eta_sec"] - 2 * final["eta_sec"]) <= 1


def test_external_gpu_lock_is_read_never_written(pack, monkeypatch, tmp_path):
    stub = tmp_path / "never_runs.py"
    stub.write_text("raise SystemExit('the runner must not start while a lock exists')\n")
    monkeypatch.setattr(P, "MUSIC_RUNNER", stub)
    monkeypatch.setattr(P.HELPER, "kill", lambda: None)
    for lock, make in ((P.MUSIC_GPU_FILE_LOCK, lambda p: p.write_text("lab job")),
                       (P.MUSIC_GPU_DIR_LOCK, lambda p: p.mkdir())):
        make(lock)
        job = P.make_job({"mode": "music", "music_style": "test"})
        with pytest.raises(P.RenderRefused, match=lock.name):
            P.run_job_inner(job)
        assert lock.exists()  # left exactly as found
        if lock.is_dir():
            lock.rmdir()
        else:
            lock.unlink()
    src = Path(P.__file__).read_text()
    body = src[src.index("def run_music_job_inner"):src.index("ENGINE_DEFAULT = ")]
    assert "MUSIC_GPU_DIR_LOCK.mkdir" not in src and "MUSIC_GPU_FILE_LOCK.open" not in src
    assert "_music_take_gpu_locks" not in src and "_music_release_gpu_locks" not in src


def test_late_stop_leaves_nothing_behind(pack, monkeypatch, tmp_path):
    """The runner publishes a song, and Stop lands while it does."""
    import threading
    ran = tmp_path / "ran"
    stub = tmp_path / "late_runner.py"
    stub.write_text(f'''import sys,pathlib,time
args=sys.argv
out=pathlib.Path(args[args.index('--output')+1])
out.with_name(out.stem+'.partial'+out.suffix).write_bytes(b'partial')
out.write_bytes(b'late WAV')
pathlib.Path(str(out)+'.json').write_text('{{}}')
pathlib.Path({str(ran)!r}).write_text(str(out))
time.sleep(1.5)
print('[music] done 1 '+str(out),flush=True)
''')
    monkeypatch.setattr(P, "MUSIC_RUNNER", stub)
    monkeypatch.setattr(P.HELPER, "kill", lambda: None)
    # Another song already in the gallery must survive the cleanup.
    keep = P.OUTPUT / "music_20260101_000000_other.wav"
    keep.write_bytes(b"other song")
    job = P.make_job({"mode": "music", "music_style": "test"})
    P.STATE["current"] = job

    def stop_after_publish():
        for _ in range(200):
            if ran.exists():
                job["cancel_requested"] = True
                return
            time.sleep(0.02)

    t = threading.Thread(target=stop_after_publish)
    t.start()
    with pytest.raises(P.JobStopped):
        P.run_job_inner(job)
    t.join()
    published = Path(ran.read_text())
    assert published.parent == P.OUTPUT and published != keep
    assert not published.exists()
    assert not Path(str(published) + ".json").exists()
    assert not published.with_name(published.stem + ".partial.wav").exists()
    assert keep.read_bytes() == b"other song"


def test_stop_targets_the_pgid_read_with_the_cancel_flag(pack, monkeypatch):
    hit = []
    monkeypatch.setattr(P.os, "killpg", lambda pg, sig: hit.append(pg))
    monkeypatch.setattr(P.HELPER, "kill", lambda: None)
    job = {"id": "a", "params": {"engine": "music"}}
    P.STATE["current"] = job
    P.STATE["music_pgid"] = 111

    real = P._stop_music_proc

    def advanced(*a, **kw):
        # The worker moves on to the next song between the lock and the signal.
        P.STATE["music_pgid"] = 222
        return real(*a, **kw)

    monkeypatch.setattr(P, "_stop_music_proc", advanced)
    try:
        P.stop_current_job(timeout=0.01)
    except Exception:
        pass
    assert hit and hit[0] == 111 and 222 not in hit
    P.STATE["music_pgid"] = None
    P.STATE["current"] = None


def test_completed_staged_file_is_promoted_not_refetched(pack, monkeypatch):
    models = P.MUSIC_MODELS
    name = "ar-bf16.safetensors"
    stage = models / ".staging/generator"
    stage.mkdir(parents=True)
    os.replace(models / "generator" / name, stage / name)
    (models / "pack_source.json").unlink()
    fetched = []

    def fake_download(repo_id, revision, filename, local_dir, force_download=False):
        fetched.append(filename)
        src = models / ("generator" if "generator" in str(local_dir) else "vae") / filename
        dest = Path(local_dir) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return str(dest)

    class API:
        def list_repo_files(self, repo, revision):
            return ["licenses/notice.txt"]

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: API())
    monkeypatch.setattr(F.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    monkeypatch.setattr(F, "PACK_BYTES", 10**12)
    F.fetch_pack(models, 0)
    assert name not in fetched
    assert F.pack_problems(models, hashes=True) == []


def test_runner_checks_stop_right_before_publishing():
    src = (ROOT / "scripts/music/yue2_run.py").read_text()
    write = src.index("sf.write(tmp")
    assert src.index("if stop.is_set():", write) < src.index("os.replace(tmp, out)", write)


def test_fetch_clears_abandoned_temporaries_and_does_not_credit_them():
    src = (ROOT / "scripts/pinokio/music_fetch.py").read_text()
    assert 'stage.rglob("*.incomplete")' in src and "leftover.unlink()" in src
    assert "partial_bytes" not in src
