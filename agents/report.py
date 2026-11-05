"""Stage 4: Spanish report as Markdown (committed under reports/) and HTML (email body)."""
from __future__ import annotations

import re
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


PLATFORM_ES = {"linkedin": "LinkedIn", "facebook": "Facebook", "meetup": "Meetup", "slack": "Slack",
               "discord": "Discord", "whatsapp": "WhatsApp", "university": "Universidad",
               "accelerator": "Aceleradora", "association": "Asociación", "other": "Otro"}
FORMAT_ES = {"in_person": "Presencial", "online": "En línea", "hybrid": "Híbrido"}


def network_section(net: dict) -> list[str]:
    L = ["## Red de contactos", ""]
    L += ["### Grupos a los que unirte", ""]
    if net.get("groups"):
        L += ["| Prioridad | Grupo | Plataforma | Qué ganas | Cómo participar |", "|---|---|---|---|---|"]
        L += [f"| {g['priority']} | [{_cell(g['name'])}]({g['url']}) | {PLATFORM_ES.get(g['platform'], _cell(g['platform']))} | "
              f"{_cell(g['why'])} | {_cell(g['how_to_engage'])} |" for g in net["groups"]] + [""]
    else:
        L += ["No se encontraron grupos con enlace verificable en esta ejecución.", ""]

    L += ["### Actividades próximas", ""]
    events = net.get("events") or []
    in_person = [e for e in events if e["format"] in ("in_person", "hybrid")]
    online = [e for e in events if e["format"] == "online"]
    for title, rows in (("Presenciales en Brisbane y alrededores", in_person), ("En línea", online)):
        L += [f"**{title}**", ""]
        if not rows:
            L += ["Ninguna con fecha y enlace verificables en esta ejecución.", ""]
            continue
        L += ["| Fecha | Actividad | Formato | Lugar | Costo | Prioridad | Por qué ir |", "|---|---|---|---|---|---|---|"]
        L += [f"| {e['date']} | [{_cell(e['name'])}]({e['url']}) | {FORMAT_ES.get(e['format'], e['format'])} | "
              f"{_cell(e['location'])} | {_cell(e['cost'] or 'sin dato')} | {e['priority']} | {_cell(e['why'])} |"
              for e in rows] + [""]

    L += ["### A quién seguir en LinkedIn", ""]
    if net.get("follow"):
        L += ["| Persona | Perfil | Publica sobre | Por qué seguirla |", "|---|---|---|---|"]
        L += [f"| [{_cell(f['name'])}]({f['linkedin_url']}) | {_cell(f['headline'])} | {_cell(f['posts_about'])} | "
              f"{_cell(f['why'])} |" for f in net["follow"]] + [""]
    else:
        L += ["No se encontraron perfiles de LinkedIn con enlace verificable en esta ejecución.", ""]

    L += ["### Personas para contactar", ""]
    if net.get("people"):
        for p in net["people"]:
            name = f"[{p['name']}]({p['url']})" if p.get("url") else p["name"]
            L.append(f"- **{name}**, {p['role']} ({p['org']}), afinidad {p['alignment']}. {p['why']} "
                     f"*Cómo acercarte:* {p['how_to_approach']}")
        L += [""]
    else:
        L += ["Sin recomendaciones en esta ejecución.", ""]
    return L


def cost_section(cost: dict, pricing: dict, models: dict, totals: dict | None) -> list[str]:
    """Cost of this run per model, with month and all-time totals (USD and AUD)."""
    rate = pricing.get("usd_to_aud", 1.0)
    stages = {}
    for stage, model in models.items():
        stages.setdefault(model, []).append(stage)
    L = ["## Costo de la ejecución", "",
         "| Modelo | Etapas | Llamadas | Tokens entrada | Tokens salida | Búsquedas web | USD | AUD |",
         "|---|---|---|---|---|---|---|---|"]
    per = cost.get("per_model", {})
    for model, t in sorted(per.items(), key=lambda kv: -kv[1]["usd"]):
        L.append(f"| {model} | {', '.join(stages.get(model, []))} | {t['calls']} | {t['input'] + t['cache_write'] + t['cache_read']:,} | "
                 f"{t['output']:,} | {t['web_searches']} | {t['usd']:.2f} | {t['usd'] * rate:.2f} |")
    calls = sum(t["calls"] for t in per.values())
    searches = sum(t["web_searches"] for t in per.values())
    L.append(f"| **Total ejecución** | | **{calls}** | | | **{searches}** | **{cost['usd']:.2f}** | **{cost['aud']:.2f}** |")
    if totals is not None:
        month_aud = totals["month_aud"] + cost["aud"]
        all_aud = totals["all_aud"] + cost["aud"]
        n = lambda k: f"{k} ejecución" if k == 1 else f"{k} ejecuciones"
        L.append(f"| Acumulado del mes ({n(totals['month_runs'] + 1)}) | | | | | | {month_aud / rate:.2f} | "
                 f"{month_aud:.2f} |")
        L.append(f"| Acumulado desde {totals['since'] or 'hoy'} ({n(totals['all_runs'] + 1)}) | | | | | | "
                 f"{all_aud / rate:.2f} | {all_aud:.2f} |")
    L += ["", f"Estimación con los precios de `config.yaml` (USD por millón de tokens, búsqueda web USD "
              f"{pricing.get('web_search_usd_per_1000', 10)} por 1,000) y cambio USD 1 = AUD {rate}. "
              "El cobro exacto está en la Console de Anthropic.", ""]
    return L


