"""Claude studies an example video and writes an original one: script, narration, scene choice and captions."""
import base64
from typing import List, Literal

from pydantic import BaseModel, Field

from .. import db
from ..config import CLAUDE_MODEL
from .analyze import CONTENT_TYPES, _client

WORDS_PER_SECOND = 2.6  # Brazilian neural voice at +8% speed


class Scene(BaseModel):
    narration: str = Field(description="O que a voz fala nesta cena: 1 frase curta e natural, falada")
    on_screen_text: str = Field(description="Texto curto em destaque na tela (máx 5 palavras, MAIÚSCULAS) ou vazio")
    shot_id: int = Field(description="Número da imagem (cena) mostrada enquanto essa fala toca")


class VideoPlan(BaseModel):
    example_analysis: str = Field(description="2-3 frases: o que faz o vídeo de exemplo funcionar "
                                              "(gancho, ritmo, estrutura, tom) e como o seu vídeo usa isso")
    content_type: Literal[tuple(CONTENT_TYPES)]  # type: ignore[valid-type]
    title: str = Field(description="Título curto e chamativo (máx 70 caracteres, sem #)")
    hook: str = Field(description="Texto grande na tela nos primeiros segundos (máx 6 palavras, MAIÚSCULAS)")
    scenes: List[Scene]
    caption_youtube: str
    caption_tiktok: str
    caption_instagram: str
    hashtags: List[str]
    viral_score: float = Field(description="0-100, chance de viralizar")


def _img(path: str) -> dict:
    data = base64.standard_b64encode(open(path, "rb").read()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def write_video(example: dict, segments: list, shots: list, sheets: list, style_sheet: str,
                topic: str, seconds: int) -> dict:
    words = int(seconds * WORDS_PER_SECOND)
    transcript = "\n".join(f"[{_mmss(s['start'])}] {s['text']}" for s in segments)[:12000]
    system = (
        "Você é roteirista e editor de um canal que viraliza em YouTube Shorts, TikTok e Reels. "
        f"Nicho: {db.setting('niche')}. Estilo do canal: {db.setting('channel_style')} "
        f"Idioma: {db.setting('language')}. "
        "Você recebe um vídeo de exemplo e cria um vídeo NOVO e PRÓPRIO inspirado nele: mesmo formato, ritmo e "
        "energia, mas roteiro, frases e opiniões suas. Nunca copie frases do exemplo. "
        "Não invente fatos, datas, números ou anúncios: use só o que está no exemplo, no tema pedido ou o que é "
        "conhecimento certo; especulação deve ser dita como teoria ou opinião. "
        "A primeira cena é um gancho forte (pergunta, revelação ou polêmica) e a última chama para comentar. "
        "Escreva como se fala: frases curtas, diretas, sem emojis. "
        "Para cada fala escolha a imagem que melhor combina com ela; evite repetir imagens."
    )
    intro = (
        f"VÍDEO DE EXEMPLO: {example.get('title') or 'sem título'} ({example.get('duration') or 0:.0f}s)\n"
        + (f"Descrição: {example['description'][:1000]}\n" if example.get("description") else "")
        + (f"Transcrição do exemplo:\n{transcript}\n" if transcript.strip() else "O exemplo não tem fala.\n")
        + f"\nTEMA DO MEU VÍDEO: {topic.strip() or 'o mesmo assunto do exemplo, com abordagem própria'}\n"
        + f"DURAÇÃO: {seconds}s. A narração inteira deve ter NO MÁXIMO {words} palavras, em cerca de "
          f"{max(4, round(seconds / 3.5))} cenas de 1 frase curta (6 a 12 palavras cada).\n"
    )
    content = [{"type": "text", "text": intro}]
    if style_sheet:
        content += [{"type": "text", "text": "Quadros do vídeo de exemplo (para entender o visual):"}, _img(style_sheet)]
    listing = "\n".join(f"{s['id']}: {s['name']} em {_mmss(s['t'])}" for s in shots)
    content.append({"type": "text", "text": "IMAGENS DISPONÍVEIS (o número está no canto de cada imagem):\n" + listing})
    per = 24
    for i, sheet in enumerate(sheets):
        content += [{"type": "text", "text": f"Imagens {i * per + 1} a {min((i + 1) * per, len(shots))}:"},
                    _img(sheet)]
    content.append({"type": "text", "text": "Agora escreva o roteiro do meu vídeo."})
    resp = _client().messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "medium"},
        output_format=VideoPlan,
    )
    if resp.stop_reason == "refusal" or not resp.parsed_output:
        raise RuntimeError("A IA recusou criar este vídeo.")
    plan = resp.parsed_output.model_dump()
    scenes = [s for s in plan["scenes"] if s["narration"].strip()]
    if not scenes:
        raise RuntimeError("A IA não escreveu nenhuma cena. Tente de novo.")
    # Too long for the chosen duration: drop lines before the closing one, keeping the hook and the ending.
    while len(scenes) > 3 and sum(len(s["narration"].split()) for s in scenes) > words * 1.3:
        scenes.pop(-2)
    plan["scenes"] = scenes
    return plan
