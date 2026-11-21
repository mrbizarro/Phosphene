"""Voices library routes. Files stay under the active panel's personal state."""
from pathlib import Path
import tempfile
import hashlib
import shutil
import json
import re
import subprocess

from panel.routes import get, get_when, post
from voices import MAX_UPLOAD_BYTES, VoiceLibrary

P = None


def library():
    return VoiceLibrary(P.STATE_DIR)


@get("/voices")
def get_voices(h, parsed):
    lib = library()
    records = []
    for record in lib.list():
        # Existing voices get their speaker description the first time they are listed.
        try:
            record = lib.identity(record["id"])
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
        records.append(record)
    with P.QUEUE_COND:
        current = P.STATE.get("current") or {}
        jobs = [*P.STATE["queue"], current]
        busy = {j.get("params", {}).get("voice_id"): j.get("params", {}).get("mode")
                for j in jobs if j.get("params", {}).get("mode") in ("voice_prepare", "voice_train")}
        # A voice job behind another render is waiting, not working: say so.
        running = (current.get("params") or {}).get("voice_id") if current else None
        others_running = bool(current) and not running
    for record in records:
        record["busy"] = busy.get(record["id"], "")
        record["waiting"] = bool(record["busy"]) and record["id"] != running and others_running
        if record["busy"] and record.get("training") == "training":
            record["busy"] = "voice_train"
            try:
                record["progress"] = json.loads((lib.folder(record["id"]) / "progress.json").read_text())
            except (OSError, ValueError):
                record["progress"] = {}
    from voices import resolve_render_voice
    characters = P.list_library_characters()
    by_id = {r["id"]: r for r in records}
    character_voices = []
    for character in characters:
        try:
            voice = resolve_render_voice(lib, {'voice_id': 'character:' + character['id']}, characters)
        except ValueError:
            continue
        if not voice or not Path(voice.get('adapter_path') or '').is_file():
            continue
        name = character.get('name') or character['id']
        # One voice, one entry: a character that uses a library voice (or whose
        # trained voice was imported into the library) is that library voice.
        same = by_id.get(voice.get('id')) or next(
            (r for r in records if r.get('training') == 'ready'
             and (r.get('from_character') == character['id']
                  or r.get('trigger') in {character['id'], character.get('trigger')})), None)
        if same:
            same.setdefault('characters', []).append(name)
            continue
        character_voices.append({'id': 'character:' + character['id'], 'name': name})
    h._json({"voices": records, "assignments": lib.assignments(), "character_voices": character_voices})


@get_when(lambda p: p.startswith("/voices/") and p.endswith("/sample"))
def get_voice_sample(h, parsed):
    try:
        sample = library().sample(parsed.path[len("/voices/"):-len("/sample")])
        h._ok(sample.read_bytes(), "audio/wav")
    except (ValueError, OSError):
        h.send_error(404)


@post("/voices/upload")
def post_voice_upload(h, path, qs, ctype):
    try:
        length = int(h.headers.get("Content-Length") or 0)
        if not ctype.startswith("multipart/form-data") or length <= 0:
            h._json({"error": "Choose an audio or video clip"}, 400); return
        if length > MAX_UPLOAD_BYTES:
            h._json({"error": "Choose a clip smaller than 256 MB"}, 413); return
        form = P._parse_multipart_form(h.rfile, ctype, length)
        if "file" not in form or not getattr(form["file"], "file", None):
            h._json({"error": "Choose a clip"}, 400); return
        with tempfile.TemporaryDirectory(prefix="voice-import-") as tmp:
            source = Path(tmp) / "clip"
            with source.open("wb") as out:
                remaining = MAX_UPLOAD_BYTES
                while chunk := form["file"].file.read(min(1 << 20, remaining + 1)):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError("Choose a clip smaller than 256 MB")
                    out.write(chunk)
            record = library().import_clip(source, form.getvalue("name") or "")
            # Re-adding an existing trained sample reuses its proven adapter.
            # Match bytes, never a filename or a display name (both are editable).
            fingerprint = hashlib.sha256(source.read_bytes()).digest()
            for character in P.list_library_characters():
                sample = Path(character.get("voice_sample") or "")
                adapter = Path(character.get("audio_lora_path") or "")
                if (sample.is_file() and adapter.is_file()
                        and sample.stat().st_size == source.stat().st_size
                        and hashlib.sha256(sample.read_bytes()).digest() == fingerprint):
                    target = library().folder(record["id"]) / "voice.audio.safetensors"
                    partial = target.with_suffix(".partial")
                    shutil.copyfile(adapter, partial)
                    partial.replace(target)
                    sidecar = adapter.with_suffix(".json")
                    if sidecar.is_file():
                        shutil.copyfile(sidecar, target.with_suffix(".json"))
                    record["training"] = "ready"
                    record["trigger"] = character.get("trigger") or character["id"]
                    record["from_character"] = character["id"]
                    library().save(record)
                    break

        job = P.make_job({"mode": "voice_prepare", "voice_id": record["id"]})
        with P.QUEUE_COND:
            P.STATE["queue"].append(job)
            P.QUEUE_COND.notify_all()
        P.persist_queue()
        h._json({"voice": record, "queued_id": job["id"]}, 201)
    except (ValueError, OSError) as exc:
        h._json({"error": str(exc)}, 400)
    except Exception:
        h._json({"error": "Could not read this clip. Try another audio or video file."}, 400)


