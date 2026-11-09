"""Goals progress: posts per day, views/likes per week/month."""
from datetime import datetime, timedelta, timezone

from . import db

PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}
PERIOD_LABEL = {"day": "hoje", "week": "últimos 7 dias", "month": "últimos 30 dias"}
METRIC_LABEL = {"posts": "posts", "views": "visualizações", "likes": "curtidas", "comments": "comentários"}


def progress():
    out = []
    for g in db.q("SELECT * FROM goals ORDER BY id"):
        if g["period"] == "day":
            since = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            since = since.astimezone(timezone.utc).isoformat(timespec="seconds")
        else:
            since = (datetime.now(timezone.utc) - timedelta(days=PERIOD_DAYS[g["period"]])).isoformat(timespec="seconds")
        where, args = "status='posted' AND posted_at>=?", [since]
        if g["platform"] != "all":
            where += " AND platform=?"
            args.append(g["platform"])
        col = "COUNT(*)" if g["metric"] == "posts" else f"COALESCE(SUM({g['metric']}),0)"
        value = db.one(f"SELECT {col} AS v FROM posts WHERE {where}", args)["v"]
        pct = min(100, round(100 * value / g["target"])) if g["target"] else 0
        out.append({**g, "value": value, "pct": pct, "period_label": PERIOD_LABEL[g["period"]],
                    "metric_label": METRIC_LABEL.get(g["metric"], g["metric"])})
    return out


# Content mix the platform aims for during the hype window (shown on the Goals page).
CONTENT_MIX = [
    ("noticia", "Notícias / anúncios oficiais", 25, "Postar em até 2h após o anúncio. Hook: 'SAIU AGORA'."),
    ("vazamento_rumor", "Vazamentos e rumores", 15, "Sempre deixar claro que é rumor. Gera muito comentário."),
    ("detalhe_escondido", "Detalhes escondidos / easter eggs", 20, "Zoom no detalhe + seta. Ótimo para retenção."),
    ("comparacao", "Comparação GTA 5 x GTA 6", 10, "Formato lado a lado, viraliza bem no TikTok."),
    ("teoria", "Teorias da comunidade", 10, "Terminar com pergunta para puxar comentários."),
    ("reacao_opiniao", "Reação / opinião", 10, "Sua narração no começo deixa o corte transformador."),
    ("top_lista", "Top 3 / Top 5", 5, "Contagem regressiva segura a pessoa até o fim."),
    ("gameplay_momento", "Momentos de gameplay", 5, "Assim que houver gameplay oficial ou de criadores."),
]
