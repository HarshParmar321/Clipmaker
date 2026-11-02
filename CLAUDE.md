# CLAUDE.md

Working notes for anyone — person or model — writing code in this repository.
Read this before changing the pipeline. Most of it is here because something
already went wrong once.

---

## The shape of the thing

A job moves through seven stages, each of which can fail independently and
resume from the last checkpoint:

```
downloading → transcribing → detecting_highlights → cutting_clips
           → burning_captions → thumbnails → done
```

`services/pipeline.py` orchestrates. Everything else is a service that does one
thing and does not know about job state. Keep it that way: when a stage needs
something new, the service gains a parameter, not a database import.

**Media is local, metadata is Supabase.** `media_storage.py` is the only thing
that constructs paths — ask it, never build a path by hand. Supabase has a
memory fallback so the app boots without it.

---

## Rules that are not negotiable

### Never let a failure be silent

This codebase has been bitten three times by something that worked on a Mac and
degraded quietly in production. Every one cost hours to find because nothing
logged it.

- YouTube served 360p for weeks because a JS runtime was missing
- `Arial` was named by two caption presets and has never existed in this repo
- selection discarded four clips out of five and reported success

If a decision affects output quality, **log the decision and the reason**, and add
a startup check if it can be detected at boot. `startup_checks.py` is cheap and
runs before any user is affected.

### Error messages must say whose fault it is

Three categories, and they need different handling:

| Kind | Example | What the user should see |
|---|---|---|
| **User-fixable** | video too long, no speech | what to change |
| **Transient** | 429, dropped connection | that retrying will help |
| **Our bug** | malformed option, bad config | that it is on us |

A malformed yt-dlp option once produced *"YouTube may be blocking automated
downloads"*. The real cause was a `ValueError` thrown before any network call.
See `_download_failure_message` in `downloader.py` for the pattern.

### Test the contract, not your assumption

The bug that broke every download was a unit test asserting the option shape we
*believed* was right. `js_runtimes` is a list on the CLI and a **dict** in the
library API.

The fix was a test that hands the real options dict to the real `YoutubeDL`
constructor. Where a boundary exists — ffmpeg, yt-dlp, Groq, fontconfig — assert
against the real thing, not a mock of what you expect it to want.

### Bundle what you name

Anything a caption preset names must be a file in `fonts/`. The host's installed
fonts are not a plan; macOS substitutes silently and Linux renders `□□□□`. There
is a test that enforces this — do not weaken it.

---

## Traps, by area

### Resolution is decided before the encoder

A 9:16 crop of a landscape frame keeps about 31% of its width. Encoder settings
cannot recover what the source never had:

| Source | Usable width in 9:16 | Upscale to 1080 |
|---|---|---|
| 360p | 114 px | 5.3× — unusable |
| 720p | 228 px | 2.7× |
| 1080p | 342 px | 1.6× |
| 1440p | 456 px | 1.2× |

Before blaming CRF or the scaler, `ffprobe` the source. Zoom multiplies the
problem, which is why `source_zoom_ceiling()` exists.

### Windows, and why words belong to exactly one

Both transcription and highlight detection window their input. Both overlap. The
overlap exists so the **model** has context across the seam — Whisper is bad at
the first and last words of a clip, and a highlight does not politely end on a
boundary.

Overlap is **not** for stitching. Every word is assigned to exactly one window by
its absolute start time. If you touch this, the test to keep passing is
`test_a_word_heard_by_two_windows_appears_once`.

Windowed highlight prompts must state that **timestamps are absolute positions in
the full recording**. Without it the model renumbers each window from zero and
every clip after the first points at the wrong part of the video.

### Scoring ranks; it must not filter

A hard score floor plus a fixed topic-similarity ceiling meant a single-subject
video returned one clip no matter how many were asked for. Selection is now
tiered — strict first, then progressively relaxed, and only while slots remain
empty:

```python
_SELECTION_TIERS = ((60, 0.62), (52, 0.75), (45, 0.88), (0, 1.01))
```

If you add a quality signal, make it affect **rank**, not **inclusion**. Returning
fewer clips than the user asked for is a failure, not strictness.

