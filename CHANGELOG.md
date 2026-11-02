# Changelog

## Quality and scale overhaul — September 2026

A single session of debugging that started as *"the video quality is too low"* and
ended up touching ingest, transcription, highlight detection, rendering, captions
and storage. Most of what follows are root causes rather than features: the
pipeline was doing the wrong thing quietly, and in several places the logs
actively pointed the wrong way.

### The headline numbers

| | Before | After |
|---|---|---|
| Source resolution downloaded | **640 × 360** | up to **1440p** |
| Real pixel width in a 9:16 crop | 114 px | 608 px at 1080p source |
| Vertical output | 720 × 1280, CRF 23 | **1080 × 1920**, CRF 18, lanczos |
| Clips returned when 5 requested | **1** | 5 |
| Maximum video length | 15 minutes | **4 hours** |
| Transcription ceiling | ~52 minutes (hard failure) | none |
| Supported URL platforms | 1 (YouTube) | **10** |
| Caption languages | spoken language only | spoken language **+ English** |
| Tests | 50 | **214** |

---

## Fixes

### The video quality problem was never the encoder

Every clip was soft. The encoder settings were raised first — CRF, preset,
scaler — and it changed almost nothing, because the input was the problem.

A `ffprobe` of the actual source file on disk:

```
source/original.mp4   640x360   h264 Main   632 kbit/s
```

A 9:16 crop of a 360p landscape frame is roughly **114 real pixels of width**,
stretched to 1080. That is a 5.3× upscale. No encoder setting survives it.

**Root cause:** YouTube's 720p+ streams are DASH formats behind a JavaScript
signature challenge. yt-dlp needs a JS runtime to solve it, and it enables only
**Deno** by default. Node was installed; Deno was not. So yt-dlp silently fell
back to the one stream that needs no signature — itag 18, the 640 × 360 muxed
format — and nothing in the logs said so.

**Fixed by** enabling every runtime yt-dlp supports (`deno, node, quickjs, bun`)
and adding a startup check that refuses to let a missing runtime be silent:

> No JavaScript runtime found (looked for: deno, node, quickjs, bun). YouTube
> will only serve the 360p stream, and vertical clips will be badly upscaled.

### The downloader took the first client that worked, not the best one

`download_video` walked a list of yt-dlp client strategies and accepted whichever
one succeeded first. A client offered only the 360p muxed stream would beat one
offering 1080p, and nothing ever compared them.

It now **probes clients, compares what each is actually offered, and downloads
once with the best** — stopping early as soon as something ≥720p turns up, so the
common case costs nothing extra. It logs what it chose and what landed on disk:

```
[job] client 'default' offers up to 1080p
[job] downloading with client 'default' at up to 1080p
[job] source downloaded: 1920x1080
```

When a client is handed a signed URL that YouTube then rejects mid-transfer with
a 403, it falls through to the next client rather than failing the job. The
client ladder is ordered by *what can finish*, not *what is offered*:
`android_vr`, `web_embedded` and `tv` do not require a proof-of-origin token, so
they sit ahead of `android` (capped at 360p) and `ios`.

### Asking for five clips returned one

Two filters stacked, and a third thing that should have rescued it did not run.

- a hard `score < 60` floor discarded every merely-decent candidate
- a fixed `0.62` topic-similarity ceiling treated every candidate in a
  single-subject video as a duplicate of the first
- `recover_highlights` only ran when selection returned **zero** — return one of
  five and nothing filled the other four

Measured on a synthetic single-topic transcript: the old code returned **0**, the
new code returns **3** from the same input, with the pipeline topping up to the
target.

Selection is now **tiered**. The strict pass runs first, so premium picks are
still chosen first and in the same order; later passes only run while slots
remain empty:

```python
_SELECTION_TIERS = ((60, 0.62), (52, 0.75), (45, 0.88), (0, 1.01))
```

### Errors blamed YouTube for our own bugs

A malformed yt-dlp option produced the same user-facing message as a genuine
platform refusal — *"YouTube may be temporarily blocking automated downloads"* —
while the real cause was a `ValueError` raised before any network call. That cost
a full debugging round trip in the wrong direction.

Failures are now classified before they are described: a 403 says the platform
refused, a bad option says *"the downloader is misconfigured… this is a bug on
our side"*, and the message names the platform the user actually pasted.

### Hindi captions would have rendered as empty boxes in production

`fonts/` contained only `BebasNeue-Regular.ttf`, and two caption presets named
**`Arial`** — which has never been in this repository and does not exist on most
Linux servers. macOS fontconfig was quietly substituting both, so Hindi captions
looked perfect in development and would have burned in as `□□□□` the moment this
deployed.

