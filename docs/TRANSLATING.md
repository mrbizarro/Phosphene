# Translating Phosphene

Two parts of Phosphene can be translated, each with its own tool and the same
round trip: **glossary → export → translate → import → native review → check**.

| Part | Where the English lives | Translations | Tool |
|---|---|---|---|
| **Studio** (the panel) | the page and the JS code itself; the list is `i18n/studio/en.json` | `i18n/studio/<lang>.json` | `python3 scripts/i18n_tool.py` (this repo) |
| **Mac app** | `app/Sources/Copy.swift`, `SupportCopy.swift`, SwiftUI labels | `app/Resources/Localizable.xcstrings` (Apple String Catalog) | `python3 tools/i18n.py` (app repo) |

The website has its own system (`src/i18n/*.json`). The iPhone Remote is not
translatable yet (see `REMOTE_BACKLOG.md`).

**One file per language, no regional variants.** A single neutral `es`, `pt`,
`fr`… that reads well everywhere. The one exception is Chinese, where
Simplified and Traditional are different scripts: `zh-Hans` (Simplified) first.

Codes: `es` Spanish, `de` German, `fr` French, `it` Italian, `pt` Portuguese,
`ja` Japanese, `zh-Hans` Chinese (Simplified), `ko` Korean, `ru` Russian,
`ar` Arabic, `he` Hebrew.

Which language people see: the Mac's own language list (System Settings →
General → Language & Region). In the Studio, **Settings → Language** overrides
it. Anything without a translation shows in English, never as a code word.

## What makes a good translation here

Translations must make sense to a real user, not be word-for-word. "Outputs"
is the gallery of the user's own videos, so in Spanish it is *Galería*, not
*Salidas*. Four things make that happen:

1. **Every string says what it is and does.** Notes in the export name the
   screen and its purpose (from `i18n/studio/areas.json` — edit it when you add
   a screen), the button's own tooltip, and, for pieces the code glues
   together, the whole sentence (`FRAGMENT: «…»`).
2. **A glossary of core terms** (`i18n/glossary.json`): Outputs, Queue,
   Render, Draft, Extend, Seed, LoRA, Character, Storyboard, Upscale,
   Settings, Pro…, each with its meaning and the agreed word per language.
   Every export file lists the terms its strings use; import refuses a
   translation that drops a *strict* term's agreed word and warns for the
   others. The Studio's file is the master; the Mac app keeps a copy
   (`python3 tools/i18n.py glossary <lang> --sync <studio repo>`).
3. **The translator instructions** (top of each export file) say: translate
   meaning for a native user of a creative app, use the words that language's
   popular video and photo apps use, keep it short and natural, one neutral
   language, never literal when the literal word isn't what a native says.
4. **A native-reviewer pass** after import: a model reads every translated
   string with its context and flags anything unnatural, too literal or
   inconsistent, with a better text. `status` only says **DONE** when every
   string is translated *and* reviewed.

## A round, step by step (Studio)

Run in the Studio repo. Example: Japanese.

1. **Agree the core words** (new language only):

   ```bash
   python3 scripts/i18n_tool.py glossary ja
   ```

   Paste `i18n/rounds/ja/ja.glossary.txt` into the model, save its answer as
   `i18n/rounds/ja/ja.glossaryreply.txt`, then:

   ```bash
   python3 scripts/i18n_tool.py glossary ja --import i18n/rounds/ja/ja.glossaryreply.txt
   ```

   Look at the words it picked (in `i18n/glossary.json`, under `"ja"`); change
   any you don't like before going on.

2. **Export** what is missing or changed:

   ```bash
   python3 scripts/i18n_tool.py export ja
   ```

   It writes `i18n/rounds/ja/ja.part01.txt`, `ja.part02.txt`… (about 150
   strings each, so one reply fits in one answer).

