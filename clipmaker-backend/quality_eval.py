#!/usr/bin/env python3
"""Repeatable, private quality evaluation for Clipmaker Smart Reframe.

The evaluator reads a JSON manifest, analyses each source locally, optionally
renders a 9:16 sample, and writes machine-readable JSON plus a self-contained
HTML report. Video frames never leave the machine.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from services.face_tracker import FocusPoint, ReframeAnalysis, track_subject
from services.render_settings import OutputFormat, ReframeMode
from services.video_cutter import cut_clip, probe_media

SCHEMA_VERSION = 1
CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
HUMAN_FIELDS = (
    "correct_subject",
    "composition",
    "stability",
    "clip_completeness",
    "caption_readability",
    "overall",
)
DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_detection_ratio": 0.45,
    "min_average_confidence": 0.35,
    "max_horizontal_speed_per_second": 0.42,
    "max_vertical_speed_per_second": 0.32,
    "max_edge_hold_ratio": 0.55,
    "max_subject_switches": 4,
    "max_zoom": 1.35,
}


class ManifestError(ValueError):
    """A user-fixable manifest validation error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _finite_number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"{label} must be a number") from exc
    if not math.isfinite(result):
        raise ManifestError(f"{label} must be finite")
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"Manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Manifest is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ManifestError("Manifest must be an object containing a cases array")
    seen: set[str] = set()
    for index, case in enumerate(payload["cases"], start=1):
        if not isinstance(case, dict):
            raise ManifestError(f"Case {index} must be an object")
        case_id = str(case.get("id", ""))
        if not CASE_ID_PATTERN.fullmatch(case_id):
            raise ManifestError(
                f"Case {index} id must use lowercase letters, numbers, - or _"
            )
        if case_id in seen:
            raise ManifestError(f"Duplicate case id: {case_id}")
        seen.add(case_id)
        if not case.get("source"):
            raise ManifestError(f"Case {case_id} is missing source")
        start = _finite_number(case.get("start", 0), f"{case_id}.start")
        end = _finite_number(case.get("end", 0), f"{case_id}.end")
        if start < 0 or end <= start:
            raise ManifestError(f"Case {case_id} needs 0 <= start < end")
        review = case.get("human_review") or {}
        if not isinstance(review, dict):
            raise ManifestError(f"{case_id}.human_review must be an object")
        for field in HUMAN_FIELDS:
            rating = review.get(field)
            if rating is not None and not 1 <= _finite_number(
                rating, f"{case_id}.human_review.{field}"
            ) <= 5:
                raise ManifestError(f"{case_id}.{field} must be between 1 and 5")
    return payload


def resolve_source(manifest_path: Path, source: str) -> Path:
    candidate = Path(source).expanduser()
    if not candidate.is_absolute():
        candidate = manifest_path.parent / candidate
    return candidate.resolve()


def _axis_speeds(points: list[FocusPoint], attribute: str) -> list[float]:
    speeds: list[float] = []
    ordered = sorted(points, key=lambda point: point.time)
    for previous, current in zip(ordered, ordered[1:]):
        # A hard cut is a new camera shot, not a pan-speed failure.
        if current.scene_cut:
            continue
        elapsed = current.time - previous.time
        if elapsed <= 0:
            continue
        speeds.append(abs(getattr(current, attribute) - getattr(previous, attribute)) / elapsed)
    return speeds


