"""Translates caption words while keeping them on the video's timeline.

Whisper transcribes in whatever language is spoken, which is the right default:
Hindi audio should get Hindi captions, and the word-level timings that drive the
karaoke highlight are only correct for the words that were actually said.

Translating breaks that one-to-one relationship. Groq's audio translation
endpoint returns English text but no word timings, and even if it did, "मैंने
कहा" and "I said" do not have matching word counts, so there is nothing to line
up. What survives is phrase timing: a phrase starts and ends when the original
phrase did, and the translated words inside it are spread across that span.

So this works in phrases, not words:
  1. group the original words into short phrases at natural boundaries
  2. translate the phrases in one request, as a batch, so the model has context
  3. redistribute each translated phrase's words across the original span

The result highlights phrase-by-phrase rather than word-by-word. That is a real
downgrade from the original-language captions, and it is why "original" is the
default rather than a setting nobody chose.
"""
from __future__ import annotations

import json
import logging
import re

from groq import Groq

from config import GROQ_API_KEY
from services.exceptions import VideoDownloadError
from services.highlight_detector import _model_chain, _raw_text, _short

logger = logging.getLogger("clipmaker.captions")

client = Groq(api_key=GROQ_API_KEY)

# Long enough that the model has something to work with, short enough that the
# timing spread inside a phrase stays believable.
MAX_PHRASE_WORDS = 9
PHRASE_GAP_SECONDS = 0.45
MAX_PHRASES_PER_REQUEST = 60

_SENTENCE_END = re.compile(r"[.!?।][\"')\]]*$")

_SYSTEM_PROMPT = """You translate short caption phrases for social video subtitles.

Rules:
- Translate every phrase into natural, spoken {language}. Not literal, not formal.
- Keep each translation roughly as long as the original phrase. These are
  subtitles on a timeline; a phrase that doubles in length will not fit.
- Preserve names, brands, numbers and units exactly as they appear.
- If a phrase is already in {language}, return it unchanged.
- Never merge or split phrases. Return exactly one translation per input, in the
  same order, with the same ids.
- Return no commentary, only the JSON object."""

_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "caption_translations",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "phrases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "text": {"type": "string"},
                        },
                        "required": ["id", "text"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["phrases"],
            "additionalProperties": False,
        },
    },
}


def _text_of(word: dict) -> str:
    return str(word.get("word") or "").strip()


def group_into_phrases(words: list[dict]) -> list[dict]:
    """Split words at sentence ends and speech pauses.

    Phrase boundaries are where translation is safest to apply: within one, word
    order can change freely without the caption drifting off the speech.
    """
    phrases: list[dict] = []
    current: list[dict] = []

    for index, word in enumerate(words):
        current.append(word)
        is_last = index == len(words) - 1
        gap_follows = not is_last and (
            float(words[index + 1]["start"]) - float(word["end"]) >= PHRASE_GAP_SECONDS
        )
        if (
            is_last
            or gap_follows
            or _SENTENCE_END.search(_text_of(word))
            or len(current) >= MAX_PHRASE_WORDS
        ):
            phrases.append(
                {
                    "start": float(current[0]["start"]),
                    "end": float(current[-1]["end"]),
                    "text": " ".join(_text_of(item) for item in current if _text_of(item)),
                    "word_count": len(current),
                }
            )
            current = []
    return [phrase for phrase in phrases if phrase["text"]]


def spread_across(text: str, start: float, end: float) -> list[dict]:
    """Lay a translated phrase back onto the span the original occupied.

    Words get time in proportion to their length rather than an equal share —
    "extraordinary" holding the screen as long as "a" reads badly.
    """
    tokens = [token for token in text.split() if token]
    if not tokens:
        return []
    span = max(float(end) - float(start), 0.05)
    weights = [max(len(token), 1) for token in tokens]
    total = sum(weights)

    result: list[dict] = []
    cursor = float(start)
    for index, token in enumerate(tokens):
        share = span * weights[index] / total
        word_end = end if index == len(tokens) - 1 else cursor + share
        result.append(
            {
                "word": token,
                "start": round(cursor, 3),
                "end": round(max(word_end, cursor + 0.04), 3),
            }
        )
        cursor = result[-1]["end"]
    return result


def _translate_batch(phrases: list[dict], language: str) -> dict[int, str]:
    payload = json.dumps(
        [{"id": index, "text": phrase["text"]} for index, phrase in enumerate(phrases)],
        ensure_ascii=False,
    )
    system_prompt = _SYSTEM_PROMPT.format(language=language)
    user_message = f"Translate these caption phrases into {language}:\n{payload}"

    errors: list[str] = []
    for model in _model_chain():
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                response_format=_SCHEMA,
                temperature=0.2,
                max_completion_tokens=8000,
            )
            parsed = json.loads(_raw_text(response))
            translations = {
                int(item["id"]): str(item["text"]).strip()
                for item in parsed.get("phrases", [])
                if str(item.get("text") or "").strip()
            }
            if translations:
                logger.info(
                    "Translated %d/%d caption phrases with %s",
                    len(translations), len(phrases), model,
                )
                return translations
            errors.append(f"{model}: returned no phrases")
        except Exception as exc:
            errors.append(f"{model}: {_short(exc)}")
            logger.warning("Caption translation with %s failed: %s", model, _short(exc))

    raise VideoDownloadError(
        "We couldn't translate these captions right now. The original-language "
        "captions are still available.",
        technical_detail="; ".join(errors)[:600],
        retryable=True,
    )


def translate_caption_words(words: list[dict], language: str = "English") -> list[dict]:
    """Return caption words in `language`, still aligned to the original speech.

    Falls back to the untranslated phrase rather than dropping it, so a partial
    translation degrades into mixed captions instead of missing ones.
    """
    if not words:
        return []

    phrases = group_into_phrases(words)
    if not phrases:
        return []

    translations: dict[int, str] = {}
    for offset in range(0, len(phrases), MAX_PHRASES_PER_REQUEST):
        batch = phrases[offset:offset + MAX_PHRASES_PER_REQUEST]
        for index, text in _translate_batch(batch, language).items():
            if 0 <= index < len(batch):
                translations[offset + index] = text

    translated: list[dict] = []
    for index, phrase in enumerate(phrases):
        text = translations.get(index) or phrase["text"]
        translated.extend(spread_across(text, phrase["start"], phrase["end"]))
    return translated