SECTOR_ES = {"hospitality_food": "Gastronomía y hospitalidad", "retail_ecommerce": "Comercio y ecommerce",
             "construction_trades": "Construcción y oficios", "property": "Inmobiliario",
             "health_wellness": "Salud y bienestar", "aged_care_disability": "Adultos mayores y discapacidad (NDIS)",
             "education_training": "Educación y formación", "tourism_events": "Turismo y eventos",
             "professional_services": "Servicios profesionales", "technology_ai": "Tecnología e IA",
             "creative_media": "Creativo y medios", "agriculture_food_production": "Agro y producción de alimentos",
             "manufacturing": "Manufactura", "energy_sustainability": "Energía y sostenibilidad",
             "transport_logistics": "Transporte y logística", "finance": "Finanzas",
             "community_social": "Comunitario y social", "other": "Otros"}
MOMENTUM_ES = {"new": "nuevo", "rising": "subiendo", "stable": "estable", "falling": "bajando"}


def _sector(s: str) -> str:
    return SECTOR_ES.get(s, _cell(s))


def local_section(local: dict | None, sectors: list[dict]) -> list[str]:
    L = ["## Tendencias de emprendimiento en Brisbane (todos los sectores)", ""]
    if local and local.get("summary"):
        L += [local["summary"], ""]
    topics = (local or {}).get("topics", [])
    if topics:
        L += ["| Tema | Sector | Fuerza | Momento | Sentimiento | Qué está pasando | Oportunidad a tiempo parcial | Capital inicial | Ventaja con tus skills | Evidencia |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for t in topics:
            ev = ", ".join(f"[{k}]({u})" for k, u in enumerate(t["evidence_urls"][:3], 1))
            L.append(f"| **{_cell(t['topic'])}** | {_sector(t['sector'])} | {t['strength']} | "
                     f"{MOMENTUM_ES.get(t['momentum'], t['momentum'])} | {t['sentiment']} | {_cell(t['what'])} {_cell(t['why_now'])} | "
                     f"{_cell(t['opportunity'])} | AUD {t['startup_capital_aud']:,} | {t['fit_for_founder']} | {ev} |")
        L += [""]
    else:
        L += ["Sin temas con evidencia verificable en esta ejecución.", ""]
    if sectors:
        L += ["**Actividad por sector en Brisbane y Queensland** (elementos de los últimos 14 días frente a los 14 anteriores)", "",
              "| Sector | Ahora | Antes | Cambio | Sentimiento (-1 a 1) |", "|---|---|---|---|---|"]
        L += [f"| {_sector(r['sector'])} | {r['current']} | {r['previous']} | {r['change']:+d} | "
              f"{'' if r['sentiment'] is None else r['sentiment']} |" for r in sectors[:12]] + [""]
    return L


def build_markdown(today: str, ideas: dict, people: dict, trends: dict, ledger: list[dict], strategy: dict | None,
                   stats: dict, local: dict | None = None) -> str:
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

    L += local_section(local, trends.get("local_sectors", []))
    L += network_section(people)

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

    if ledger:
        L += ["", "## Historial de ideas", "", "Puntaje compuesto por ejecución (últimas 8).", "",
              "| Idea | Vista desde | Evolución | Último |", "|---|---|---|---|"]
        L += [f"| {_cell(l['title'])} | {l['first_seen']} | {_spark(l['history'])} | "
              f"{l['history'][-1][1] if l['history'] else ''} |" for l in ledger[:15]]
    if stats.get("errors") or stats.get("notes"):
        L += ["", "## Notas de la ejecución", ""]
        L += [f"- Error en {e}" for e in stats.get("errors", [])]
        L += [f"- {g}: {n}" for g, n in stats.get("notes", {}).items()]
    if stats.get("cost"):
        L += [""] + cost_section(stats["cost"], stats["pricing"], stats["models"], stats.get("cost_totals"))
    return no_dashes("\n".join(L) + "\n")


def no_dashes(md: str) -> str:
    """Model text sometimes uses em or en dashes as punctuation; the founder prefers commas."""
    return re.sub(r"\s*[\u2014\u2013]\s*", ", ", md)


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
