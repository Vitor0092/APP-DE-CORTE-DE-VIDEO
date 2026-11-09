"""Claude watches the transcript and picks the moments most likely to go viral."""
import json
from typing import List, Literal

import anthropic
from pydantic import BaseModel, Field

from .. import db
from ..config import ANTHROPIC_API_KEY, CLAUDE_MODEL

CONTENT_TYPES = [
    "noticia", "vazamento_rumor", "detalhe_escondido", "comparacao", "teoria",
    "reacao_opiniao", "top_lista", "gameplay_momento", "humor",
]


class ClipIdea(BaseModel):
    start: float = Field(description="Segundo inicial no vídeo original")
    end: float = Field(description="Segundo final no vídeo original")
    content_type: Literal[tuple(CONTENT_TYPES)]  # type: ignore[valid-type]
    title: str = Field(description="Título curto e chamativo para YouTube Shorts (máx 70 caracteres, sem #)")
    hook: str = Field(description="Texto grande na tela nos primeiros 3s (máx 6 palavras, MAIÚSCULAS)")
    voiceover: str = Field(description="Narração própria de 1-2 frases que abre o corte, com opinião/contexto (transforma o conteúdo). Vazio se não fizer sentido.")
    caption_youtube: str
    caption_tiktok: str
    caption_instagram: str
    hashtags: List[str]
    viral_score: float = Field(description="0-100, chance de viralizar")
    reason: str = Field(description="Por que esse trecho pode viralizar (1 frase)")


class Plan(BaseModel):
    clips: List[ClipIdea]


def _client():
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY não configurada no .env")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def pick_clips(title: str, segments: list[dict], duration: float) -> list[dict]:
    n = int(db.setting("clips_per_video", 5))
    mn, mx = int(db.setting("min_clip_seconds", 20)), int(db.setting("max_clip_seconds", 59))
    transcript = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)
    system = (
        "Você é editor-chefe de um canal de cortes que viraliza em YouTube Shorts, TikTok e Reels. "
        f"Nicho atual: {db.setting('niche')}. Estilo do canal: {db.setting('channel_style')} "
        f"Idioma das legendas e textos: {db.setting('language')}. "
        "Escolha trechos com gancho forte nos 2 primeiros segundos, informação nova, polêmica, surpresa ou emoção, "
        "que façam sentido sozinhos. Comece e termine em frases completas. "
        "Os cortes devem ser transformadores: sempre traga contexto ou opinião própria na narração e nas legendas, "
        "nunca apenas reposte o conteúdo. Legendas de post: TikTok curta e provocativa com pergunta para comentários; "
        "Instagram com 2-3 linhas e CTA; YouTube com 1-2 frases. Hashtags sem espaço, relevantes ao nicho."
    )
    user = (
        f"Vídeo: {title}\nDuração: {duration:.0f}s\n"
        f"Gere até {n} cortes entre {mn} e {mx} segundos, sem sobreposição, ordenados por viral_score.\n\n"
        f"Transcrição com tempos:\n{transcript[:150000]}"
    )
    resp = _client().messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "medium"},
        output_format=Plan,
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("A IA recusou analisar este vídeo.")
    clips = []
    for c in resp.parsed_output.clips:
        start, end = max(0.0, c.start), min(duration or c.end, c.end)
        if end - start < 5:
            continue
        d = c.model_dump()
        d.update(start=start, end=end)
        clips.append(d)
    return clips


def rate_trending(videos: list[dict]) -> dict:
    """Score trending videos 0-100 for clip potential, from title/channel/stats only (cheap)."""
    lines = "\n".join(
        f"{v['video_id']} | {v['title']} | {v['channel']} | {v['views']} views | {v['velocity']:.0f}/h" for v in videos
    )

    class Score(BaseModel):
        video_id: str
        score: float

    class Scores(BaseModel):
        scores: List[Score]

    resp = _client().messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        system=f"Você avalia vídeos do nicho {db.setting('niche')} para virar cortes virais. Dê nota 0-100 a cada um.",
        messages=[{"role": "user", "content": lines}],
        output_config={"effort": "low"},
        output_format=Scores,
    )
    if resp.stop_reason == "refusal" or not resp.parsed_output:
        return {}
    return {s.video_id: s.score for s in resp.parsed_output.scores}
