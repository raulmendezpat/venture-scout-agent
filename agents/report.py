"""Stage 4: Spanish report as Markdown (committed under reports/) and HTML (email body)."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

import markdown

from .common import env

TREND_ES = {"new": "nueva", "rising": "subiendo", "stable": "estable", "falling": "bajando"}
CONFLICT_ES = {"none": "ninguno", "low": "bajo", "medium": "medio", "high": "alto"}


def _cell(s) -> str:
    return str(s).replace("|", "/").replace("\n", " ")


def _spark(history: list) -> str:
    """Score history scaled between its own minimum and maximum, then first and last value."""
    bars = "▁▂▃▄▅▆▇█"
    vals = [v for _, v in history][-8:]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    line = "".join(bars[3] if hi - lo < 1 else bars[min(7, int(7 * (v - lo) / (hi - lo)))] for v in vals)
    return f"{line} ({vals[0]} a {vals[-1]})" if len(vals) > 1 else line


def _bars(vals: list) -> str:
    """Sparkline scaled to the series maximum; a dot marks a week with no data."""
    bars = "▁▂▃▄▅▆▇█"
    nums = [v for v in vals if v is not None]
    top = max(nums) if nums else 0
    if not top:
        return "".join("·" if v is None else "▁" for v in vals)
    return "".join("·" if v is None else bars[min(7, int(7 * v / top))] for v in vals)


def _sent_bars(vals: list) -> str:
    """Sentiment from -1 to 1 as a sparkline (▁ very negative, █ very positive)."""
    bars = "▁▂▃▄▅▆▇█"
    return "".join("·" if v is None else bars[min(7, int((v + 1) * 4))] for v in vals)


def _arrow(slope: float, eps: float = 0.005) -> str:
    return "sube" if slope > eps else "baja" if slope < -eps else "estable"


def history_section(h: dict) -> list[str]:
    if not h or not h.get("since"):
        return []
    w0, w1 = h["weeks"][0], h["weeks"][-1]
    L = ["## Análisis histórico", "",
         f"Datos desde {h['since']}, {h['runs']} ejecuciones anteriores. Series semanales de {w0} a {w1} "
         f"(una barra por semana; · = sin datos). Elementos por semana: {_bars(h['items_per_week'])} "
         f"({', '.join(map(str, h['items_per_week']))}).", ""]
    if h["persistent_terms"]:
        L += ["**Términos persistentes** (aparecen semana tras semana: señal más sólida que un pico)", "",
              "| Término | Semanas presente | Total | Evolución | Participación |", "|---|---|---|---|---|"]
        L += [f"| {_cell(r['term'])} | {r['weeks_present']}/{len(h['weeks'])} | {r['total']} | "
              f"{_bars(r['weekly'])} | {_arrow(r['share_slope'])} |" for r in h["persistent_terms"]] + [""]
    if h["gaining_terms"] or h["fading_terms"]:
        L += ["**Ganando y perdiendo participación** (pendiente de su peso semanal)", "",
              "| Término | Evolución | Total | Dirección |", "|---|---|---|---|"]
        L += [f"| {_cell(r['term'])} | {_bars(r['weekly'])} | {r['total']} | sube |" for r in h["gaining_terms"][:6]]
        L += [f"| {_cell(r['term'])} | {_bars(r['weekly'])} | {r['total']} | baja |" for r in h["fading_terms"][:6]]
        L += [""]
    L += ["**Ocurrencias acumuladas (todo el historial)**", "",
          "Etiquetas: " + ", ".join(f"{_cell(t)} ({c})" for t, c in h["cumulative_tags"]), "",
          "Frases: " + ", ".join(f"{_cell(t)} ({c})" for t, c in h["cumulative_phrases"]), ""]
    if h["theme_sentiment"]:
        L += ["**Sentimiento por tema en el tiempo** (de -1 a 1)", "",
              "| Tema | Menciones | Semanas | Promedio | Evolución | Tendencia |", "|---|---|---|---|---|---|"]
        L += [f"| {_cell(t['theme'])} | {t['mentions']} | {t['weeks_present']} | {t['avg_sentiment']} | "
              f"{_sent_bars(t['weekly_sentiment'])} | {_arrow(t['sentiment_slope'], 0.02)} |"
              for t in h["theme_sentiment"]] + [""]
    if h["recurring_people"]:
        L += ["**Personas que aparecen de forma recurrente**", "",
              "| Persona | Rol | Organización | Apariciones | Primera vez | Última vez |", "|---|---|---|---|---|---|"]
        for p in h["recurring_people"]:
            name = f"[{_cell(p['name'])}]({p['url']})" if p.get("url") else _cell(p["name"])
            L.append(f"| {name} | {_cell(p['role'])} | {_cell(p['org'])} | {p['mentions']} | "
                     f"{p['first_seen']} | {p['last_seen']} |")
        L += [""]
    return L


def build_markdown(today: str, ideas: dict, people: dict, trends: dict, ledger: list[dict], strategy: dict | None,
                   stats: dict) -> str:
    L = [f"# Venture Scout, {today}", ""]
    L += [f"Fuentes revisadas: {stats['groups']} grupos; elementos nuevos: {stats['new']} (de {stats['seen']} "
          f"encontrados). Ventana de tendencias: {trends['window']['current'][0]} a {trends['window']['current'][1]}, "
          f"{trends['window']['items']} elementos, {int(trends['window']['ai_share'] * 100)}% sobre IA. "
          f"Costo estimado: AUD {stats['cost_aud']:.2f}.", ""]

    if strategy:
        L += ["## Revisión estratégica semanal", "", f"**{strategy['headline']}**", ""]
        for b in strategy["top_bets"]:
            L.append(f"- **{b['idea']}**: {b['why']} *Criterio para descartarla:* {b['kill_criteria']}")
        L += ["", "**Plan de 30 días**", ""] + [f"{n}. {s}" for n, s in enumerate(strategy["thirty_day_plan"], 1)]
        L += ["", "**Cuidado con**", ""] + [f"- {w}" for w in strategy["watch_out"]] + [""]

    L += ["## Qué cambió", "", ideas["market_summary"], ""]

    rec = [i for i in ideas["ideas"] if i["passes"]]
    L += ["## Ideas recomendadas (pasan los filtros)", ""]
    if not rec:
        L += ["Ninguna idea pasa todos los filtros en esta ejecución.", ""]
    for n, i in enumerate(rec, 1):
        L += [f"### {n}. {i['title']} ({i['composite']}/100, {TREND_ES.get(i['trend'], i['trend'])})", "",
              i["pitch"], "",
              "| Señal de mercado | Fit contigo | Tiempo parcial | Sentimiento | Capital inicial | Horas/sem | Meses a ingreso | Conflicto QFD |",
              "|---|---|---|---|---|---|---|---|",
              f"| {i['market_signal']} | {i['founder_fit']} | {i['part_time_feasibility']} | {i['sentiment']} | "
              f"AUD {i['startup_capital_aud']:,} | {i['hours_per_week']} | {i['months_to_first_revenue']} | "
              f"{CONFLICT_ES.get(i['conflict_risk'], i['conflict_risk'])} |", "",
              f"**Cliente:** {i['target_customer']}. **Modelo de ingreso:** {i['revenue_model']}", "",
              f"**Por qué ahora:** {i['why_now']}", "", f"**Competencia:** {i['competition']}", "",
              f"**Prueba de validación:** {i['validation_test']}", "", "**Primeros pasos (2 semanas):**", ""]
        L += [f"{k}. {s}" for k, s in enumerate(i["first_steps"], 1)] + [""]
        if i.get("conflict_note"):
            L += [f"**Nota sobre QFD:** {i['conflict_note']}", ""]
        if i["risks"]:
            L += ["**Riesgos:** " + "; ".join(i["risks"]), ""]
        if i["evidence_urls"]:
            L += ["**Evidencia:** " + ", ".join(f"[{k}]({u})" for k, u in enumerate(i["evidence_urls"], 1)), ""]

    other = [i for i in ideas["ideas"] if not i["passes"]]
    if other:
        L += ["## Ideas que no pasan los filtros", "", "| Idea | Puntaje | Motivo |", "|---|---|---|"]
        L += [f"| {_cell(i['title'])} | {i['composite']} | {_cell(', '.join(i['fails']))} |" for i in other] + [""]

    L += ["## Dominios evaluados", "", "| Dominio | Puntaje | Razón |", "|---|---|---|"]
    L += [f"| {_cell(d['domain'])} | {d['score']} | {_cell(d['reason'])} |"
          for d in sorted(ideas["domain_scores"], key=lambda d: -d["score"])] + [""]

    L += ["## Tendencias", "", "**Etiquetas en alza** (frente a la ventana anterior)", "",
          "| Término | Menciones | Antes | Lift |", "|---|---|---|---|"]
    L += [f"| {_cell(t['term'])} | {t['count']} | {t['previous']} | {t['lift']} |" for t in trends["rising_tags"][:12]]
    L += ["", "**Palabras y frases más repetidas:** " +
          ", ".join(f"{_cell(w)} ({c})" for w, c in trends["top_words"][:25]), "",
          "**Temas con más evidencia y su sentimiento promedio** (de -1 a 1)", "",
          "| Tema | Menciones | Sentimiento |", "|---|---|---|"]
    L += [f"| {_cell(t['theme'])} | {t['mentions']} | {t['sentiment']} |" for t in trends["themes"][:12]] + [""]

    L += history_section(trends.get("history", {}))

    L += ["## Personas para conocer", ""]
    for p in people["people"]:
        name = f"[{p['name']}]({p['url']})" if p.get("url") else p["name"]
        L.append(f"- **{name}**, {p['role']} ({p['org']}), afinidad {p['alignment']}. {p['why']} "
                 f"*Cómo acercarte:* {p['how_to_approach']}")
    L += ["", "## Eventos próximos", ""]
    L += [f"- **{e['date']}**, [{e['name']}]({e['url']}), {e['location']}. {e['why']}" for e in people["events"]]
    L += ["", "## Comunidades", ""]
    L += [f"- [{c['name']}]({c['url']}): {c['why']}" for c in people["communities"]]

    if ledger:
        L += ["", "## Historial de ideas", "", "Puntaje compuesto por ejecución (últimas 8).", "",
              "| Idea | Vista desde | Evolución | Último |", "|---|---|---|---|"]
        L += [f"| {_cell(l['title'])} | {l['first_seen']} | {_spark(l['history'])} | "
              f"{l['history'][-1][1] if l['history'] else ''} |" for l in ledger[:15]]
    if stats.get("errors") or stats.get("notes"):
        L += ["", "## Notas de la ejecución", ""]
        L += [f"- Error en {e}" for e in stats.get("errors", [])]
        L += [f"- {g}: {n}" for g, n in stats.get("notes", {}).items()]
    return "\n".join(L) + "\n"


def to_html(md: str) -> str:
    body = markdown.markdown(md, extensions=["tables"])
    css = ("body{font-family:Arial,sans-serif;font-size:14px;line-height:1.45;color:#222;max-width:900px}"
           "table{border-collapse:collapse;margin:6px 0}td,th{border:1px solid #ddd;padding:4px 8px;text-align:left;"
           "vertical-align:top}th{background:#f4f4f4}h2{border-bottom:2px solid #eee;padding-bottom:4px}")
    return f"<html><head><meta charset='utf-8'><style>{css}</style></head><body>{body}</body></html>"


def recipient(cfg: dict) -> str:
    """Where the report goes: EMAIL_TO secret if set, else the GMAIL_USER account itself, else config."""
    return (env("EMAIL_TO", required=False) or env("GMAIL_USER", required=False)
            or cfg.get("email", {}).get("to") or "")


def send(subject: str, md: str, to: str, outbox=None) -> None:
    """Send the report through Gmail SMTP with the GMAIL_USER and GMAIL_APP_PASSWORD secrets.
    With outbox set (dry run or mock) the message is written to outbox/report.eml instead."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg.set_content(md)
    msg.add_alternative(to_html(md), subtype="html")
    if outbox is not None:
        outbox.mkdir(parents=True, exist_ok=True)
        msg["From"], msg["To"] = "venture-scout@localhost", to or "dry-run@localhost"
        (outbox / "report.eml").write_bytes(bytes(msg))
        return
    user, password = env("GMAIL_USER"), env("GMAIL_APP_PASSWORD")
    msg["From"], msg["To"] = f"Venture Scout <{user}>", to or user
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, password.replace(" ", ""))   # Gmail shows app passwords in groups of four
        s.send_message(msg)


def subject_line(prefix: str, today: str, ideas: dict, weekly: bool) -> str:
    top = next((i for i in ideas["ideas"] if i["passes"]), None)
    lead = f"Top: {top['title']} ({top['composite']})" if top else "Sin ideas que pasen filtros"
    return f"{prefix}{' Semanal' if weekly else ''} {today} | {lead}"



if __name__ == "__main__":   # failure notice from CI: python -m agents.report failure <run url>
    import sys
    from .common import load_config
    cfg = load_config()
    link = sys.argv[2] if len(sys.argv) > 2 else ""
    send(f"{cfg['email']['subject_prefix']} La ejecución falló",
         f"# Venture Scout\n\nLa ejecución programada falló. Revisa el registro: {link}\n", recipient(cfg))
