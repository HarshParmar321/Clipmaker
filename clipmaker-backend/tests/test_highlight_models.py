"""Regression tests for the highlight model chain.

These cover the failure modes that actually took the pipeline down in
production: a retired model, a reasoning model returning empty content,
Groq rejecting json_schema, and a prompt strict enough that every model
answers with an empty list.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import highlight_detector as hd
from services.exceptions import VideoDownloadError


class FakeGroqError(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        super().__init__(f"Error code: {status_code} - {message}")


def json_validate_failed():
    return FakeGroqError(400, "{'code': 'json_validate_failed', 'failed_generation': ''}")


def model_not_found(model):
    return FakeGroqError(404, f"The model `{model}` does not exist or you do not have access")


CLIP = {
    "title": "t", "topic": "x", "start": 1.0, "end": 9.0,
    "hook_score": 80, "relevance_score": 80,
    "completeness_score": 80, "shareability_score": 80, "reason": "r",
}
WORDS = [{"word": f"w{i}", "start": i * 0.4, "end": i * 0.4 + 0.3} for i in range(200)]


def reply(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")]
    )


class ModelChainTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def run_with(self, handler, chain=("model-a", "model-b")):
        def create(**kwargs):
            self.calls.append(kwargs)
            return handler(**kwargs)

        with patch.object(hd, "_model_chain", return_value=list(chain)), \
             patch.object(hd, "client", SimpleNamespace(
                 chat=SimpleNamespace(completions=SimpleNamespace(create=create)))):
            return hd.detect_highlights(WORDS)

    def test_happy_path(self):
        clips = self.run_with(lambda **kw: reply('{"clips": [%s]}' % _json(CLIP)))
        self.assertEqual(len(clips), 1)
        self.assertEqual(len(self.calls), 1)

    def test_retired_model_falls_through_to_the_next(self):
        def handler(**kw):
            if kw["model"] == "model-a":
                raise model_not_found("model-a")
            return reply('{"clips": [%s]}' % _json(CLIP))

        clips = self.run_with(handler)
        self.assertEqual(len(clips), 1)
        self.assertEqual(self.calls[-1]["model"], "model-b")

    def test_json_validate_failed_relaxes_to_no_response_format(self):
        """The exact production failure: json_schema and json_object both rejected."""
        def handler(**kw):
            if "response_format" in kw:
                raise json_validate_failed()
            return reply('{"clips": [%s]}' % _json(CLIP))

        clips = self.run_with(handler, chain=("model-a",))
        self.assertEqual(len(clips), 1)
        formats = [kw.get("response_format", {}).get("type") for kw in self.calls]
        self.assertEqual(formats, ["json_schema", "json_object", None])

    def test_empty_content_moves_on_instead_of_crashing(self):
        with self.assertRaises(VideoDownloadError) as ctx:
            self.run_with(lambda **kw: reply(""))
        self.assertIn("model-a", str(ctx.exception))
        self.assertIn("model-b", str(ctx.exception))

    def test_zero_candidates_is_reported_per_model(self):
        with self.assertRaises(VideoDownloadError) as ctx:
            self.run_with(lambda **kw: reply('{"clips": []}'))
        self.assertIn("zero candidates", str(ctx.exception))

    def test_rate_limit_is_not_mistaken_for_a_format_problem(self):
        with self.assertRaises(VideoDownloadError) as ctx:
            self.run_with(lambda **kw: (_ for _ in ()).throw(FakeGroqError(429, "Rate limit reached")))
        self.assertIn("429", str(ctx.exception))

    def test_unsupported_parameters_are_dropped_not_fatal(self):
        def handler(**kw):
            if "reasoning_effort" in kw:
                raise FakeGroqError(400, "unsupported parameter: 'reasoning_effort'")
            return reply('{"clips": [%s]}' % _json(CLIP))

        with patch.object(hd, "_reasoning_effort_for", return_value="low"):
            clips = self.run_with(handler, chain=("model-a",))
        self.assertEqual(len(clips), 1)

    def test_short_transcript_fails_fast_with_a_clear_message(self):
        with self.assertRaises(VideoDownloadError) as ctx:
            hd.detect_highlights(WORDS[:10])
        self.assertIn("transcribed words", ctx.exception.user_message)


class ParsingTests(unittest.TestCase):
    def test_tolerates_fences_prose_and_bare_lists(self):
        cases = [
            '{"clips": [%s]}' % _json(CLIP),
            "```json\n" + '{"clips": [%s]}' % _json(CLIP) + "\n```",
            "[%s]" % _json(CLIP),
            '{"highlights": [%s]}' % _json(CLIP),
            "Here you go:\n" + '{"clips": [%s]}' % _json(CLIP),
        ]
        for raw in cases:
            with self.subTest(raw=raw[:30]):
                self.assertEqual(len(hd._parse_highlight_clips(reply(raw))), 1)

    def test_rejects_empty_and_unparseable(self):
        for raw in ("", "   ", None, "I cannot help with that."):
            with self.subTest(raw=raw):
                with self.assertRaises(VideoDownloadError):
                    hd._parse_highlight_clips(reply(raw))


class PromptTests(unittest.TestCase):
    def test_gaming_mode_waives_the_complete_sentence_rule(self):
        gaming = hd._system_prompt("gaming")
        general = hd._system_prompt("general")
        self.assertIn("clutch", gaming)
        self.assertNotIn("clutch", general)
        self.assertIn("complete thought", general)

    def test_scoring_is_never_used_as_a_filter(self):
        """validate_highlights owns the quality bar; the model must not withhold."""
        for mode in ("general", "gaming"):
            prompt = hd._system_prompt(mode)
            self.assertNotIn("below 60 should not be returned", prompt)
            self.assertIn("Never return an empty list", prompt)


def _json(clip):
    import json
    return json.dumps(clip)


if __name__ == "__main__":
    unittest.main()