@post("/voices/assign")
def post_voice_assign(h, path, qs, ctype):
    read = h._read_form_body()
    if read is None:
        return
    _, form = read
    try:
        character = (form.get("character_id") or [""])[0]
        if character not in {c["id"] for c in P.list_library_characters()}:
            raise ValueError("Character not found")
        library().assign(character, (form.get("voice_id") or [""])[0])
        h._json({"ok": True})
    except ValueError as exc:
        h._json({"error": str(exc)}, 400)


@post("/voices/train")
def post_voice_train(h, path, qs, ctype):
    read = h._read_form_body()
    if read is None:
        return
    _, form = read
    try:
        form["mode"] = ["voice_train"]
        job = P.make_job(form)
        with P.QUEUE_COND:
            if any(j.get("params", {}).get("voice_id") == job["params"]["voice_id"]
                   and j.get("params", {}).get("mode") in ("voice_prepare", "voice_train")
                   for j in [*P.STATE["queue"], P.STATE.get("current") or {}]):
                h._json({"error": "This voice is already waiting to train"}, 409); return
            P.STATE["queue"].append(job)
            P.QUEUE_COND.notify_all()
        P.persist_queue()
        h._json({"ok": True, "queued_id": job["id"]}, 202)
    except ValueError as exc:
        h._json({"error": str(exc)}, 400)


@post("/voices/speaker")
def post_voice_speaker(h, path, qs, ctype):
    """Who is speaking, in the creator's words. Re-voice names this person in
    every take; empty goes back to the description made from the sample."""
    read = h._read_form_body()
    if read is None:
        return
    _, form = read
    try:
        record = library().set_speaker((form.get("voice_id") or [""])[0], (form.get("speaker") or [""])[0])
        h._json({"ok": True, "speaker": record.get("speaker") or "", "speaker_auto": record.get("speaker_auto") or ""})
    except (ValueError, OSError) as exc:
        h._json({"error": str(exc)}, 400)


@post("/voices/delete")
def post_voice_delete(h, path, qs, ctype):
    read = h._read_form_body()
    if read is None:
        return
    _, form = read
    voice_id = (form.get("voice_id") or [""])[0]
    try:
        with P.QUEUE_COND:
            if any((j.get("params") or {}).get("voice_id") == voice_id
                   for j in [*P.STATE["queue"], P.STATE.get("current") or {}]):
                h._json({"error": "A job in the queue uses this voice. Wait for it to finish, or stop it first."}, 409); return
        library().remove(voice_id)
        h._json({"ok": True})
    except (ValueError, OSError) as exc:
        h._json({"error": str(exc)}, 400)


@post('/voices/revoice')
def post_revoice(h, path, qs, ctype):
    read = h._read_form_body()
    if read is None:
        return
    _, form = read
    # Re-voice is part of Pro and runs inside the Pro add-on (P.revoice_addon). Free sees the Pro
    # window; a member whose add-on is still arriving is told so, never shown the buy window.
    addon = P.revoice_addon()
    if addon is None:
        if not P.pro_is_active():
            h._json({'error': P.REVOICE_PRO_ONLY, 'pro_required': True}, 403); return
        h._json({'error': 'Getting Pro ready on this Studio. Re-voice works once that is done.',
                 'pro_setup': P.pro_addon_get_ready()}, 503); return
    try:
        form['mode'] = ['revoice' if form.get('session') else 'revoice_detect']
        if not form.get('session'):
            # The same clip, unchanged since its lines were found: reuse them
            # instead of waiting in the queue to transcribe it again.
            found = addon.find_session(P, (form.get('path') or [''])[0])
            if found:
                h._json({'queued_id': found}, 202); return
        job = P.make_job(form)
        refusal = P.base_models_refusal(job['params'])
        if refusal:
            h._json(refusal, 400); return
        with P.QUEUE_COND:
            pending = [*P.STATE['queue'], P.STATE.get('current') or {}]
            duplicate = next((j for j in pending if j.get('params') == job['params']), None)
            if duplicate:
                h._json({'queued_id': duplicate['id']}, 202); return
            P.STATE['queue'].append(job)
            P.QUEUE_COND.notify_all()
        P.persist_queue()
        h._json({'queued_id': job['id']}, 202)
    except (ValueError, OSError) as exc:
        h._json({'error': str(exc)}, 400)


def revoice_session_file(ident):
    """Where the Pro add-on saves the lines it found for job `ident` (the same place and id rule as
    the add-on's session_path), so the dialog can follow its own job."""
    if not re.fullmatch(r'[\w-]{1,100}', ident or ''):
        raise ValueError('Invalid Re-voice session')
    return P.STATE_DIR / 'revoice' / (ident + '.json')


@get('/voices/revoice/status')
def get_revoice_status(h, parsed):
    ident = P.parse_qs(parsed.query).get('id', [''])[0]
    try:
        target = revoice_session_file(ident)
        with P.QUEUE_COND:
            jobs = [P.STATE.get('current') or {}, *P.STATE['queue'], *P.STATE['history']]
            job = next((j for j in jobs if j.get('id') == ident), None)
            result = {k: job.get(k) for k in ('status', 'error', 'output_path', 'revoice_progress')} if job else {}
            if job and job.get('status') == 'queued':
                # How many renders go first, so the dialog can say why it waits.
                queue = P.STATE['queue']
                position = next((i for i, j in enumerate(queue) if j.get('id') == ident), len(queue))
                result['ahead'] = position + (1 if P.STATE.get('current') else 0)
        if target.is_file():
            result.setdefault('status', 'done')
            result['session'] = ident
            result['phrases'] = json.loads(target.read_text())['phrases']
        if not result:
            h._json({'error': 'Re-voice job not found'}, 404); return
        h._json(result)
    except (ValueError, OSError) as exc:
        h._json({'error': str(exc)}, 400)