def compute_reframe_metrics(
    analysis: ReframeAnalysis,
    media: dict[str, Any],
) -> dict[str, Any]:
    points = analysis.points
    confidences = [point.confidence for point in points if point.confidence > 0]
    horizontal_speeds = _axis_speeds(points, "focus")
    vertical_speeds = _axis_speeds(points, "vertical_focus")
    edge_points = [point for point in points if point.focus <= 0.02 or point.focus >= 0.98]
    travel = 0.0
    for previous, current in zip(points, points[1:]):
        if current.scene_cut:
            continue
        travel += abs(current.focus - previous.focus)
        travel += abs(current.vertical_focus - previous.vertical_focus)
    return {
        "duration": round(float(media["duration"]), 3),
        "source_width": int(media["width"]),
        "source_height": int(media["height"]),
        "source_aspect_ratio": round(media["width"] / max(media["height"], 1), 4),
        "detector": analysis.detector,
        "detection_ratio": round(float(analysis.detection_ratio), 4),
        "point_count": len(points),
        "average_confidence": round(mean(confidences), 4) if confidences else 0.0,
        "minimum_confidence": round(min(confidences), 4) if confidences else 0.0,
        "max_horizontal_speed_per_second": round(max(horizontal_speeds, default=0.0), 4),
        "max_vertical_speed_per_second": round(max(vertical_speeds, default=0.0), 4),
        "camera_travel": round(travel, 4),
        "edge_hold_ratio": round(len(edge_points) / max(len(points), 1), 4),
        "zoom": round(float(analysis.zoom), 3),
        "scene_cuts": int(analysis.scene_cuts),
        "subject_switches": int(analysis.subject_switches),
        "centered_fallback": not points,
    }


def _check(
    actual: float,
    target: float,
    operator: str,
) -> dict[str, Any]:
    passed = actual >= target if operator == ">=" else actual <= target
    return {
        "actual": round(float(actual), 4),
        "target": round(float(target), 4),
        "operator": operator,
        "passed": passed,
    }


def evaluate_thresholds(
    metrics: dict[str, Any],
    thresholds: dict[str, Any] | None,
    *,
    expect_subject: bool = True,
) -> dict[str, dict[str, Any]]:
    supplied = thresholds or {}
    effective = {**DEFAULT_THRESHOLDS, **supplied} if expect_subject else supplied
    checks: dict[str, dict[str, Any]] = {}
    pairs = (
        ("min_detection_ratio", "detection_ratio", ">="),
        ("min_average_confidence", "average_confidence", ">="),
        ("max_horizontal_speed_per_second", "max_horizontal_speed_per_second", "<="),
        ("max_vertical_speed_per_second", "max_vertical_speed_per_second", "<="),
        ("max_edge_hold_ratio", "edge_hold_ratio", "<="),
        ("max_subject_switches", "subject_switches", "<="),
        ("max_zoom", "zoom", "<="),
        ("max_detection_ratio", "detection_ratio", "<="),
    )
    for threshold_name, metric_name, operator in pairs:
        if threshold_name in effective:
            checks[threshold_name] = _check(
                float(metrics[metric_name]),
                _finite_number(effective[threshold_name], threshold_name),
                operator,
            )
    if not expect_subject:
        checks["uses_centered_fallback"] = {
            "actual": bool(metrics["centered_fallback"]),
            "target": True,
            "operator": "==",
            "passed": bool(metrics["centered_fallback"]),
        }
    return checks


def _clamped_ratio(actual: float, ideal: float) -> float:
    return max(0.0, min(float(actual) / max(float(ideal), 0.0001), 1.0))


def automatic_score(
    metrics: dict[str, Any],
    *,
    expect_subject: bool,
    render: dict[str, Any] | None,
) -> float:
    components: list[tuple[float, float]] = []
    if expect_subject:
        components.extend(
            [
                (25, _clamped_ratio(metrics["detection_ratio"], 0.85)),
                (15, _clamped_ratio(metrics["average_confidence"], 0.7)),
            ]
        )
    else:
        fallback_quality = 1.0 if metrics["centered_fallback"] else max(
            0.0, 1.0 - metrics["detection_ratio"]
        )
        components.append((40, fallback_quality))
    components.extend(
        [
            (15, max(0.0, 1.0 - metrics["max_horizontal_speed_per_second"] / 0.55)),
            (10, max(0.0, 1.0 - metrics["max_vertical_speed_per_second"] / 0.42)),
            (10, max(0.0, 1.0 - metrics["edge_hold_ratio"])),
            (10, max(0.0, 1.0 - metrics["subject_switches"] / 6)),
            (5, 1.0 if metrics["zoom"] <= 1.35 else 0.0),
        ]
    )
    if render is not None:
        render_ok = (
            render.get("width") == 720
            and render.get("height") == 1280
            and render.get("duration_matches") is True
            and render.get("has_video") is True
        )
        components.append((10, 1.0 if render_ok else 0.0))
    earned = sum(weight * value for weight, value in components)
    possible = sum(weight for weight, _ in components)
    return round(earned / max(possible, 1) * 100, 1)


