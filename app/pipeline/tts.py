"""Voice-over with Microsoft Edge neural voices (free, no key)."""
import asyncio

import edge_tts

from ..config import TTS_VOICE


def speak(text: str, out_path: str, voice: str = TTS_VOICE, rate: str = "+8%") -> list[dict]:
    """Writes the mp3 and returns word timings [{start, end, word}] in seconds (empty if unavailable)."""
    words: list[dict] = []

    async def _run():
        comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
        with open(out_path, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append({"start": start, "end": start + chunk["duration"] / 1e7, "word": chunk["text"]})

    asyncio.run(_run())
    return words
