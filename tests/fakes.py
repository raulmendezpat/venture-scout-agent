"""Deterministic stand-in for the Claude API, keyed by the structured tool name."""
from __future__ import annotations

import re


def _items(group: str) -> list[dict]:
    base = [
        ("AI receptionists gain traction with Brisbane clinics", "ai receptionist", 0.6),
        ("SMEs say compliance paperwork costs them 10 hours a week", "compliance automation", 0.4),
        ("Founders debate whether AI agencies are saturated", "ai agency", -0.3),
        ("New Queensland program backs AI startups", "founder programs", 0.7),
    ]
    return [{"title": f"{t} ({group})", "url": f"https://example.org/{group}/{n}", "source_name": "Example",
             "source_type": "news", "published": "", "summary": f"{t}. Sample summary about AI agents for small business.",
             "keywords": [th, "ai agents", "small business"], "themes": [th], "sentiment": s, "ai_related": True,
             "idea_signal": f"Opportunity in {th}"} for n, (t, th, s) in enumerate(base)]


def fake_llm(*, name: str, system: str, user: str, schema: dict) -> dict:
    if name == "report_findings":
        group = re.search(r"\(([a-z_]+)\)\.\nFocus", user)
        g = group.group(1) if group else "inbox"
        return {"items": _items(g),
                "people": [{"name": "Alex Example", "role": "Founder", "org": "Example AI", "url": "https://example.org/alex",
                            "themes": ["ai agents"], "why": "Runs a Brisbane AI founders meetup"}],
                "events": [{"name": "Brisbane AI Founders Night", "date": "2099-01-15", "location": "Brisbane",
                            "url": "https://example.org/event", "why": "Monthly founder meetup"}],
                "notes": ""}
    if name == "rank_ideas":
        url = re.search(r"https://example\.org/[a-z_]+/0", user)
        ev = [url.group(0)] if url else []

        def idea(title, cap, hrs, months, fit, conflict="none"):
            return {"id": "", "title": title, "domain": "AI agents for SMEs", "pitch": "Pitch.", "target_customer": "Clinics",
                    "revenue_model": "Monthly retainer", "market_signal": 75, "founder_fit": fit,
                    "part_time_feasibility": 70, "sentiment": 65, "startup_capital_aud": cap, "hours_per_week": hrs,
                    "months_to_first_revenue": months, "evidence_urls": ev + ["https://invented.example/x"],
                    "why_now": "Now.", "competition": "Some.", "risks": ["Risk"], "conflict_risk": conflict,
                    "conflict_note": "", "first_steps": ["Uno", "Dos", "Tres"], "validation_test": "Test.",
                    "trend": "new"}
        return {"market_summary": "Resumen.",
                "domain_scores": [{"domain": "AI agents for SMEs", "score": 78, "reason": "Demanda"}],
                "ideas": [idea("Recepcionista IA para clínicas", 1500, 10, 3, 80),
                          idea("Plataforma SaaS de cumplimiento", 40000, 25, 12, 70),
                          idea("Asesoría de resiliencia para consejos municipales", 500, 8, 4, 85, "high")]}
    if name == "rank_people":
        return {"people": [{"name": "Alex Example", "role": "Founder", "org": "Example AI", "url": "https://example.org/alex",
                            "alignment": 82, "why": "Afinidad.", "how_to_approach": "Ir al meetup."},
                           {"name": "Invented Person", "role": "x", "org": "x", "url": "", "alignment": 99, "why": "",
                            "how_to_approach": ""}],
                "communities": [{"name": "Comunidad", "url": "https://example.org/c", "why": "Por qué."}],
                "events": [{"name": "Brisbane AI Founders Night", "date": "2099-01-15", "location": "Brisbane",
                            "url": "https://example.org/event", "why": "Conocer gente."}]}
    if name == "weekly_strategy":
        return {"headline": "Probar la recepcionista IA.",
                "top_bets": [{"idea": "Recepcionista IA para clínicas", "why": "Señal fuerte.", "kill_criteria": "0 de 10 clínicas."}],
                "thirty_day_plan": ["Semana 1", "Semana 2", "Semana 3", "Semana 4"], "watch_out": ["Política de QFD"]}
    raise KeyError(name)
