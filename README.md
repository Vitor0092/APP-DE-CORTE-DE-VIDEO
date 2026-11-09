# ⚡ HypeCut

Plataforma pessoal que transforma vídeos em cortes virais e posta sozinha no YouTube Shorts, TikTok e Instagram Reels.
Foco inicial: **GTA 6**.

## O que ela faz

1. **Você cola um link** (YouTube, TikTok, Instagram, X, Twitch…) no Painel.
2. Baixa o vídeo (yt-dlp) → transcreve com tempos por palavra (Whisper local) →
   **a IA (Claude) escolhe os momentos com mais chance de viralizar** e escreve título, gancho, narração, legendas para cada rede e hashtags.
3. **Edita cada corte**: formato vertical 9:16 com fundo desfocado, legenda animada palavra por palavra, texto de gancho no topo, **narração com voz de IA** (edge-tts) abaixando o áudio original.
4. **Aprovação**: por padrão cada corte espera seu OK em *Cortes*. Você pode editar textos/tempos (o vídeo é re-editado), aprovar, postar agora ou rejeitar. Desligue "Exigir aprovação" em *Ajustes* para postar 100% automático.
5. **Agenda e posta** nos horários definidos, pelas APIs oficiais de cada rede.
6. **Descobrir**: a cada X minutos busca vídeos recentes de GTA 6 no YouTube, mede views/hora, a IA dá nota e — se você ligar — já cria os cortes sozinha.
7. **Metas**: posts por dia por rede, views/curtidas por semana e mês, e o mix ideal de tipos de post (notícia, vazamento, detalhe escondido, comparação, teoria…). Métricas atualizadas a cada 3h.

## Como rodar

```bash
cp .env.example .env      # preencha senha e chaves
docker compose up -d      # abre em http://localhost:8000
```
Sem Docker: `./run.sh` (precisa de Python 3.10+ e ffmpeg).

Para postar no Instagram e para os logins OAuth funcionarem fora do seu PC, rode num servidor com HTTPS
(ex.: VPS + Caddy, Railway, Render) e coloque essa URL em `PUBLIC_BASE_URL`.

## Chaves e contas necessárias

| O quê | Onde conseguir | Para quê |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | Escolher momentos virais e escrever textos |
| `YOUTUBE_API_KEY` | Google Cloud → APIs → YouTube Data API v3 → Credenciais | Descobrir vídeos em alta |
| `GOOGLE_CLIENT_ID/SECRET` | Google Cloud → OAuth (app Web). Redirect: `PUBLIC_BASE_URL/oauth/youtube/callback` | Postar Shorts |
| `TIKTOK_CLIENT_KEY/SECRET` | developers.tiktok.com → app com Login Kit + Content Posting API. Redirect: `/oauth/tiktok/callback` | Postar no TikTok |
| `INSTAGRAM_APP_ID/SECRET` | developers.facebook.com → app com "Instagram API with Instagram Login". Redirect: `/oauth/instagram/callback`. Conta precisa ser Profissional (Business/Creator) | Postar Reels |

Depois de preencher, vá em **Contas** e clique em Conectar em cada rede.

### Limites das plataformas (importante)
- **YouTube**: a cota padrão da API (10.000 unidades/dia) permite ~6 uploads/dia. Peça aumento de cota no Google Cloud se precisar de mais. Apps OAuth não verificados podem ter vídeos marcados como privados até a verificação do Google.
- **TikTok**: enquanto o app não passa na auditoria do TikTok, os posts saem como **privados (só você vê)**. Depois da auditoria saem públicos.
- **Instagram**: limite de 50 posts por API a cada 24h; o vídeo precisa estar acessível em `PUBLIC_BASE_URL`.

## Direitos autorais
Repostar vídeo de outros criadores sem transformar pode gerar **strike** e desmonetização. A plataforma foi feita para gerar conteúdo transformador
(narração com opinião própria, contexto, texto, edição). Prefira material oficial (trailers da Rockstar), use a narração e mantenha a aprovação manual ligada no começo.

## Estrutura
```
app/main.py            painel web + login + OAuth
app/worker.py          fila: processa vídeos, agenda/posta, descoberta, métricas
app/pipeline/          download, transcrição, IA, voz, edição ffmpeg
app/publishers/        youtube, tiktok, instagram (APIs oficiais)
app/discovery.py       vídeos em alta
app/goals.py           metas e mix de conteúdo
```
