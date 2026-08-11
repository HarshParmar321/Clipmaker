"""
Feeds the timestamped transcript to Groq LLaMA 3.3 70B and asks it to pick
the best short-form clip-worthy segments. Same structured-JSON-output
pattern as the Mode A-F classification in the RAG bot.
"""
import json
from groq import Groq
from config import GROQ_API_KEY
from services.transcriber import words_to_plain_transcript

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """You are an expert short-form video editor who finds the most
engaging, self-contained moments in long videos for platforms like Instagram
Reels, YouTube Shorts, and TikTok.

Given a timestamped transcript, identify the best 5-8 clip-worthy segments.

Rules:
- Each clip should be 20-90 seconds long.
- Each clip must be self-contained (makes sense without the rest of the video).
- Prioritize: strong hooks, emotional moments, surprising claims, punchlines,
  actionable advice, or controversial/debate-worthy statements.
- Do not let clips overlap.
- Use the exact timestamps given in the transcript (in seconds).

Respond with ONLY valid JSON, no markdown, no preamble, in this exact shape:
{
  "clips": [
    {"title": "short punchy title", "start": 12.4, "end": 58.9, "score": 88, "reason": "one line on why this works"}
  ]
}
"""


def detect_highlights(words: list[dict]) -> list[dict]:
    transcript = words_to_plain_transcript(words)

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcript:\n\n{transcript}"},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    try:
        parsed = json.loads(raw)
        return parsed.get("clips", [])
    except json.JSONDecodeError:
        # LLM occasionally wraps in markdown fences despite instructions - strip and retry
        cleaned = raw.strip().strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        parsed = json.loads(cleaned)
        return parsed.get("clips", [])
