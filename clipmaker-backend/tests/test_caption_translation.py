"""Translated captions must stay on the speech timeline.

Whisper gives word-level timings for the words actually spoken. A translation
has different words, and usually a different number of them, so word-level sync
cannot survive. What must survive is the phrase: it starts and ends when the
original phrase did, and drift there is immediately visible on screen.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import caption_translator as translator  # noqa: E402
from services.caption_translator import (  # noqa: E402
    group_into_phrases,
    spread_across,
    translate_caption_words,
)


def _words(*specs) -> list[dict]:
    return [{"word": text, "start": start, "end": end} for text, start, end in specs]


class PhraseGroupingTests(unittest.TestCase):
    def test_a_sentence_end_closes_a_phrase(self):
        words = _words(("Hello", 0, .4), ("there.", .4, .9), ("Next", 1.0, 1.4))
        phrases = group_into_phrases(words)
        self.assertEqual(len(phrases), 2)
        self.assertEqual(phrases[0]["text"], "Hello there.")

    def test_a_pause_closes_a_phrase(self):
        words = _words(("one", 0, .3), ("two", .3, .6), ("three", 2.0, 2.4))
        phrases = group_into_phrases(words)
        self.assertEqual(len(phrases), 2)

    def test_a_long_run_without_punctuation_is_still_split(self):
        words = _words(*[(f"w{i}", i * .3, i * .3 + .25) for i in range(30)])
        phrases = group_into_phrases(words)
        self.assertTrue(all(p["word_count"] <= translator.MAX_PHRASE_WORDS for p in phrases))
        self.assertGreater(len(phrases), 1)

    def test_a_hindi_danda_ends_a_sentence(self):
        """Devanagari uses । as a full stop; treating it as a normal character
        would run whole paragraphs into one caption phrase."""
        words = _words(("मैंने", 0, .4), ("कहा।", .4, .9), ("फिर", 1.0, 1.3))
        self.assertEqual(len(group_into_phrases(words)), 2)

    def test_phrase_spans_match_the_words_they_came_from(self):
        words = _words(("a", 0, .5), ("b.", .5, 1.2), ("c", 1.3, 2.0))
        phrases = group_into_phrases(words)
        self.assertEqual(phrases[0]["start"], 0)
        self.assertEqual(phrases[0]["end"], 1.2)


class SpreadTests(unittest.TestCase):
    def test_the_translation_occupies_exactly_the_original_span(self):
        result = spread_across("I said that yesterday", 10.0, 12.0)
        self.assertEqual(result[0]["start"], 10.0)
        self.assertEqual(result[-1]["end"], 12.0)

    def test_words_do_not_overlap_and_stay_ordered(self):
        result = spread_across("one two three four five", 0, 3)
        for earlier, later in zip(result, result[1:]):
            self.assertLessEqual(earlier["end"], later["start"] + 1e-6)
            self.assertLess(earlier["start"], earlier["end"])

    def test_longer_words_hold_the_screen_longer(self):
        """An equal split gives 'a' as much time as 'extraordinary'."""
        result = spread_across("a extraordinary", 0, 4)
        short = result[0]["end"] - result[0]["start"]
        long = result[1]["end"] - result[1]["start"]
        self.assertGreater(long, short)

    def test_empty_text_produces_nothing_rather_than_a_blank_caption(self):
        self.assertEqual(spread_across("   ", 0, 2), [])

    def test_a_very_short_span_still_yields_usable_timings(self):
        result = spread_across("two words", 5.0, 5.02)
        self.assertTrue(all(word["end"] > word["start"] for word in result))


class TranslateTests(unittest.TestCase):
    WORDS = _words(
        ("मैंने", 0, .4), ("कहा।", .4, .9),
        ("फिर", 1.5, 1.8), ("क्या", 1.8, 2.1), ("हुआ।", 2.1, 2.6),
    )

    def test_translated_words_cover_the_original_timeline(self):
        with patch.object(translator, "_translate_batch",
                          return_value={0: "I said that", 1: "then what happened"}):
            result = translate_caption_words(self.WORDS)
        self.assertEqual(result[0]["start"], 0)
        self.assertEqual(result[-1]["end"], 2.6)
        self.assertEqual(" ".join(w["word"] for w in result), "I said that then what happened")

    def test_a_missing_translation_falls_back_to_the_original_phrase(self):
        """Partial results should degrade to mixed captions, never to gaps."""
        with patch.object(translator, "_translate_batch", return_value={0: "I said that"}):
            result = translate_caption_words(self.WORDS)
        text = " ".join(word["word"] for word in result)
        self.assertIn("I said that", text)
        self.assertIn("फिर", text)

    def test_no_speech_returns_nothing_without_calling_the_model(self):
        with patch.object(translator, "_translate_batch") as batch:
            self.assertEqual(translate_caption_words([]), [])
        batch.assert_not_called()

    def test_long_transcripts_are_batched(self):
        words = _words(*[(f"w{i}.", i * 1.0, i * 1.0 + .5) for i in range(150)])
        calls = []

        def fake(phrases, language):
            calls.append(len(phrases))
            return {index: "x" for index in range(len(phrases))}

        with patch.object(translator, "_translate_batch", side_effect=fake):
            translate_caption_words(words)
        self.assertGreater(len(calls), 1, "150 phrases went out as one request")
        self.assertTrue(all(size <= translator.MAX_PHRASES_PER_REQUEST for size in calls))


if __name__ == "__main__":
    unittest.main()