**Fixed by** bundling **Poppins-Bold**, which was drawn as a Devanagari-and-Latin
family. That pairing is the point: Indian creators code-mix constantly, and
`बहुत good है` is one caption line — a Devanagari-only face would only have moved
the boxes onto the English words.

The caption face is now chosen from the script present in the text, looking at
the whole clip rather than the first word. The test that matters asserts **every
font any preset names is a real file in `fonts/`** — the check that would have
caught `Arial` long ago.

### Caption sizing was tuned for a canvas that no longer existed

A fixed 28–72 px clamp was correct for a 720 px-wide frame. At 1080 it silently
shrank captions by a third. Bounds are now proportional to frame width.

---

## New capabilities

### Ten platforms, not one

The API hard-rejected every host but `youtube.com`, and the entire client ladder
was YouTube-specific machinery. A platform registry (`services/platforms.py`) now
covers:

**YouTube · Twitch · Vimeo · Loom · X (Twitter) · Reddit · Instagram · TikTok ·
Facebook · Dailymotion**

Each entry carries its hosts, download strategy, cookie configuration and whether
it typically needs a login. Host matching handles the subdomains people actually
paste — `m.youtube.com`, `clips.twitch.tv`, `vm.tiktok.com`, `old.reddit.com`,
`v.redd.it`, `fb.watch`, `dai.ly` — and a test asserts `evilyoutube.com` does
**not** match, because a naive suffix check would have let it through.

Only YouTube gets a `player_client` ladder; it means nothing to other extractors.
Per-platform cookies via `YTDLP_COOKIES_FILE_<KEY>`, falling back to the shared
file. Adding a platform is one entry.

### Four-hour videos

Two separate ceilings had to go.

**Transcription** was one upload against Groq's 25 MB limit — about 52 minutes at
64 kbps mono. Audio is now cut into 10-minute windows (~4.8 MB each, a wide
margin, with no reduction in audio quality).

The difficulty is the seam, not the splitting. Whisper is markedly worse at the
first and last words of a clip because it has no context there. So each window is
extracted **4 seconds early** — but every word is assigned to exactly one window
by its **absolute start time**. The overlap exists to make Whisper accurate across
the boundary, not to be stitched.

**Highlight detection** sent the entire transcript in one prompt. A three-hour
podcast is ~27,000 words / ~72,000 characters, which a model either refuses or
skims. It is now map-reduce over ~2,500-word windows:

```
transcript:          27,000 words / 180 min
audio windows:       24   (was: 1 request, hard fail past 52 min)
highlight windows:   12   (was: 1 prompt of 71,946 chars)
per-window size:     2,500 words / 6,483 chars
final clips:         5    spread at [1.9, 34.5, 67.2, 99.8, 132.5] min
```

The prompt explicitly states that timestamps are **absolute positions in the full
recording** — without it the model renumbers each window from zero and every clip
after the first points at the wrong part of the video. A test fails if that
instruction disappears.

One failing window no longer loses the others; a three-hour job is expensive to
retry from scratch. And when a transcript has neither punctuation nor speech
pauses — the shouted-gameplay case — there is a last-resort time-slice fallback,
scored low so it never reads as a confident pick.

Progress reports per window rather than sitting still for six minutes:

```
Transcribing speech · part 7 of 24
Finding the best moments · part 3 of 12
```

### Caption language toggle

**Original / English**, in the studio's caption panel.

Translation swaps the words *in the editor* rather than at render time, so live
preview, manual edits, the segment track and undo all keep working on translated
captions exactly as they do on the originals.

It works in **phrases, not words**. A translation has different words and usually
a different number of them, so word-level sync cannot survive — `मैंने कहा` and
`I said` have nothing to line up. Each phrase is redistributed across the exact
span the original occupied:

```
10.000 → 11.500   This is really good      (original phrase: 10.0–11.5)
12.500 → 14.000   You can see it           (original phrase: 12.5–14.0)
```

The one-second silence between them is preserved. Within a phrase, words get time
proportional to their length — an equal split gave `a` as much screen time as
`extraordinary`, which reads badly.

Devanagari's danda (`।`) counts as a sentence end. Treating it as an ordinary
character ran whole paragraphs into one caption phrase.

Original stays the default, because word-level karaoke highlighting only survives
there.

### Zoom bounded by what the source can support

A 9:16 crop already upscales; zoom multiplies it. The smart-reframe ceiling is now
derived from the source:

| Source | Zoom ceiling |
|---|---|
| 720p | 1.00 (none) |
| 1080p | 1.12 |
| 1440p | 1.50 |
| 4K | 2.25 |

The cap lives in the tracker, so the figure the studio displays is the figure that
actually renders, with a backstop in the cutter for manually-set zoom.

