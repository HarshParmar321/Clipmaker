import json
import tempfile
import unittest
from pathlib import Path

from quality_eval import (
    ManifestError,
    automatic_score,
    compare_with_baseline,
    compute_reframe_metrics,
    evaluate_thresholds,
    human_score,
    load_manifest,
    render_html,
    summarize,
)
from services.face_tracker import FocusPoint, ReframeAnalysis


class QualityEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.analysis = ReframeAnalysis(
            points=[
                FocusPoint(0.0, 0.25, 0.8, 0.35),
                FocusPoint(1.0, 0.35, 0.7, 0.38),
                # This large movement is a scene cut and must not count as a pan.
                FocusPoint(2.0, 0.95, 0.75, 0.7, scene_cut=True),
                FocusPoint(3.0, 0.85, 0.85, 0.65),
            ],
            zoom=1.1,
            detection_ratio=0.9,
            detector="yunet",
            scene_cuts=1,
            subject_switches=1,
        )
        self.metrics = compute_reframe_metrics(
            self.analysis,
            {"duration": 4, "width": 1920, "height": 1080},
        )

    def test_metrics_ignore_scene_cut_jump_for_speed(self):
        self.assertEqual(self.metrics["max_horizontal_speed_per_second"], 0.1)
        self.assertEqual(self.metrics["max_vertical_speed_per_second"], 0.05)
        self.assertEqual(self.metrics["point_count"], 4)
        self.assertEqual(self.metrics["detector"], "yunet")

    def test_thresholds_explain_each_failure(self):
        checks = evaluate_thresholds(
            self.metrics,
            {
                "min_detection_ratio": 0.95,
                "max_horizontal_speed_per_second": 0.2,
                "max_subject_switches": 0,
            },
        )
        self.assertFalse(checks["min_detection_ratio"]["passed"])
        self.assertTrue(checks["max_horizontal_speed_per_second"]["passed"])
        self.assertFalse(checks["max_subject_switches"]["passed"])

    def test_no_face_case_requires_centered_fallback(self):
        metrics = dict(self.metrics, centered_fallback=True, detection_ratio=0.05)
        checks = evaluate_thresholds(
            metrics,
            {"max_detection_ratio": 0.2},
            expect_subject=False,
        )
        self.assertTrue(all(check["passed"] for check in checks.values()))

    def test_scores_are_bounded_and_human_score_is_optional(self):
        score = automatic_score(self.metrics, expect_subject=True, render=None)
        self.assertGreater(score, 0)
        self.assertLessEqual(score, 100)
        self.assertIsNone(human_score({}))
        self.assertEqual(human_score({"composition": 4, "overall": 5}), 90.0)

    def test_baseline_marks_pass_to_fail_and_large_drop(self):
        current = [{"id": "core", "status": "failed", "final_score": 88}]
        baseline = {"cases": [{"id": "core", "status": "passed", "final_score": 95}]}
        count = compare_with_baseline(current, baseline, tolerance=3)
        self.assertEqual(count, 1)
        self.assertTrue(current[0]["baseline"]["regression"])
        self.assertEqual(current[0]["baseline"]["score_delta"], -7.0)

    def test_manifest_rejects_unsafe_case_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(
                json.dumps(
                    {
                        "cases": [
                            {"id": "../escape", "source": "video.mp4", "start": 0, "end": 2}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ManifestError):
                load_manifest(path)

    def test_html_escapes_user_supplied_text(self):
        cases = [
            {
                "id": "safe",
                "title": "<script>alert(1)</script>",
                "tags": ["core"],
                "status": "passed",
                "final_score": 90,
                "human_notes": "<b>private</b>",
                "checks": {},
            }
        ]
        report = {
            "suite": "Quality <suite>",
            "generated_at": "now",
            "cases": cases,
            "summary": summarize(cases, 0),
        }
        rendered = render_html(report)
        self.assertNotIn("<script>alert", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;b&gt;private", rendered)


if __name__ == "__main__":
    unittest.main()
