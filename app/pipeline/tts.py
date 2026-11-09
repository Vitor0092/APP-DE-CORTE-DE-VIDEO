"""Voice-over with Microsoft Edge neural voices (free, no key)."""
import asyncio

import edge_tts

from ..config import TTS_VOICE


def speak(text: str, out_path: str, voice: str = TTS_VOICE, rate: str = "+8%") -> str:
    async def _run():
        await edge_tts.Communicate(text, voice, rate=rate).save(out_path)
    asyncio.run(_run())
    return out_path
