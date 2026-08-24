# Clipmaker quality evaluation

This suite answers a product question with evidence: did a change make clips
better, worse, or only different? All analysis and rendered samples stay local.

## First setup

From `clipmaker-backend`:

```bash
.venv/bin/python quality_eval.py init
mkdir -p evaluation/videos
```

`init` creates `evaluation/cases.local.json`. Both that private manifest and
the videos/results folders are ignored by Git. Add one legally usable test
video for each case name, then adjust each `start` and `end` to a useful
15–25 second section. You may also use absolute source paths.

Do not collect 15 nearly identical talking-head clips. The template deliberately
covers edge placement, profiles, low light, small faces, motion, conversations,
panels, scene cuts, B-roll, vertical input, screen sharing, occlusion, captions,
and speech boundaries.

## Run it

```bash
.venv/bin/python quality_eval.py run \
  --manifest evaluation/cases.local.json \
  --output-dir evaluation/results/current \
  --render
```

Open `evaluation/results/current/report.html`. The JSON beside it is designed
for scripts and future CI. `--render` is slower, but it verifies the real FFmpeg
output is 720×1280, has video, and has the expected duration.

## Human review

Automatic metrics cannot know whether the visually correct person is speaking,
whether a crop feels natural, or whether a thought was cut off. Watch every
render and enter 1–5 ratings in `human_review`:

- `correct_subject`: crop follows the actual active speaker.
- `composition`: face/headroom look intentional and important content remains.
- `stability`: camera path is calm; no jitter or unnecessary jumps.
- `clip_completeness`: clip starts with context and ends after the thought.
- `caption_readability`: correct, timed, inside the safe area, at most two lines.
- `overall`: would you publish this without fixing it?

The final score uses 65% automatic and 35% human review when ratings exist.
Without ratings, the final score is the automatic score and the report makes
that visible.

## Compare a new algorithm to a baseline

Keep a known-good report, then run:

```bash
.venv/bin/python quality_eval.py run \
  --manifest evaluation/cases.local.json \
  --output-dir evaluation/results/candidate \
  --render \
  --baseline evaluation/results/baseline/report.json \
  --fail-on-regression
```

A case is a regression if it changes from passed to failed or its final score
drops by more than three points. Adjust with `--regression-tolerance` only when
there is a documented product reason.

## Reading the metrics

- Detection ratio: sampled frames where a subject was found.
- Confidence: detector/tracker confidence for usable path points.
- Horizontal/vertical speed: fastest crop movement per second. Scene cuts are
  excluded because a cut is not a camera pan.
- Edge-hold ratio: how often the crop remains at its horizontal limit; a high
  value can mean the subject cannot be framed comfortably.
- Subject switches: tracker identity changes. A conversation may legitimately
  have more than a single-speaker clip.
- Centered fallback: safe behavior when no reliable face exists.

Passing thresholds is the release gate; the score helps rank improvements. Do
not tune the algorithm merely to increase the score—watch the private renders.