### Storage that looks after itself

Expiry only ever saw files with an artifact row, so anything whose row never
existed or had been removed — a crashed run, a wiped table — stayed on disk
forever. Expiry is also *time*-based, which cannot hold a ceiling: twenty 5 GB
sources arriving in one hour fill the disk while every one of them is still hours
from expiring. That stopped being hypothetical the moment four-hour videos were
allowed.

The maintenance pass now runs three steps every 30 minutes:

1. **Expired artifacts** — unchanged
2. **Orphan sweep** — job media the database no longer knows about, with a grace
   period and a newest-mtime check so a job mid-render is never touched
3. **Disk budget** — evicts least-recently-used jobs until under `MAX_STORAGE_GB`

Eviction *expires rows and lets normal cleanup remove the files*, so no database
row is ever left pointing at a file that vanished. A test fails if budget
enforcement deletes a file directly.

```
python cleanup_storage.py --dry-run     # what a pass would remove
python cleanup_storage.py --execute     # run it
python cleanup_storage.py --purge-all   # delete all job media (test data)
```

### Studio

- the finished export plays **at size** in the studio with download and open
  beside it, instead of a text link
- clip grid used `auto-fit`, so a single clip stretched to the full row width and
  a 9:16 thumbnail became ~1800 px tall → `auto-fill` with a max card width
- studio preview raised from 520 px to 720 px tall

---

## Configuration

New settings, all with working defaults.

| Variable | Default | Purpose |
|---|---|---|
| `MAX_SOURCE_HEIGHT` | `1440` | Cap on downloaded source resolution |
| `OUTPUT_HEIGHT` | `1080` | Drives both output orientations |
| `VIDEO_CRF` | `18` | x264 quality |
| `VIDEO_PRESET` | `medium` | x264 speed/size trade-off |
| `AUDIO_BITRATE` | `192k` | Output audio |
| `MAX_UPSCALE_FACTOR` | `2.0` | Total upscale budget; bounds reframe zoom |
| `MAX_VIDEO_DURATION_SECONDS` | `14400` | 4 hours |
| `TRANSCRIBE_CHUNK_SECONDS` | `600` | Audio window length |
| `TRANSCRIBE_OVERLAP_SECONDS` | `4` | Context lead-in per window |
| `HIGHLIGHT_WINDOW_WORDS` | `2500` | Transcript window for detection |
| `HIGHLIGHT_OVERLAP_WORDS` | `200` | Shared text between windows |
| `MAX_STORAGE_GB` | `20` | Disk ceiling before eviction |
| `ORPHAN_GRACE_HOURS` | `2` | Protects jobs mid-render from the sweep |
| `YTDLP_JS_RUNTIMES` | `deno,node,quickjs,bun` | Runtimes offered to yt-dlp |
| `YTDLP_HTTP_CHUNK_SIZE` | `10485760` | Ranged request size |
| `YTDLP_COOKIES_FILE_<KEY>` | — | Per-platform cookies, e.g. `_TWITCH` |

---

## Deployment notes

Three things that work on a developer's Mac and break on a Linux server. All are
now guarded, but they are worth knowing:

1. **A JavaScript runtime must be installed** or every YouTube download silently
   degrades to 360p. The startup check warns loudly.
2. **Fonts must be bundled, not assumed.** Anything a caption preset names has to
   exist in `fonts/`; the host's installed fonts are not a plan.
3. **ffmpeg needs libass.** Already checked at startup.

---

## Known limits

- **No speaker diarization.** Groq's Whisper does not return it, so "cut to
  whoever is speaking" is not possible. Face tracking picks the most prominent
  face, not the active speaker.
- **Highlight detection reads the transcript only.** In a gaming stream the best
  moment is often where nobody says anything quotable. Audio-energy scoring is the
  obvious next step.
- **Processing a four-hour recording takes real time** — roughly 24 Whisper calls
  plus 12 LLM calls before any rendering begins.
- **URL ingest is best-effort by nature.** Ten platforms is ten sets of terms and
  ten things that can change without notice. Direct upload is the path that
  always works, and the product should say so.

---

## Testing

214 tests. The ones worth knowing about are the ones that encode a mistake
already made:

- every font a caption preset names is a real file in `fonts/`
- the real options dict is handed to the real `YoutubeDL` constructor — a unit
  test asserting the shape we *assumed* was right is how a list-instead-of-dict
  broke every download
- the model is told timestamps are absolute
- token-free download clients are ordered ahead of the 360p-capped ones
- budget enforcement expires rows instead of deleting files
- a word heard by two transcription windows appears once
- every platform failure message points at upload as the way out