def human_score(review: dict[str, Any] | None) -> float | None:
    if not review:
        return None
    ratings = [float(review[field]) for field in HUMAN_FIELDS if review.get(field) is not None]
    return round(mean(ratings) / 5 * 100, 1) if ratings else None


def _probe_streams(path: Path) -> set[str]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return set()
    try:
        return {stream["codec_type"] for stream in json.loads(result.stdout)["streams"]}
    except (KeyError, TypeError, json.JSONDecodeError):
        return set()


def verify_render(path: Path, expected_duration: float) -> dict[str, Any]:
    media = probe_media(path)
    streams = _probe_streams(path)
    tolerance = max(0.18, expected_duration * 0.02)
    return {
        "path": str(path),
        "width": media["width"],
        "height": media["height"],
        "duration": round(media["duration"], 3),
        "duration_matches": abs(media["duration"] - expected_duration) <= tolerance,
        "has_video": "video" in streams,
        "has_audio": "audio" in streams,
    }


def _review_result(case: dict[str, Any]) -> tuple[float | None, str]:
    review = case.get("human_review") or {}
    return human_score(review), str(review.get("notes", ""))


def evaluate_case(
    case: dict[str, Any],
    manifest_path: Path,
    output_dir: Path,
    *,
    render_enabled: bool,
) -> dict[str, Any]:
    source = resolve_source(manifest_path, str(case["source"]))
    result: dict[str, Any] = {
        "id": case["id"],
        "title": case.get("title") or case["id"],
        "tags": list(case.get("tags") or []),
        "source": str(source),
        "start": float(case.get("start", 0)),
        "end": float(case["end"]),
        "expect_subject": bool(case.get("expect_subject", True)),
    }
    review_score, review_notes = _review_result(case)
    result["human_score"] = review_score
    result["human_notes"] = review_notes
    if not source.is_file():
        result.update(status="error", error="Source video was not found", final_score=0.0)
        return result
    try:
        media = probe_media(source)
        if result["end"] > media["duration"] + 0.05:
            raise ValueError(
                f"Case ends at {result['end']:.1f}s but source is {media['duration']:.1f}s"
            )
        analysis = track_subject(source, result["start"], result["end"])
        metrics = compute_reframe_metrics(analysis, media)
        checks = evaluate_thresholds(
            metrics,
            case.get("thresholds"),
            expect_subject=result["expect_subject"],
        )
        rendered: dict[str, Any] | None = None
        if render_enabled:
            render_path = output_dir / "renders" / f"{case['id']}.mp4"
            cut_clip(
                source,
                render_path,
                result["start"],
                result["end"],
                output_format=OutputFormat.VERTICAL,
                reframe_mode=ReframeMode.AUTO,
                smart_analysis=analysis,
            )
            rendered = verify_render(render_path, result["end"] - result["start"])
            checks["vertical_render"] = {
                "actual": f"{rendered['width']}x{rendered['height']}",
                "target": "720x1280 with matching duration",
                "operator": "==",
                "passed": bool(
                    rendered["width"] == 720
                    and rendered["height"] == 1280
                    and rendered["duration_matches"]
                    and rendered["has_video"]
                ),
            }
        auto_score = automatic_score(
            metrics,
            expect_subject=result["expect_subject"],
            render=rendered,
        )
        final_score = auto_score if review_score is None else round(
            auto_score * 0.65 + review_score * 0.35, 1
        )
        passed = all(check["passed"] for check in checks.values())
        result.update(
            status="passed" if passed else "failed",
            metrics=metrics,
            thresholds={**(case.get("thresholds") or {})},
            checks=checks,
            render=rendered,
            automatic_score=auto_score,
            final_score=final_score,
        )
    except Exception as exc:  # A suite should report one bad case and keep going.
        result.update(status="error", error=str(exc), final_score=0.0)
    return result