### Preview must match export

Three places encode the same truth and will drift apart:

- `render_settings.TARGET_DIMENSIONS` — the real output size
- `caption_burner` font bounds — proportional to frame width
- `CAPTION_LAYOUTS` in `script.js` — the browser preview

Change one, change all three. The comment in `script.js` says so; believe it.

### Script-aware fonts

`caption_burner.font_for_text()` picks the face from the script in the caption
text, across the whole clip rather than the first word. Adding a language is one
entry in `SCRIPT_FONTS` plus the font file.

Pick faces that cover **Latin too**. Indian creators code-mix constantly —
`बहुत good है` is one caption line, and a Devanagari-only font moves the empty
boxes onto the English words instead of fixing anything.

### Translation cannot keep word timing

A translation has different words and usually a different number of them.
`caption_translator.py` works in phrases: each translated phrase is redistributed
across the exact span the original occupied. Within a phrase, words get time
proportional to their length.

Devanagari's danda (`।`) is a sentence end. Latin-only punctuation regexes run
whole paragraphs into one phrase.

### Deleting files

`media_storage.delete()` refuses anything outside its root. Keep it that way.

Budget eviction **expires artifact rows and lets normal cleanup remove the
files** — it never deletes directly, so no row is left pointing at a file that
vanished. A test enforces this.

The orphan sweep uses the **newest mtime anywhere in the tree**, not the folder's
own. A directory's mtime does not move when a grandchild is written, so a job
busy rendering looks untouched. Deleting a source out from under ffmpeg is a
memorable bug.

---

## Conventions

**Comments explain why, not what.** `# increment counter` is noise. `# A
directory's mtime does not move when a grandchild is written` is the reason the
next person does not break it.

**Config over constants.** Anything someone might tune belongs in `config.py`
with a comment explaining the trade-off and what happens at the extremes.

**Log numbers, not adjectives.** `source downloaded: 1920x1080` beats `download
complete`.

**Tests encode bugs.** When you fix something subtle, the test's name and
docstring should say what went wrong. Future-you will not remember.

**Never commit secrets.** `.env`, `cookies.txt`, `storage/` and `venv/` are
gitignored and have never been tracked. Keep it that way.

---

## Commands

```bash
# backend
cd clipmaker-backend && source venv/bin/activate
python -m uvicorn main:app --reload --port 8000

# frontend (static, must be port 5500 for CORS)
cd clipmaker-frontend && python3 -m http.server 5500

# tests — all of them, every time
python -m unittest discover -s tests

# one file
python -m unittest tests.test_highlights

# disk
python cleanup_storage.py --dry-run
```

**Bump the cache-bust query when you touch frontend assets.** `index.html`
references `style.css?v=N` and `script.js?v=N`. Forgetting this means the browser
serves stale JavaScript and the bug you "fixed" is still there — this has
happened and wasted a full debugging cycle.

---

## Before you call something done

- [ ] Full test suite passes, not just the file you touched
- [ ] New behaviour has a test named after the failure it prevents
- [ ] Anything that affects output quality logs its decision
- [ ] Anything that can be checked at boot is in `startup_checks.py`
- [ ] Error messages say whose fault it is
- [ ] Preview still matches export, if you touched dimensions or captions
- [ ] Cache-bust bumped, if you touched frontend assets
- [ ] No secrets, no `storage/`, no `venv/` in the diff

---

## Known limits

Do not promise these; they are not built.

- **No speaker diarization.** Groq's Whisper does not return it, so "cut to
  whoever is speaking" is impossible. Face tracking finds the most prominent
  face, not the active one.
- **Highlight detection reads the transcript only.** In a gaming stream the best
  moment is often where nobody says anything quotable.
- **No job queue.** `uvicorn --reload` with background tasks is a development
  setup; one long encode blocks the loop and a restart loses in-flight jobs.
- **Local disk only.** Fine for one machine; a separate worker process would need
  object storage first.
- **URL ingest is best-effort.** Ten platforms, ten sets of terms, ten things that
  can change without notice. Upload is the path that always works, and the
  product should keep saying so.
