"""Stage 3: analysis agents. Idea analyst (every run), people matcher (every run), strategist (weekly)."""
from __future__ import annotations

from .common import LLM, dumps
from .store import key_of

STYLE = ("Write all text for the founder in Spanish (neutral Latin American). Be concrete and evidence based. "
         "Do not use dashes (—, – or a hyphen between clauses) as punctuation; use commas, colons, semicolons, "
         "parentheses or separate sentences.")

SCORE = {"type": "integer", "minimum": 0, "maximum": 100}
IDEA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Copy the id of an existing idea from the ledger when this is the same idea; empty for a new one"},
        "title": {"type": "string", "description": "Short name of the business idea"},
        "domain": {"type": "string"},
        "pitch": {"type": "string", "description": "Who pays, for what problem, how it is delivered (2 sentences)"},
        "target_customer": {"type": "string"},
        "revenue_model": {"type": "string"},
        "market_signal": SCORE, "founder_fit": SCORE, "part_time_feasibility": SCORE, "sentiment": SCORE,
        "startup_capital_aud": {"type": "integer", "description": "Cash needed to reach the first paying customer"},
        "hours_per_week": {"type": "integer"},
        "months_to_first_revenue": {"type": "integer"},
        "evidence_urls": {"type": "array", "items": {"type": "string"}, "description": "URLs from the provided items only"},
        "why_now": {"type": "string"},
        "competition": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "conflict_risk": {"type": "string", "enum": ["none", "low", "medium", "high"],
                          "description": "Conflict with his QFD employment"},
        "conflict_note": {"type": "string"},
        "first_steps": {"type": "array", "items": {"type": "string"}, "description": "3 steps for the next 2 weeks, doable part time"},
        "validation_test": {"type": "string", "description": "Cheapest test that would prove or kill the idea"},
        "trend": {"type": "string", "enum": ["new", "rising", "stable", "falling"]},
    },
    "required": ["id", "title", "domain", "pitch", "target_customer", "revenue_model", "market_signal", "founder_fit",
                 "part_time_feasibility", "sentiment", "startup_capital_aud", "hours_per_week",
                 "months_to_first_revenue", "evidence_urls", "why_now", "competition", "risks", "conflict_risk",
                 "conflict_note", "first_steps", "validation_test", "trend"],
}
IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "market_summary": {"type": "string", "description": "What changed in this period, 4 to 6 sentences"},
        "domain_scores": {"type": "array", "items": {"type": "object", "properties": {
            "domain": {"type": "string"}, "score": SCORE, "reason": {"type": "string"}},
            "required": ["domain", "score", "reason"]}},
        "ideas": {"type": "array", "items": IDEA, "description": "6 to 10 ideas, existing ones re-scored plus new ones"},
    },
    "required": ["market_summary", "domain_scores", "ideas"],
}

IDEA_SYSTEM = f"""You are a venture analyst for one specific founder. You judge which AI and agent businesses he can
start from zero in Australia, part time, with almost no capital, using the evidence collected by the research agents.
Score honestly: low scores are useful. Re-score the ideas already in the ledger (keep their id) and add new ideas only
when the evidence supports them. Prefer services that can become products (productised services), niches where his mix
of AI strategy, agent engineering, architecture and regulated or public safety experience is rare, and customers who
can pay without long procurement cycles. Respect his employment constraint: never suggest selling to his employer or
using its data, systems, IP or contacts, and rate conflict risk for anything near emergency services or Queensland
Government. Use TRENDS.history (whole stored history): weight persistent and gaining terms and themes with improving
sentiment above one-week spikes, and mark an idea "falling" when its themes are fading. Cite only URLs that appear
in the provided items. {STYLE}"""


def _clip(n: float) -> float:
    return max(0.0, min(100.0, n))


def score(idea: dict, cfg: dict) -> dict:
    """Composite score and hard filters, computed in code so they are consistent across runs."""
    f, w = cfg["filters"], cfg["weights"]
    cap = max(idea.get("startup_capital_aud", 0), 0)
    idea["low_capital"] = round(_clip(100 - 100 * cap / (2 * f["max_startup_capital_aud"])))
    months = max(idea.get("months_to_first_revenue", 12), 0)
    idea["speed_to_revenue"] = round(_clip(100 - 100 * months / (2 * f["max_months_to_first_revenue"])))
    comp = sum(w[k] * idea.get(k, 0) for k in w)
    if idea.get("conflict_risk") == "high":
        comp *= 0.6
    elif idea.get("conflict_risk") == "medium":
        comp *= 0.85
    idea["composite"] = round(comp, 1)
    fails = []
    if cap > f["max_startup_capital_aud"]:
        fails.append(f"capital AUD {cap:,}")
    if idea.get("hours_per_week", 99) > f["max_hours_per_week"]:
        fails.append(f"{idea.get('hours_per_week')} h/semana")
    if months > f["max_months_to_first_revenue"]:
        fails.append(f"{months} meses a primer ingreso")
    if idea.get("founder_fit", 0) < f["min_founder_fit"]:
        fails.append(f"fit {idea.get('founder_fit')}")
    if idea["composite"] < f["min_composite"]:
        fails.append(f"puntaje {idea['composite']}")
    if idea.get("conflict_risk") == "high":
        fails.append("conflicto alto con QFD")
    idea["fails"], idea["passes"] = fails, not fails
    return idea