def compare_with_baseline(
    cases: list[dict[str, Any]],
    baseline: dict[str, Any] | None,
    *,
    tolerance: float = 3.0,
) -> int:
    if not baseline:
        return 0
    old_cases = {case["id"]: case for case in baseline.get("cases", [])}
    regressions = 0
    for case in cases:
        old = old_cases.get(case["id"])
        if not old:
            continue
        delta = round(float(case.get("final_score", 0)) - float(old.get("final_score", 0)), 1)
        regressed = (
            old.get("status") == "passed" and case.get("status") != "passed"
        ) or delta < -abs(tolerance)
        case["baseline"] = {
            "previous_status": old.get("status"),
            "score_delta": delta,
            "regression": regressed,
        }
        regressions += int(regressed)
    return regressions


def summarize(cases: list[dict[str, Any]], regressions: int) -> dict[str, Any]:
    evaluated = [case for case in cases if case.get("status") != "error"]
    tag_scores: dict[str, list[float]] = {}
    for case in evaluated:
        for tag in case.get("tags", []):
            tag_scores.setdefault(str(tag), []).append(float(case["final_score"]))
    return {
        "total": len(cases),
        "evaluated": len(evaluated),
        "passed": sum(case.get("status") == "passed" for case in cases),
        "failed": sum(case.get("status") == "failed" for case in cases),
        "errors": sum(case.get("status") == "error" for case in cases),
        "average_score": round(mean(case["final_score"] for case in evaluated), 1)
        if evaluated
        else 0.0,
        "regressions": regressions,
        "tag_scores": {
            tag: round(mean(scores), 1) for tag, scores in sorted(tag_scores.items())
        },
    }


def _cell(value: Any) -> str:
    return html.escape(str(value))


