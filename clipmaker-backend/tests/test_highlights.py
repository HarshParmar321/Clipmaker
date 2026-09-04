import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.exceptions import VideoDownloadError
from services.highlight_detector import detect_highlights, recover_highlights, validate_highlights


class HighlightValidationTests(unittest.TestCase):
    @patch("services.highlight_detector.client.chat.completions.create")
    def test_creative_direction_and_generation_settings_reach_the_model(self, create):
        create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"clips": []}'))]
        )
        detect_highlights(
            [{"word": "Advice", "start": 0, "end": 1}],
            "Find practical advice for first-time founders",
            target_clip_count=3,
            min_clip_duration=15,
            max_clip_duration=30,
        )
        request = create.call_args.kwargs
        self.assertEqual(request["model"], "openai/gpt-oss-120b")
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        self.assertEqual(
            request["response_format"]["json_schema"]["schema"]["required"],
            ["clips"],
        )
        user_message = request["messages"][1]["content"]
        self.assertIn("Final clips requested: 3", user_message)
        self.assertIn("Duration: 15-30 seconds", user_message)
        self.assertIn("Find practical advice for first-time founders", user_message)

    @patch("services.highlight_detector.client.chat.completions.create")
    def test_unavailable_primary_model_uses_configured_fallback(self, create):
        primary_error = RuntimeError(
            "model_not_found: The model does not exist or you do not have access to it"
        )
        primary_error.status_code = 404
        create.side_effect = [
            primary_error,
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"clips": []}'))]
            ),
        ]

        self.assertEqual(
            detect_highlights([{"word": "Advice", "start": 0, "end": 1}]),
            [],
        )
        self.assertEqual(
            [request.kwargs["model"] for request in create.call_args_list],
            ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
        )

    @patch("services.highlight_detector.client.chat.completions.create")
    def test_clear_retryable_error_when_all_highlight_models_are_unavailable(self, create):
        unavailable = RuntimeError("model_not_found: The model does not exist")
        unavailable.status_code = 404
        create.side_effect = unavailable

        with self.assertRaises(VideoDownloadError) as raised:
            detect_highlights([{"word": "Advice", "start": 0, "end": 1}])

        self.assertTrue(raised.exception.retryable)
        self.assertIn("AI models used to detect highlights", raised.exception.user_message)
        self.assertIn("openai/gpt-oss-120b", raised.exception.technical_detail)
        self.assertIn("openai/gpt-oss-20b", raised.exception.technical_detail)

    @patch("services.highlight_detector.client.chat.completions.create")
    def test_non_model_api_error_does_not_fall_back(self, create):
        create.side_effect = RuntimeError("connection interrupted")

        with self.assertRaisesRegex(RuntimeError, "connection interrupted"):
            detect_highlights([{"word": "Advice", "start": 0, "end": 1}])
        self.assertEqual(create.call_count, 1)

    def test_rejects_invalid_model_output_and_overlaps(self):
        raw = [
            {"title": "best", "start": 10, "end": 40, "score": 90},
            {"title": "overlap", "start": 20, "end": 45, "score": 50},
            {"title": "negative", "start": -1, "end": 20, "score": 80},
            {"title": "past end", "start": 90, "end": 110, "score": 80},
            {"title": "bad score", "start": 50, "end": 80, "score": 200},
            {"title": "valid second", "start": 50, "end": 80, "score": 85},
        ]
        result = validate_highlights(raw, video_duration=100)
        self.assertEqual([clip["title"] for clip in result], ["best", "valid second"])

    def test_snaps_to_natural_boundaries_and_respects_requested_count(self):
        words = [
            {"word": "First", "start": 0, "end": 4},
            {"word": "thought.", "start": 4, "end": 8},
            {"word": "This", "start": 8.2, "end": 12},
            {"word": "is", "start": 12, "end": 16},
            {"word": "complete.", "start": 16, "end": 24},
            {"word": "Another", "start": 30, "end": 34},
            {"word": "moment", "start": 34, "end": 38},
            {"word": "ends.", "start": 38, "end": 45},
        ]
        raw = [
            {
                "title": "complete idea",
                "start": 12,
                "end": 20,
                "hook_score": 90,
                "relevance_score": 90,
                "completeness_score": 90,
                "shareability_score": 90,
            },
            {
                "title": "second idea",
                "start": 30,
                "end": 45,
                "hook_score": 80,
                "relevance_score": 80,
                "completeness_score": 80,
                "shareability_score": 80,
            },
        ]
        result = validate_highlights(
            raw,
            video_duration=50,
            words=words,
            target_clip_count=1,
            min_clip_duration=15,
            max_clip_duration=30,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "complete idea")
        self.assertEqual(result[0]["start"], 8.2)
        self.assertEqual(result[0]["end"], 24)
        self.assertEqual(result[0]["score"], 90)

    def test_weak_dimension_caps_quality_and_breakdown_is_preserved(self):
        raw = [{
            "title": "Relevant but incomplete",
            "topic": "startup pricing",
            "start": 0,
            "end": 20,
            "hook_score": 95,
            "relevance_score": 95,
            "completeness_score": 50,
            "shareability_score": 90,
        }]
        result = validate_highlights(
            raw,
            video_duration=30,
            target_clip_count=1,
            min_clip_duration=10,
            max_clip_duration=25,
        )
        self.assertEqual(result[0]["score"], 68)
        self.assertEqual(result[0]["score_breakdown"]["completeness"], 50)

    def test_rejects_repeated_topics_at_different_timestamps(self):
        def candidate(title, start, end, topic, score):
            return {
                "title": title,
                "topic": topic,
                "start": start,
                "end": end,
                "hook_score": score,
                "relevance_score": score,
                "completeness_score": score,
                "shareability_score": score,
            }

        raw = [
            candidate("Pricing lesson", 0, 10, "startup pricing mistake", 90),
            candidate("Another pricing lesson", 20, 30, "startup pricing mistake", 85),
            candidate("Hiring lesson", 40, 50, "startup hiring culture", 82),
        ]
        result = validate_highlights(
            raw,
            video_duration=60,
            target_clip_count=3,
            min_clip_duration=5,
            max_clip_duration=15,
        )
        self.assertEqual([clip["title"] for clip in result], ["Pricing lesson", "Hiring lesson"])

    def test_removes_short_leading_disfluency(self):
        words = [
            {"word": "Um", "start": 0, "end": 0.2},
            {"word": "Build", "start": 0.3, "end": 2},
            {"word": "for", "start": 2, "end": 4},
            {"word": "customers.", "start": 4, "end": 10},
        ]
        raw = [{
            "title": "Customer lesson",
            "start": 0,
            "end": 10,
            "hook_score": 85,
            "relevance_score": 85,
            "completeness_score": 85,
            "shareability_score": 85,
        }]
        result = validate_highlights(
            raw,
            video_duration=12,
            words=words,
            target_clip_count=1,
            min_clip_duration=5,
            max_clip_duration=12,
        )
        self.assertEqual(result[0]["start"], 0.3)

    def test_rejects_borderline_model_candidates(self):
        raw = [{
            "title": "Weak fragment",
            "start": 0,
            "end": 10,
            "hook_score": 55,
            "relevance_score": 55,
            "completeness_score": 55,
            "shareability_score": 55,
        }]
        self.assertEqual(
            validate_highlights(raw, 20, target_clip_count=1, min_clip_duration=5, max_clip_duration=15),
            [],
        )

    def test_allows_small_duration_grace_to_finish_the_sentence(self):
        words = [
            {"word": "A", "start": 0, "end": 2},
            {"word": "complete", "start": 2, "end": 7},
            {"word": "thought.", "start": 7, "end": 12},
        ]
        raw = [{
            "title": "Complete thought",
            "start": 0,
            "end": 9,
            "hook_score": 85,
            "relevance_score": 85,
            "completeness_score": 90,
            "shareability_score": 80,
        }]
        result = validate_highlights(
            raw,
            video_duration=20,
            words=words,
            target_clip_count=1,
            min_clip_duration=5,
            max_clip_duration=10,
        )
        self.assertEqual(result[0]["end"], 12)

    def test_rejects_candidate_when_speaker_has_no_clean_ending(self):
        words = [
            {"word": f"word{index}", "start": index * 2, "end": index * 2 + 1.8}
            for index in range(9)
        ]
        raw = [{
            "title": "Interrupted thought",
            "start": 0,
            "end": 8,
            "hook_score": 85,
            "relevance_score": 85,
            "completeness_score": 85,
            "shareability_score": 85,
        }]
        self.assertEqual(
            validate_highlights(
                raw,
                video_duration=20,
                words=words,
                target_clip_count=1,
                min_clip_duration=5,
                max_clip_duration=10,
            ),
            [],
        )

    def test_recovery_uses_relaxed_natural_pause_after_strict_rejection(self):
        words = [
            {"word": "This", "start": 0, "end": 2},
            {"word": "moment", "start": 2, "end": 4},
            {"word": "has", "start": 4, "end": 6},
            {"word": "a", "start": 6, "end": 8},
            {"word": "natural", "start": 8, "end": 10},
            {"word": "pause", "start": 10, "end": 12},
            {"word": "and", "start": 12.4, "end": 14},
            {"word": "finishes", "start": 14, "end": 17},
            {"word": "cleanly", "start": 17, "end": 20},
        ]
        raw = [{
            "title": "Useful recovery",
            "start": 1,
            "end": 17,
            "hook_score": 82,
            "relevance_score": 84,
            "completeness_score": 55,
            "shareability_score": 78,
        }]
        recovered = recover_highlights(
            raw,
            video_duration=22,
            words=words,
            target_clip_count=1,
            min_clip_duration=10,
            max_clip_duration=18,
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["start"], 0)
        self.assertEqual(recovered[0]["end"], 20)
        self.assertLessEqual(recovered[0]["score"], 79)

    def test_recovery_builds_transcript_window_when_model_returns_none(self):
        words = [
            {"word": "A", "start": 0, "end": 1},
            {"word": "complete", "start": 1, "end": 4},
            {"word": "opening.", "start": 4, "end": 8},
            {"word": "Another", "start": 8.4, "end": 11},
            {"word": "useful", "start": 11, "end": 14},
            {"word": "thought.", "start": 14, "end": 18},
        ]
        recovered = recover_highlights(
            [],
            video_duration=20,
            words=words,
            target_clip_count=1,
            min_clip_duration=6,
            max_clip_duration=12,
        )
        self.assertEqual(len(recovered), 1)
        self.assertIn(recovered[0]["start"], {0, 8.4})
        self.assertGreaterEqual(recovered[0]["end"] - recovered[0]["start"], 6)
        self.assertTrue(recovered[0]["title"])


if __name__ == "__main__":
    unittest.main()