def digest(items: list[dict], limit: int = 120) -> list[dict]:
    keep = ("title", "url", "source_group", "source_name", "published", "summary", "themes", "sentiment",
            "idea_signal")
    return [{k: it.get(k) for k in keep if it.get(k) not in (None, "", [])} for it in items[:limit]]


def analyse_ideas(llm: LLM, cfg: dict, founder: dict, items: list[dict], trends: dict, ledger: list[dict]) -> dict:
    known_urls = {it.get("url") for it in items}
    ledger_view = [{"id": l["id"], "title": l["title"], "domain": l["domain"], "history": l["history"][-6:],
                    "last_pitch": l["last"].get("pitch", "")} for l in ledger]
    user = (f"FOUNDER PROFILE\n{dumps(founder)}\n\nHARD FILTERS (an idea above these is still allowed, it just will not be "
            f"recommended)\n{dumps(cfg['filters'])}\n\nIDEA LEDGER (previous runs)\n{dumps(ledger_view)}\n\n"
            f"TRENDS (deterministic counts over the stored history)\n{dumps(trends)}\n\n"
            f"NEW EVIDENCE THIS RUN\n{dumps(digest(items))}")
    out = llm.structured(model=cfg["models"]["analyst"], system=IDEA_SYSTEM, user=user, schema=IDEAS_SCHEMA,
                         name="rank_ideas", max_tokens=12000)
    known_ids = {l["id"] for l in ledger}
    for idea in out["ideas"]:
        if idea.get("id") not in known_ids:
            idea["id"] = key_of(idea["title"])
        idea["evidence_urls"] = [u for u in idea.get("evidence_urls", []) if u in known_urls]
        score(idea, cfg)
    out["ideas"].sort(key=lambda i: (-i["passes"], -i["composite"]))
    return out


PEOPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "role": {"type": "string"}, "org": {"type": "string"},
            "url": {"type": "string"}, "alignment": SCORE,
            "why": {"type": "string", "description": "Why he should know this person"},
            "how_to_approach": {"type": "string", "description": "A specific, non spammy first step (comment on X, attend Y, ask about Z)"}},
            "required": ["name", "role", "org", "url", "alignment", "why", "how_to_approach"]}},
        "communities": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "url": {"type": "string"}, "why": {"type": "string"}},
            "required": ["name", "url", "why"]}},
        "events": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "date": {"type": "string"}, "location": {"type": "string"},
            "url": {"type": "string"}, "why": {"type": "string"}}, "required": ["name", "date", "location", "url", "why"]}},
    },
    "required": ["people", "communities", "events"],
}
PEOPLE_SYSTEM = f"""You help a founder in Brisbane build a network for an AI and agents venture. From the people,
events and items provided, pick who is most worth knowing (aligned founders, possible co-founders, mentors, investors
who back bootstrapped AI services, program leads, early customers) and the events and communities that give the best
chance of meeting them. Use only people, events and URLs present in the input. {STYLE}"""


def match_people(llm: LLM, cfg: dict, founder: dict, people: list[dict], events: list[dict], items: list[dict]) -> dict:
    user = (f"FOUNDER PROFILE\n{dumps(founder)}\n\nPEOPLE SEEN (this run and history)\n{dumps(people)}\n\n"
            f"UPCOMING EVENTS\n{dumps(events)}\n\nCOMMUNITY ITEMS\n"
            f"{dumps([i for i in digest(items) if i.get('source_group') in ('communities', 'universities', 'brisbane_ecosystem', 'linkedin_authorities', 'inbox')])}\n\n"
            "Return at most 12 people, 6 communities and 8 events.")
    out = llm.structured(model=cfg["models"]["people"], system=PEOPLE_SYSTEM, user=user, schema=PEOPLE_SCHEMA,
                         name="rank_people", max_tokens=12000)
    names = {p.get("name", "").lower() for p in people}
    out["people"] = sorted((p for p in out["people"] if p["name"].lower() in names), key=lambda p: -p["alignment"])
    return out


STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "top_bets": {"type": "array", "items": {"type": "object", "properties": {
            "idea": {"type": "string"}, "why": {"type": "string"}, "kill_criteria": {"type": "string"}},
            "required": ["idea", "why", "kill_criteria"]}},
        "thirty_day_plan": {"type": "array", "items": {"type": "string"}, "description": "Week by week, part time"},
        "watch_out": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "top_bets", "thirty_day_plan", "watch_out"],
}
STRATEGY_SYSTEM = f"""You are a pragmatic startup advisor. Using the idea ledger with its score history and the
trends, choose the two or three bets the founder should test next and lay out a 30 day part time plan with clear kill
criteria. Base the choice on the whole history (TRENDS.history: persistent terms, gaining and fading terms, theme
sentiment over time, recurring people) and on each idea's score history, not on the latest week alone. Point out
ideas that are fading and any risk with his employment. {STYLE}"""


def weekly_strategy(llm: LLM, cfg: dict, founder: dict, ledger: list[dict], trends: dict) -> dict:
    user = f"FOUNDER PROFILE\n{dumps(founder)}\n\nIDEA LEDGER\n{dumps(ledger)}\n\nTRENDS\n{dumps(trends)}"
    return llm.structured(model=cfg["models"]["strategist"], system=STRATEGY_SYSTEM, user=user,
                          schema=STRATEGY_SCHEMA, name="weekly_strategy", max_tokens=10000)
