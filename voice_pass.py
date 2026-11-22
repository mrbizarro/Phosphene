"""Phrase-level trained voice replacement: copied picture, original residual bed.

Speaker tags use `character_id: <d>[English] words</d>`. Untagged lines use
 the shot's selected voice; tagged characters without a voice retain their audio.
"""
from pathlib import Path
import json
import math
import re
import subprocess
import unicodedata

SR = 44100


_QUOTED = re.compile(r'["\u201c]([^"\u201d\n]{1,300})["\u201d]')
_LTX_AUDIO_CUE = "\nAudio: clear natural spoken dialogue in a quiet room, no music."
_SAYS_LINE = re.compile(r'^(\s*)([^\s:\n<>"\u201c\u201d][^\n:<>"\u201c\u201d]{0,60}?)\s+says(?:\s+clearly)?\s*:\s*["\u201c](.+?)["\u201d]\s*$', re.I)
_COLON_LINE = re.compile(r'^(\s*)([^\s:\n<>"\u201c\u201d][^\n:<>"\u201c\u201d]{0,60}?)\s*:\s*(.+?)\s*$')
_NON_SPEAKER_KEYS = frozenset({
    "integrated_multimodal_description", "overall_soundscape", "non_diegetic_music",
    "audio", "sound", "soundscape", "music", "scene", "camera", "lighting", "light",
    "style", "summary", "high_level_description", "style_description",
    "compositional_deconstruction", "note", "notes", "avoid", "negative",
    "negative_prompt", "location", "setting", "action", "shot", "time", "timestamp",
    "sfx", "ambience", "ambient", "voiceover_off", "http", "https",
})


def _norm_key(val: str) -> str:
    return unicodedata.normalize("NFC", str(val or "")).strip().casefold()


def _is_word_or_mark(ch: str) -> bool:
    return ch.isalnum() or unicodedata.category(ch).startswith(("L", "M", "N"))


def _is_speaker_label(who: str, known: dict[str, str]) -> bool:
    s = str(who or "").strip()
    if not s:
        return False
    cf = _norm_key(s)
    if cf in known:
        return True
    if "_" in s or "." in s or cf in _NON_SPEAKER_KEYS:
        return False
    if re.fullmatch(r"(?:shot|beat|window|part|act|scene|frame|take|clip|step)\s*\d+", s, re.I):
        return False
    tokens = s.split()
    if not (1 <= len(tokens) <= 3):
        return False
    if not all(all(_is_word_or_mark(ch) or ch in "'-" for ch in tok) for tok in tokens):
        return False
    first = unicodedata.normalize("NFC", s)[0]
    if not (first.isupper() or (unicodedata.category(first).startswith("L") and not first.islower())):
        return False
    return not any(_norm_key(tok) in _NON_SPEAKER_KEYS for tok in tokens)


def plain_dialogue(prompt, names, default_speaker="", language="English"):
    """Turn lines written in plain words into the H3 dialogue format, so nobody has to type tags.

    `Bizarro: Did you hear that?` (or `Speaker: Hello there.`) becomes
    `Bizarro: <d>[English] Did you hear that?</d>`, while structural `key:` lines like
    `overall_soundscape:` or `Audio:` are left alone. Also accepts LTX-expanded
    `Name says clearly: "words"` lines so replayed recipes round-trip cleanly.
    """
    if not prompt or re.search(r'<d>', prompt, re.I):
        return prompt
    cleaned = prompt[:-len(_LTX_AUDIO_CUE)] if prompt.endswith(_LTX_AUDIO_CUE) else prompt
    known = {_norm_key(n): str(n).strip() for n in names if str(n or "").strip()}
    tag = f"[{language}] " if language else ""
    out, hit = [], False
    for line in cleaned.split("\n"):
        ms = _SAYS_LINE.match(line)
        if ms and _is_speaker_label(ms.group(2), known):
            text = ms.group(3).strip()
            if text:
                out.append(f"{ms.group(1)}{ms.group(2).strip()}: <d>{tag}{text}</d>")
                hit = True
                continue
        m = _COLON_LINE.match(line)
        if m and _is_speaker_label(m.group(2), known):
            text = m.group(3).strip().strip('"\u201c\u201d').strip()
            if text:
                out.append(f"{m.group(1)}{m.group(2).strip()}: <d>{tag}{text}</d>")
                hit = True
                continue
        out.append(line)
    if hit:
        return "\n".join(out)
    if default_speaker:
        # On its own line, so the speaker reads as the line's name, not as the end of the sentence before it.
        quoted, n = _QUOTED.subn(lambda m: f"\n{default_speaker}: <d>{tag}{m.group(1).strip()}</d>\n", prompt)
        if n:
            return quoted
    return prompt