def render_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    rows: list[str] = []
    details: list[str] = []
    for case in report["cases"]:
        baseline = case.get("baseline") or {}
        delta = baseline.get("score_delta")
        delta_text = "—" if delta is None else f"{delta:+.1f}"
        tags = ", ".join(case.get("tags") or []) or "—"
        rows.append(
            "<tr>"
            f"<td><strong>{_cell(case['title'])}</strong><small>{_cell(case['id'])}</small></td>"
            f"<td><span class='status {case['status']}'>{_cell(case['status'])}</span></td>"
            f"<td>{_cell(case.get('final_score', 0))}</td>"
            f"<td class='delta'>{_cell(delta_text)}</td>"
            f"<td>{_cell(tags)}</td>"
            "</tr>"
        )
        checks = case.get("checks") or {}
        check_rows = "".join(
            "<li class='check-{state}'>{name}: {actual} {operator} {target}</li>".format(
                state="pass" if check["passed"] else "fail",
                name=_cell(name),
                actual=_cell(check["actual"]),
                operator=_cell(check["operator"]),
                target=_cell(check["target"]),
            )
            for name, check in checks.items()
        )
        error = f"<p class='error'>{_cell(case['error'])}</p>" if case.get("error") else ""
        notes = (
            f"<p><strong>Human notes:</strong> {_cell(case['human_notes'])}</p>"
            if case.get("human_notes")
            else ""
        )
        details.append(
            f"<details><summary>{_cell(case['title'])} · {_cell(case.get('final_score', 0))}/100</summary>"
            f"{error}<ul>{check_rows}</ul>{notes}</details>"
        )
    tags = "".join(
        f"<span>{_cell(tag)} <strong>{_cell(score)}</strong></span>"
        for tag, score in summary["tag_scores"].items()
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clipmaker quality report</title><style>
:root{{--bg:#0d1014;--panel:#171b21;--line:#303741;--text:#f3f1ea;--muted:#9ba4af;--accent:#ffad32;--good:#63d391;--bad:#ff6b6b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}}main{{max-width:1180px;margin:auto;padding:40px 22px 70px}}h1{{margin:0;font-size:clamp(30px,5vw,56px)}}p{{color:var(--muted)}}.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:28px 0}}.card,details,.tag-list{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:17px}}.card strong{{display:block;font-size:29px}}.card span,small{{display:block;color:var(--muted)}}table{{width:100%;border-collapse:collapse;background:var(--panel);border-radius:12px;overflow:hidden}}th,td{{padding:13px;text-align:left;border-bottom:1px solid var(--line)}}th{{color:var(--muted);font-size:12px;text-transform:uppercase}}.status{{padding:4px 9px;border-radius:99px;font-weight:700}}.passed{{color:var(--good);background:#153424}}.failed,.error{{color:var(--bad);background:#3b1e22}}details{{margin-top:11px}}summary{{cursor:pointer;font-weight:700}}li{{margin:7px 0}}.check-pass{{color:var(--good)}}.check-fail{{color:var(--bad)}}.tag-list{{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0}}.tag-list span{{background:#232932;padding:7px 10px;border-radius:8px}}.delta{{font-variant-numeric:tabular-nums}}@media(max-width:760px){{.cards{{grid-template-columns:repeat(2,1fr)}}table{{font-size:12px}}th,td{{padding:8px}}}}
</style></head><body><main>
<p>PRIVATE LOCAL EVALUATION · {_cell(report['generated_at'])}</p><h1>{_cell(report['suite'])}</h1>
<p>Automatic measurements cover tracking and render integrity. Human review covers subject choice, visual quality, captions, and whether clips feel complete.</p>
<section class="cards"><div class="card"><strong>{summary['average_score']}</strong><span>average score</span></div><div class="card"><strong>{summary['passed']}</strong><span>passed</span></div><div class="card"><strong>{summary['failed']}</strong><span>failed</span></div><div class="card"><strong>{summary['errors']}</strong><span>errors</span></div><div class="card"><strong>{summary['regressions']}</strong><span>regressions</span></div></section>
<div class="tag-list">{tags or '<span>No tag results yet</span>'}</div>
<table><thead><tr><th>Case</th><th>Status</th><th>Score</th><th>vs baseline</th><th>Tags</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<section>{''.join(details)}</section>
</main></body></html>"""


def run_suite(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_manifest(manifest_path)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = [
        evaluate_case(
            case,
            manifest_path,
            output_dir,
            render_enabled=args.render,
        )
        for case in manifest["cases"]
    ]
    baseline = None
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    regressions = compare_with_baseline(cases, baseline, tolerance=args.regression_tolerance)
    report = {
        "schema_version": SCHEMA_VERSION,
        "suite": manifest.get("suite") or "Clipmaker quality suite",
        "generated_at": utc_now(),
        "manifest": str(manifest_path),
        "render_enabled": bool(args.render),
        "cases": cases,
        "summary": summarize(cases, regressions),
    }
    json_path = output_dir / "report.json"
    html_path = output_dir / "report.html"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    summary = report["summary"]
    print(
        f"Quality score {summary['average_score']}/100 · "
        f"{summary['passed']} passed · {summary['failed']} failed · "
        f"{summary['errors']} errors · {summary['regressions']} regressions"
    )
    print(f"JSON: {json_path}")
    print(f"HTML: {html_path}")
    if args.fail_on_regression and (regressions or summary["errors"]):
        return 1
    return 0


def init_manifest(args: argparse.Namespace) -> int:
    template = Path(__file__).resolve().parent / "evaluation" / "cases.template.json"
    destination = Path(args.destination).expanduser().resolve()
    if destination.exists() and not args.force:
        raise ManifestError(f"Refusing to overwrite {destination}; pass --force if intended")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, destination)
    print(f"Created private manifest: {destination}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create an ignored local manifest")
    init.add_argument(
        "--destination",
        default="evaluation/cases.local.json",
        help="local manifest path",
    )
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=init_manifest)
    run = commands.add_parser("run", help="run the evaluation suite")
    run.add_argument("--manifest", default="evaluation/cases.local.json")
    run.add_argument("--output-dir", default="evaluation/results/latest")
    run.add_argument("--render", action="store_true", help="render and verify 9:16 outputs")
    run.add_argument("--baseline", help="previous report.json to compare")
    run.add_argument("--regression-tolerance", type=float, default=3.0)
    run.add_argument("--fail-on-regression", action="store_true")
    run.set_defaults(handler=run_suite)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.handler(args))
    except (ManifestError, OSError, json.JSONDecodeError) as exc:
        print(f"Evaluation setup error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