3. **Translate**: paste each part into the model as it is (instructions,
   glossary and all). Save each answer next to it with the same number:
   `ja.reply01.txt`, `ja.reply02.txt`… (```json fences are fine).

4. **Import**:

   ```bash
   python3 scripts/i18n_tool.py import ja i18n/rounds/ja/ja.reply*.txt
   ```

   `✗` = not saved (broken placeholder, HTML, a strict glossary word missing,
   an empty answer): ask the model to fix just those ids, save, import again.
   `!` = a warning (too long for its button, a looser glossary word): worth a
   look, still saved.

5. **Native review**:

   ```bash
   python3 scripts/i18n_tool.py review ja
   ```

   Paste each `ja.reviewNN.txt` into the model (ideally a fresh chat, so it
   reads with new eyes). Save each answer as `ja.reviewreplyNN.txt` (an answer
   of `{}` means "all good" — save it too), then:

   ```bash
   python3 scripts/i18n_tool.py review-apply ja i18n/rounds/ja/ja.reviewreply*.txt
   ```

   Fixes are checked like an import. Run `review ja` again: it only sends what
   changed, until it says every string is reviewed.

6. **Check and look**:

   ```bash
   python3 scripts/i18n_tool.py check
   python3 scripts/i18n_tool.py status
   ```

   Open a test Studio, choose the language in Settings → Language, look at the
   main screens. Commit `i18n/studio/ja.json` and `i18n/glossary.json`
   (`i18n/rounds/` is scratch and is not committed).

When the English changes later, run `export` and `review` again: they only
send the new and changed strings.

## A round, step by step (Mac app)

In the app repo, the same steps:

```bash
python3 tools/i18n.py glossary ja --sync <studio repo>   # after step 1 above
python3 tools/i18n.py export ja                          # scratch/i18n-rounds/ja/ja.partNN.txt
# translate each part → ja.replyNN.txt next to it
python3 tools/i18n.py import ja scratch/i18n-rounds/ja/ja.reply*.txt
python3 tools/i18n.py review ja                          # → ja.reviewreplyNN.txt
python3 tools/i18n.py review-apply ja scratch/i18n-rounds/ja/ja.reviewreply*.txt
python3 tools/i18n.py check && python3 tools/i18n.py status
```

`tools/i18n_snap/build.sh` then `app/build/i18n_snap/i18n_snap ja.png
-AppleLanguages '(ja)'` draws the welcome, Pro and Support text into a
picture to look at, without changing the Mac's language.

## Running a language with a DeepSeek agent

A whole language can run as one Panel job. Start it from the Studio repo:

```bash
hq agent --adapter deepseek --write --title "Studio + Mac app in Japanese" @docs/TRANSLATING.md
```

and tell it the language code (or put the brief below in a file and pass
`@file`). The agent's brief:

> Translate Phosphene into **ja** following docs/TRANSLATING.md, as a native
> ja speaker. Studio repo: this folder. Mac app repo:
> ~/AI/projects/phosphene/wt/app-i18n.
> 1. `python3 scripts/i18n_tool.py glossary ja`; read the file it names, write
>    your answer to the reply path it prints, run the `--import` line.
> 2. `python3 scripts/i18n_tool.py export ja`. For every `ja.partNN.txt`, read
>    it, follow its instructions exactly, and write ONE JSON object to
>    `ja.replyNN.txt` beside it. Check each with
>    `python3 scripts/i18n_tool.py import ja <reply> --dry-run` and fix every
>    `✗` until there are none. Then import them all (no `--dry-run`).
> 3. `python3 scripts/i18n_tool.py review ja`. For every `ja.reviewNN.txt`,
>    read it as a fresh native reviewer and write `ja.reviewreplyNN.txt`
>    (`{}` if all good). Run `review-apply`. Repeat `review ja` until it says
>    every string is reviewed.
> 4. `python3 scripts/i18n_tool.py check` must say PASS; `status` must say
>    DONE for ja.
> 5. In the Mac app repo, the same with `python3 tools/i18n.py` (glossary
>    `--sync` with the Studio repo path first).
> 6. Report the `status` lines and anything you were unsure of. Do not
>    commit or push.

The tools print the exact next command after each step, so the agent never
has to guess a path.

## Adding a new language

Nothing to set up: the first `glossary`/`export`/`import` for a code creates
its entries, and the language appears in Settings → Language and in the Mac
app once it has translations. Right-to-left languages (Arabic, Hebrew) flip
the Studio's layout by themselves.

## Layout test languages

```bash
python3 scripts/i18n_tool.py pseudo
```

writes two fake languages into the Studio (not committed): **Pseudo · long**
(`en-XA`, 30% longer with accents, like German) and **Pseudo · RTL**
(`ar-XB`). Text that shows **without** brackets in Pseudo · long is not in
the lookup yet. The Mac app: `python3 tools/i18n.py pseudo`, then
`pseudo --remove` before committing.

## For developers: writing new UI text

- **Markup** (`webapp/index.html`): write the English, then
  `python3 scripts/i18n_tool.py tag` (gives new text a `data-i18n` key) and
  `python3 scripts/i18n_tool.py extract`. A new screen gets a line in
  `i18n/studio/areas.json` saying what it is.
- **JS**: `T('module.key', 'English with {name}', {name: value})`; plural
  `TP('module.key', n, {one: '{count} clip', other: '{count} clips'})`;
  inside an HTML attribute in a template `TA(…)`.
  `python3 scripts/i18n_tool.py wrap webapp/js/x.js` converts plain literals.
  Never glue English plurals (`'clip' + (n === 1 ? '' : 's')`); prefer one
  whole sentence with `{placeholders}` over pieces joined with `+`.
- **Mac app**: `L("English")`, `LF("Used %@ of %@", a, b)`,
  `LP("%lld new replies", n, one: "%lld new reply")`; SwiftUI `Text("…")`
  literals are looked up automatically. Then `python3 tools/i18n.py extract`.
- The release gates run `i18n_tool.py check` and `test_i18n.py`: they fail on
  new English UI text outside the lookup, an out-of-date `en.json`, or a
  translation with broken placeholders or HTML.

How it works inside: `i18n.py` picks the language and puts that language's
strings into the page data (`BOOT.i18n`); `webapp/js/i18n.js` translates the
marked-up page once at start-up and answers `T()` calls. English is never
looked up — it is already in the code — so the English Studio is byte for
byte what it was.