def ltx_plain_dialogue(prompt, names):
    """Turn `Speaker: line` rows into natural LTX spoken prose without `<d>` tags.

    LTX's Gemma text encoder voices quoted speech cleanly when written as
    `Speaker says clearly: "words"` with a clear spoken-dialogue audio cue, allowing
    `voice_pass` to align each speaker's words after the base render.
    """
    if not prompt:
        return prompt
    known = {_norm_key(n): str(n).strip() for n in names if str(n or "").strip()}
    out, hit = [], False
    for line in prompt.split("\n"):
        ms = _SAYS_LINE.match(line)
        if ms and _is_speaker_label(ms.group(2), known):
            out.append(line)
            hit = True
            continue
        m = _COLON_LINE.match(line)
        if m and _is_speaker_label(m.group(2), known):
            who = m.group(2).strip()
            text = m.group(3).strip().strip('"\u201c\u201d').strip()
            if text:
                out.append(f'{m.group(1)}{who} says clearly: "{text}"')
                hit = True
                continue
        out.append(line)
    joined = "\n".join(out)
    if hit and "audio:" not in joined.lower():
        joined = joined.rstrip() + _LTX_AUDIO_CUE
    return joined


def untag_dialogue(prompt: str) -> tuple[str, str]:
    """Convert engine-formatted `<d>[Lang] ...</d>` or `Name says clearly: "..."` back to plain authoring text."""
    if not prompt:
        return "", ""
    s = str(prompt)
    if s.endswith(_LTX_AUDIO_CUE):
        s = s[:-len(_LTX_AUDIO_CUE)]
    lang_found = ""

    def _sub_named_tag(m):
        nonlocal lang_found
        if m.group(3) and not lang_found:
            lang_found = m.group(3).strip()
        txt = " ".join((m.group(4) or "").split())
        return f"{m.group(1)}{m.group(2).strip()}: {txt}"

    s = re.sub(
        r'(^|\n)([ \t]*[^\s:\n<>"\u201c\u201d][^\n:<>"\u201c\u201d]{0,60}?)\s*:\s*<d>\s*(?:\[([^\]]+)\])?\s*(.*?)</d>',
        _sub_named_tag,
        s,
        flags=re.S | re.I,
    )

    def _sub_bare_tag(m):
        nonlocal lang_found
        if m.group(1) and not lang_found:
            lang_found = m.group(1).strip()
        txt = " ".join((m.group(2) or "").split())
        return f'"{txt}"'

    s = re.sub(r'<d>\s*(?:\[([^\]]+)\])?\s*(.*?)</d>', _sub_bare_tag, s, flags=re.S | re.I)
    lines = []
    for line in s.split("\n"):
        ms = _SAYS_LINE.match(line)
        if ms:
            lines.append(f"{ms.group(1)}{ms.group(2).strip()}: {ms.group(3).strip()}")
        else:
            lines.append(line)
    return "\n".join(lines), lang_found


def dialogue_phrases(prompt):
    phrases = []
    previous = 0
    for match in re.finditer(r'<d>\s*(?:\[([^\]]+)\])?\s*(.*?)</d>', prompt, re.S | re.I):
        prefix = prompt[previous:match.start()]
        speaker = re.search(r'(?:^|[\n.!?])\s*([^\s:\n.!?<>"\u201c\u201d][^\n.!?<>"\u201c\u201d:]{0,80}?)\s*:\s*$', prefix, re.I)
        if speaker:
            cand = speaker[1].strip()
            if (re.search(r'\b(says|asks|replies)$', cand, re.I)
                    or not all(_is_word_or_mark(ch) or ch in " '-_." for ch in cand)):
                speaker = None
        text = ' '.join(match[2].split())
        if text:
            phrases.append({'speaker': speaker[1].strip() if speaker else '', 'text': text, 'language': match[1] or ''})
        previous = match.end()
    return phrases


def speech_regions(pcm):
    """Energy boundaries on the separated voice, in 10 ms hops (v5 recipe)."""
    import numpy as np
    mono = np.asarray(pcm).mean(axis=1)
    hop = SR // 100
    energy = np.array([np.sqrt(np.mean(mono[i:i+hop] ** 2)) for i in range(0, len(mono), hop)])
    if not len(energy) or energy.max() < 1e-7:
        raise ValueError('Voice pass found no audible speech')
    active = energy > energy.max() * 10 ** (-35 / 20)
    regions = []; start = None
    for i, value in enumerate([*active, False]):
        if value and start is None:
            start = i * hop / SR
        if not value and start is not None:
            end = i * hop / SR
            if regions and start - regions[-1][1] < .12:
                regions[-1][1] = end
            else:
                regions.append([start, end])
            start = None
    return [r for r in regions if r[1] - r[0] > .05]


class OverlapError(ValueError):
    """Phrase `index` overlaps the one before it (or has no length)."""
    def __init__(self, index, message):
        super().__init__(message)
        self.index = index


def aligned_spans(phrases, heard, pcm=None):
    """Match the complete ordered script, refusing missing/reordered speech."""
    from word_timing import match_all, norm
    words, owners = [], []
    for i, phrase in enumerate(phrases):
        tokens = [norm(w) for w in phrase['text'].split() if norm(w)]
        if not tokens:
            raise ValueError('Voice pass cannot align this dialogue language yet')
        words.extend(tokens); owners.extend([i] * len(tokens))
    indices = match_all(words, [norm(w['w']) for w in heard])
    regions = speech_regions(pcm) if pcm is not None else []
    spans = []
    for i in range(len(phrases)):
        matches = [heard[j] for owner, j in zip(owners, indices) if owner == i and j is not None]
        total = owners.count(i)
        if len(matches) < max(1, total * .8):
            raise ValueError(f'Phrase {i+1} “{phrases[i]["text"]}”: could not match the spoken words. Try another take.')
        start, end = matches[0]['s'], matches[-1]['e']
        # Whisper sometimes gives the pause to the following first word ("good"
        # at 2.02–3.24, whose sound begins at 2.94). Use its word match for
        # identity/order, and the separated waveform for the actual onset.
        if regions:
            first = [r for r in regions if r[1] >= start - .1 and r[0] <= matches[0]['e'] + .1]
            last = [r for r in regions if r[1] >= matches[-1]['s'] - .1 and r[0] <= end + .1]
            def distance(region, point):
                return max(region[0] - point, point - region[1], 0)
            if first:
                onset = min(first, key=lambda r: distance(r, matches[0]['e'] - .06))[0]
                # Speech at sample zero has no leading padding. A negative
                # onset becomes a Python slice from the end and empties it.
                start = max(0, start - .1, onset - .02)
            # A one-word turn can inherit the preceding speaker's pause.
            # Its offset must come from speech after the refined onset.
            last = [r for r in last if r[1] >= start]
            if last:
                offset = min(last, key=lambda r: distance(r, matches[-1]['s'] + .06))[1]
                end = min(end + .4, offset + .02)
        if end <= start or (spans and start < spans[-1][1]):
            raise OverlapError(i, f'Phrase {i+1} “{phrases[i]["text"]}”: voice pass needs separate, non-overlapping spoken phrases')
        spans.append([start, end])
    return spans


def media_duration(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                             '-of', 'json', str(path)], capture_output=True, check=True)
    duration = float(json.loads(result.stdout)['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Voice pass could not read the original clip duration')
    return duration


def read_pcm(path):
    import numpy as np
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-ac', '2',
                          '-ar', str(SR), '-f', 'f32le', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def ramp(n):
    import numpy as np
    result = np.ones(n, np.float32)
    k = min(round(.03 * SR), n // 2)
    if k:
        result[:k] = np.linspace(0, 1, k); result[-k:] = np.linspace(1, 0, k)
    return result


def timing_setup(P, job):
    P.HELPER.kill()
    probe = P.run_tracked_subprocess([str(P.HELPER_PYTHON), '-c', 'import mlx_whisper'],
        pgid_key='train_pgid', label='voice timing setup', job=job, timeout=30)
    if probe.returncode:
        result = P.run_tracked_subprocess(['bash', str(P.ROOT / 'scripts/pinokio/a2v_stems_deps.sh'),
            str(P.HELPER_PYTHON.parent.parent.parent)], pgid_key='train_pgid', label='voice timing setup',
            job=job, env=P._a2v_separator_env(), timeout=1800)
        if result.returncode:
            raise RuntimeError('Voice timing setup did not finish: ' + (result.stdout + result.stderr)[-500:])


def hear_words(P, job, folder, path, name):
    P._raise_if_cancelled(job, 'voice timing')
    result_path = folder / (name + '.json')
    result = P.run_tracked_subprocess([str(P.HELPER_PYTHON), str(P.ROOT / 'scripts/voice_pass_align.py'),
        str(path), str(result_path), str(P.MODELS_DIR / 'voice_alignment')], pgid_key='train_pgid', label='voice timing', job=job,
        env=P.trainer_child_env(), timeout=600)
    if result.returncode:
        raise RuntimeError('Voice timing failed: ' + (result.stderr or result.stdout)[-500:])
    return json.loads(result_path.read_text())


def kept_warning(kept):
    """One plain sentence for the queue and Outputs (the Studio shows its own translation)."""
    lines = ', '.join(str(row['line']) for row in kept)
    label = 'Line' if len(kept) == 1 else 'Lines'
    verb = 'keeps its' if len(kept) == 1 else 'keep their'
    return f'{label} {lines} {verb} original voice: {kept[0]["reason"]}'


def weak_warning(weak):
    """Lines that changed but may look out of step with the mouth, said plainly."""
    lines = ', '.join(str(row['line']) for row in weak)
    label = 'Line' if len(weak) == 1 else 'Lines'
    return f'{label} {lines} may look out of sync: {weak[0]["reasons"][0]}.'


def take_speaker(voice):
    """(who, subject): the person every take of this voice shows, and how the
    speaking clause refers to them. LTX invents the speaker from the prompt and
    the voice comes out as that person's, so it must be named, identically on
    every take and line."""
    who = str(voice.get('speaker') or '').strip() or 'one adult speaker'
    low = who.lower()
    subject = ('The man' if re.match(r'(an? )?(older |young |middle-aged )?man\b', low)
               else 'The woman' if re.match(r'(an? )?(older |young |middle-aged )?woman\b', low)
               else 'The speaker')
    return who, subject


def take_prompt(voice, says):
    """The take prompt: the described person in the scene; the adapter's trigger
    only as the voice's label in the Audio clause (it is trained there, "<id>
    speaking clearly. Audio: a natural talking voice."), never as the person -
    an id names nobody, so each seed drew a new person, sometimes a woman.
    Outside the speaking clause, so short takes do not read it aloud."""
    who, _ = take_speaker(voice)
    return (f'Close-up of {who} in a quiet room, facing the camera. ' + says
            + f'Audio: {voice["trigger"]}, a natural talking voice, clear close speech. No other dialogue, no music.')


def render_take(P, job, folder, p, phrase, span, i, attempt, line=None):
    """One LTX take of this line in the trained voice, saved as phrase-<i>[-takeN].mp4.
    `line` is (text, delivery hint) as the original said it. The engine stays
    loaded between takes; the caller frees it before the takes are listened to."""
    import random
    voice = phrase['voice']
    text, hint = line or (phrase['text'], '')
    # The sub-render goes through the normal LTX job builder/helper and
    # remains inside the parent's serialized GPU slot. No sample conditioning.
    seconds = max(3, min(10, span[1] - span[0] + 1.5))
    seed = p.get('seed', -1) if not attempt else random.randint(0, 2**31 - 1)
    speaker = take_speaker(voice)
    says = (f'{speaker[1]} says only, {hint}: "{text}". ' if hint else f'{speaker[1]} says only, "{text}". ')
    form = {'mode': 't2v', 'engine': 'ltx', 'quality_choice': 'draft', 'quality': 'quick',
            'frames': str(1 + 8 * round(seconds * 24 / 8)), 'upscale': 'off',
            'voice_id': 'default', 'seed': str(seed), 'open_when_done': '0',
            'prompt': take_prompt(voice, says)}
    child = P.make_job(form)
    child['params']['loras'] = [{'path': voice['adapter_path'], 'strength': 1.0}]
    child['params'].update(voice_route='ltx_lora', voice_trigger=voice['trigger'])
    child['started_ts'] = P.time.time(); child['started_at'] = P.iso_now()
    P.run_job_inner(child)
    take = Path(child['output_path'])
    name = f'phrase-{i}' + (f'-take{attempt+1}' if attempt else '')
    saved = folder / f'{name}.mp4'
    P.shutil.move(str(take), saved)
    take.with_suffix(take.suffix + '.json').unlink(missing_ok=True)
    return saved


def hear_take(P, job, folder, phrase, saved):
    """The take's voice, its words and the separated voice file: (pcm, heard, stem). Refuses a take that
    skipped a word or cannot be matched to the line."""
    name = saved.stem
    take_stem, _ = P.a2v_conditioning_audio({'audio_stem_auto': True}, str(saved))
    pcm = read_pcm(take_stem)
    heard = hear_words(P, job, folder, take_stem, name)
    try:
        aligned_spans([phrase], heard, pcm)
    except ValueError as exc:
        raise ValueError(re.sub(r'^Phrase \d+ “.*?”: ', '', str(exc))) from exc
    missing = silent_words(phrase['text'], heard, pcm)
    if missing:
        raise ValueError(f'The new voice skipped the words “{" ".join(missing)}”. Try another take.')
    return pcm, heard, take_stem


def silent_words(text, heard, pcm):
    """Script words that Whisper reports but that have no sound in the take.
    Whisper fills in a word it expects ("what have you been up to" heard over a
    take that only says "have you been up to"); the energy shows it is not there."""
    from word_timing import match_all, norm
    tokens = [norm(w) for w in text.split() if norm(w)]
    try:
        regions = speech_regions(pcm)
    except ValueError:
        return tokens
    indices = match_all(tokens, [norm(w['w']) for w in heard])
    missing = []
    for token, j in zip(tokens, indices):
        if j is None:
            continue
        s, e = float(heard[j]['s']), float(heard[j]['e'])
        if not any(min(e, r[1]) - max(s, r[0]) > .03 for r in regions):
            missing.append(heard[j]['w'].strip())
    return missing


def run(P, job, target=None):
    """The voice pass after a render: every tagged line said again in its character's trained voice,
    placed on the original's words, mixed over the original's own room tone, written over the render.

    `target` lets another job reuse the same pass on a file it chose (it is how the Pro pack's
    Re-voice runs). It carries `source` (the file), optional `stem` / `spans` / `words` already found
    for it, `takes` per line, `audio_only`, `destination(folder)` -> (published path, partial path)
    and `publish(pending, destination)`. Without it the job's own render is the source and the
    result replaces it in place."""
    p = job['params']
    if p.get('voice_route') != 'voice_pass':
        return
    import numpy as np
    source = Path(target.source if target is not None else job.get('output_path') or '')
    if not source.is_file():
        raise RuntimeError('Voice pass has no rendered picture')
    folder = P.STATE_DIR / 'voice_pass' / job['id']; folder.mkdir(parents=True, exist_ok=True)
    phrases = p['voice_phrases']
    timing_setup(P, job)
    stem = target.stem if target is not None else None
    if not stem:
        stem, _ = P.a2v_conditioning_audio({'audio_stem_auto': True}, str(source))
    def hear(path, name):
        return hear_words(P, job, folder, path, name)
    P.push('[voice pass] Aligning original dialogue')
    spans = (target.spans if target is not None else None) or aligned_spans(phrases, hear(stem, 'original'), read_pcm(stem))
    # AAC decodes complete padded frames; their sample count is not the movie
    # clock. Using that count extended a 5.167 s clip by 17 ms after the pass.
    duration = media_duration(source)
    cancelled = getattr(P, 'JobCancelled', None)
    import revoice_sync as rs
    stem_pcm = read_pcm(stem)
    heard = (target.words if target is not None else None) or hear(stem, 'original')
    # Refine the whole clip at once: a line's first word must see the previous line's last.
    rows = [list(row) for row in rs.words_by_phrase(phrases, rs.refine(heard, stem_pcm))]
    fps = rs.frame_rate(source)
    # Each line's loudness against the speaker's usual level: a shouted line is prompted as one.
    line_level = [rs.loudness([stem_pcm[int(a * SR):int(b * SR)]]) for a, b in spans]
    heard_levels = sorted(v for v in line_level if v > -69)
    usual = heard_levels[len(heard_levels) // 2] if heard_levels else -23.0
    chosen = [i for i, phrase in enumerate(phrases) if phrase.get('voice')]
    kept = []          # lines that keep their original voice, and why
    weak = []          # lines that changed but may look out of sync, and why
    def keep_original(i, exc):
        if cancelled and isinstance(exc, cancelled):
            raise exc
        reason = re.sub(r'^(Phrase|Line) \d+ “.*?”: ', '', str(exc)).strip()
        kept.append({'line': i + 1, 'text': phrases[i]['text'], 'reason': reason})
        P.push(f'[voice pass] Line {i+1} keeps its original voice: {reason}')
    lines = {}
    for i in chosen:
        if not rows[i]:
            keep_original(i, ValueError('could not match the spoken words.'))
            continue
        try:
            window = rs.window(i, spans, rows, duration)
        except ValueError as exc:
            keep_original(i, exc)
            continue
        lines[i] = {'window': window, 'line': rs.spoken_line(rows[i], line_level[i] - usual),
                    'files': [], 'heard': 0, 'candidates': [], 'failure': None, 'attempts': 0,
                    'takes': [], 'not_the_voice': 0}
    takes = max(1, min(6, int(target.takes))) if target is not None else 4
    import voice_identity as vi
    try:
        from voices import VoiceLibrary, speaker_text
        _vlib = VoiceLibrary(P.STATE_DIR)
    except Exception:
        _vlib = None
    # Enrich each phrase's voice metadata (speaker description, sample_path, pitch identity)
    # from VoiceLibrary when sidecar/job params carried only id/name/trigger/adapter_path.
    for i in lines:
        voice = phrases[i]['voice']
        vid = str(voice.get('id') or '')
        if _vlib is not None and vid and not vid.startswith('character:'):
            if not voice.get('speaker') or not voice.get('sample_path'):
                try:
                    rec = _vlib.get(vid)
                    rec.update(_vlib.identity(vid))
                    voice.setdefault('sample_path', str(_vlib.sample(vid)))
                    if not voice.get('speaker'):
                        voice['speaker'] = speaker_text(rec)
                    if not voice.get('identity') and rec.get('identity'):
                        voice['identity'] = rec['identity']
                except Exception:
                    pass
    # Each voice's own reference: the pitch of its cleaned sample (and, when the
    # Speech pack is installed, its speaker fingerprint, taken with the first takes).
    references = {}
    for i in lines:
        voice = phrases[i]['voice']
        key = voice.get('id') or voice.get('trigger') or ''
        if key in references:
            continue
        known = voice.get('identity') or {}
        sample = Path(voice.get('sample_path') or '')
        prof = known if known.get('f0_median') else (vi.profile(read_pcm(sample)) if sample.is_file() else None)
        references[key] = {'profile': prof, 'sample': str(sample) if sample.is_file() else '', 'vector': None,
                           'name': voice.get('name') or 'the chosen voice'}

    def reference(i):
        voice = phrases[i]['voice']
        return references[voice.get('id') or voice.get('trigger') or '']

    def render_round(targets, count):
        """Takes for every line while the engine is loaded, then free it for listening.
        `count` is one number for every line or {line: number}."""
        try:
            for n, i in enumerate(targets, 1):
                count_i = count[i] if isinstance(count, dict) else count
                for k in range(count_i):
                    P._raise_if_cancelled(job, 'voice pass')
                    entry = lines[i]
                    job['revoice_progress'] = {'stage': 'rendering', 'phrase': n, 'total': len(targets),
                                               'text': phrases[i]['text'], 'take': k + 1, 'takes': count_i}
                    P.push(f'[voice pass] Line {i+1} ({n}/{len(targets)}), take {k+1} of {count_i}: {entry["line"][0]}'
                           + (f' ({entry["line"][1]})' if entry['line'][1] else ''))
                    try:
                        entry['files'].append(render_take(P, job, folder, p, phrases[i], spans[i], i,
                                                          entry['attempts'], entry['line']))
                    except (ValueError, RuntimeError, OSError) as exc:
                        if cancelled and isinstance(exc, cancelled):
                            raise
                        entry['failure'] = exc
                    entry['attempts'] += 1
        finally:
            P.HELPER.kill()

    def listen(targets):
        job['revoice_progress'] = {'stage': 'listening'}
        heard = []
        for i in targets:
            entry = lines[i]
            for saved in entry['files'][entry['heard']:]:
                P._raise_if_cancelled(job, 'voice pass')
                entry['heard'] += 1
                try:
                    heard.append((i, saved, *hear_take(P, job, folder, phrases[i], saved)))
                except (ValueError, RuntimeError, OSError) as exc:
                    if cancelled and isinstance(exc, cancelled):
                        raise
                    entry['failure'] = exc
                    entry['takes'].append({'file': saved.name, 'used': False, 'reason': str(exc)[:160]})
        # The identity gate: is this take the chosen voice at all? A take that drew
        # someone else (the v9 line 2 drew a woman on 2 of 4) never reaches rhythm scoring.
        fresh = [r['sample'] for r in references.values() if r['sample'] and r['vector'] is None]
        vectors = vi.fingerprints(P, job, fresh + [str(stem) for *_, stem in heard], folder) if heard else {}
        for r in references.values():
            if r['vector'] is None and r['sample'] in vectors:
                r['vector'] = vectors[r['sample']] or False
        for i, saved, pcm, take_words, stem in heard:
            entry = lines[i]; lo, hi = entry['window']; ref = reference(i)
            if ref['profile']:
                identity = vi.check(vi.profile(pcm), dict(ref['profile'], vector=ref['vector'] or None),
                                    vectors.get(str(stem)))
            else:
                identity = {'passed': True, 'method': 'none', 'reason': 'the voice has no sample to compare with'}
            row = {'file': saved.name, 'used': False, 'identity': identity}
            entry['takes'].append(row)
            if not identity['passed']:
                entry['not_the_voice'] += 1
                P.push(f'[voice pass] Line {i+1}, {saved.name}: not {ref["name"]} ({identity["reason"]};'
                       f' {identity.get("f0")} Hz, similarity {identity.get("similarity")})')
                continue
            take_rows = rs.refine(take_words, pcm)
            pairs = rs.pair_words(phrases[i]['text'], rows[i], take_rows)
            if not pairs:
                entry['failure'] = ValueError('could not match the spoken words. Try another take.')
                row['reason'] = 'could not match the spoken words'
                continue
            # Line the take up with the original by sound; word labels are the fallback.
            by_sound = rs.matched_take_words(rows[i], stem_pcm, take_rows, pcm)
            original, take = (rows[i], by_sound) if by_sound else ([o for o, _ in pairs], [t for _, t in pairs])
            score, info = rs.rhythm(original, take, lo, hi)
            score += 2 * (len(rows[i]) - len(pairs))
            info.update(score=score, pcm=pcm, file=saved.name, matched=len(pairs), identity=identity,
                        reasons=rs.gate(info['errors'], info['coverage'], len(pairs), len(rows[i]), fps))
            row['rhythm'] = round(float(score), 3)
            entry['candidates'].append(info)

    def best(i):
        found = lines[i]['candidates']
        return min(found, key=lambda c: c['score']) if found else None

    render_round(list(lines), takes)
    listen(list(lines))
    # More takes, bounded: a line with no take in the chosen voice gets a full new
    # round (twice at most); a line whose best take is out of step gets one half round.
    for extra in range(2):
        again = {i: takes for i in lines if not best(i)}
        if not extra:
            again.update({i: max(1, takes // 2) for i in lines if best(i) and best(i)['reasons']})
        if not again:
            break
        P.push('[voice pass] Another try for line' + ('s ' if len(again) > 1 else ' ')
               + ', '.join(str(i + 1) for i in again) + ': '
               + ('not yet in the chosen voice' if any(not best(i) for i in again) else 'not yet in sync'))
        render_round(list(again), again)
        listen(list(again))
    P._raise_if_cancelled(job, 'voice mix')
    job['revoice_progress'] = {'stage': 'mixing'}
    picked = {}
    for i in lines:
        choice = best(i)
        if choice is None:
            entry = lines[i]
            if entry['not_the_voice']:
                # Never a take that is someone else: the line keeps the original voice, said plainly.
                failure = ValueError(f'none of its {entry["not_the_voice"]} takes sounded like '
                                     f'{reference(i)["name"]}.')
            else:
                failure = entry['failure'] or ValueError('making it in the new voice did not work.')
            keep_original(i, failure)
            kept[-1]['take_checks'] = entry['takes']
            continue
        picked[i] = choice
        next(t for t in lines[i]['takes'] if t['file'] == choice['file'])['used'] = True
        if choice['reasons']:
            weak.append({'line': i + 1, 'text': phrases[i]['text'], 'reasons': choice['reasons']})
    if not picked:
        first = min(kept, key=lambda row: row['line']) if kept else {'line': 0, 'text': '', 'reason': 'No line was changed.'}
        raise ValueError(f'Line {first["line"]} “{first["text"]}”: {first["reason"]}' if first['line']
                         else first['reason'])
    length = round(duration * SR)
    voices = {i: rs.render(c['pcm'], c['segments'], length) for i, c in picked.items()}
    receipt = rs.level_voices(voices, picked, lines, rows, phrases, stem_pcm)
    sample_count = round(duration * SR)
    original_44 = read_pcm(source)[:sample_count]
    if len(original_44) < sample_count:
        original_44 = np.pad(original_44, ((0, sample_count - len(original_44)), (0, 0)))
    delta = rs.voice_delta(voices, {i: lines[i]['window'] for i in picked}, stem_pcm, length)
    for row in receipt:
        i = row['phrase']
        row.update(rs.line_receipt(picked[i], lines[i], rows[i], fps))
        # Every take of the line with its identity check and rhythm score, the chosen one marked.
        row['identity'] = picked[i]['identity']
        row['take_checks'] = lines[i]['takes']
        row['leftover_db'] = rs.leftover(original_44, stem_pcm, rows[i])
    windows = [lines[i]['window'] for i in sorted(picked)]
    # The clip's own sound at its own rate: outside the windows not one sample changes.
    original_pcm, rate, audio_info = rs.native_audio(source)
    count = round(duration * rate)
    original_pcm = original_pcm[:count]
    # Some MP4 edit lists outlast the decoded audio. Preserve that silent
    # tail too; otherwise re-voicing shortens the movie's container clock.
    if len(original_pcm) < count:
        original_pcm = np.pad(original_pcm, ((0, count - len(original_pcm)), (0, 0)))
    mixed = rs.apply_delta(original_pcm, delta, windows, rate)
    kept.sort(key=lambda row: row['line'])
    np.save(folder / 'original-pcm.npy', original_pcm)
    wav = folder / 'mix.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(rate), '-ac', str(mixed.shape[1]),
                    '-i', '-', '-c:a', 'pcm_f32le', str(wav)], input=mixed.astype(np.float32).tobytes(), check=True)
    if target is None:
        audio_only = False
        destination = source
        pending = source.with_name(source.stem + '.voice-pass.partial.mp4')
    else:
        audio_only = bool(target.audio_only)
        destination, pending = target.destination(folder)
    sound_kept = 'wav'
    try:
        if audio_only:
            command = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(wav), '-c:a', 'pcm_s24le',
                       '-t', str(duration), str(pending)]
        else:
            command, sound_kept = rs.mux_command(source, wav, pending, folder, duration, windows, rate, audio_info)
        result = P.run_tracked_subprocess(command, pgid_key='train_pgid', label='voice pass mix',
                                          job=job, timeout=120)
        if result.returncode or not pending.is_file():
            raise RuntimeError('Voice pass could not save the soundtrack: ' + result.stderr[-300:])
        if sound_kept == 'spliced' and not rs.splice_holds(pending, mixed, windows, rate):
            # The kept packets did not decode as expected (another encoder's packet
            # grid): fall back to one clean encode of the whole new soundtrack.
            command, sound_kept = rs.encode_command(source, wav, pending, duration, audio_info), 'encoded'
            result = P.run_tracked_subprocess(command, pgid_key='train_pgid', label='voice pass mix',
                                              job=job, timeout=120)
            if result.returncode or not pending.is_file():
                raise RuntimeError('Voice pass could not save the soundtrack: ' + result.stderr[-300:])
        P._raise_if_cancelled(job, 'voice pass publication')
        if target is None:
            # Keep the original for comparison/recovery; do not overwrite it on failure.
            P.shutil.copyfile(source, folder / 'original.mp4')
            pending.replace(destination)
        else:
            target.publish(pending, destination)
            job['output_path'] = str(destination)
        job['revoice_progress'] = {'stage': 'done'}
        p['voice_pass'] = {'phrases': receipt, 'video': 'none' if audio_only else 'copied', 'room_tone': 'original residual',
                           'duration': duration, 'original_spans': spans, 'kept': kept, 'weak': weak,
                           'windows': windows, 'sample_rate': rate, 'fps': fps,
                           'sound_kept': sound_kept}
        notes = ([kept_warning(kept)] if kept else []) + ([weak_warning(weak)] if weak else [])
        if notes:
            job['warning'] = ' '.join(notes)
        sidecar = destination.with_suffix(destination.suffix + '.json')
        record = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
        record.setdefault('params', {}).update(p)
        P.write_sidecar(sidecar, record)
    finally:
        pending.unlink(missing_ok=True)
