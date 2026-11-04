"""Stage 3: analysis agents. Idea analyst (every run), people matcher (every run), strategist (weekly)."""
from __future__ import annotations

import logging
import re

from .common import LLM, dumps
from .store import key_of

log = logging.getLogger("venture-scout")

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
                         name="rank_ideas", max_tokens=24000)
    known_ids = {l["id"] for l in ledger}
    for idea in out["ideas"]:
        if idea.get("id") not in known_ids:
            idea["id"] = key_of(idea["title"])
        idea["evidence_urls"] = [u for u in idea.get("evidence_urls", []) if u in known_urls]
        score(idea, cfg)
    out["ideas"].sort(key=lambda i: (-i["passes"], -i["composite"]))
    return out


PRIORITY = {"type": "string", "enum": ["alta", "media", "baja"]}
PEOPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {"type": "array", "description": "People worth meeting in person or contacting", "items": {
            "type": "object", "properties": {
                "name": {"type": "string"}, "role": {"type": "string"}, "org": {"type": "string"},
                "url": {"type": "string"}, "alignment": SCORE,
                "why": {"type": "string", "description": "Why he should know this person"},
                "how_to_approach": {"type": "string", "description": "A specific, non spammy first step (comment on X, attend Y, ask about Z)"}},
            "required": ["name", "role", "org", "url", "alignment", "why", "how_to_approach"]}},
        "groups": {"type": "array", "description": "Groups and communities to join, best first", "items": {
            "type": "object", "properties": {
                "name": {"type": "string"}, "platform": {"type": "string"}, "url": {"type": "string"},
                "priority": PRIORITY, "why": {"type": "string", "description": "What he gains: customers, co-founders, mentors, learning"},
                "how_to_engage": {"type": "string", "description": "How to join and the first useful contribution to make"}},
            "required": ["name", "platform", "url", "priority", "why", "how_to_engage"]}},
        "events": {"type": "array", "description": "Upcoming activities, soonest first", "items": {
            "type": "object", "properties": {
                "name": {"type": "string"}, "date": {"type": "string"},
                "format": {"type": "string", "enum": ["in_person", "online", "hybrid"]},
                "location": {"type": "string"}, "cost": {"type": "string"}, "url": {"type": "string"},
                "priority": PRIORITY, "why": {"type": "string"}},
            "required": ["name", "date", "format", "location", "cost", "url", "priority", "why"]}},
        "follow": {"type": "array", "description": "People to follow on LinkedIn (authorities, active founders, investors)", "items": {
            "type": "object", "properties": {
                "name": {"type": "string"}, "headline": {"type": "string"},
                "linkedin_url": {"type": "string", "description": "linkedin.com/in/ URL from the input only"},
                "posts_about": {"type": "string"}, "why": {"type": "string"}},
            "required": ["name", "headline", "linkedin_url", "posts_about", "why"]}},
    },
    "required": ["people", "groups", "events", "follow"],
}
PEOPLE_SYSTEM = f"""You help a founder in Brisbane (he lives in Morayfield, Moreton Bay) build a network for an AI and
agents venture while he keeps a full time job, so his time is limited to evenings and weekends. From the people,
groups, events and items provided, recommend:
- groups and communities to join, prioritised by the chance of meeting early customers, co-founders and mentors;
- upcoming entrepreneurship activities in Brisbane (in person, with a preference for evenings or weekends and for
  places reachable from Moreton Bay) and online ones in a time zone that works from Brisbane;
- who to follow on LinkedIn: Australian AI leaders, active founders and investors whose posts would keep him informed
  or open doors. Only people whose linkedin.com/in/ URL is in the input;
- people worth contacting or meeting, with a non spammy first step.
Use only names, groups, events and URLs present in the input; never invent a URL. {STYLE}"""


def norm_url(u: str) -> str:
    """Compare URLs loosely: no scheme, www or LinkedIn country subdomain, query, fragment or trailing slash."""
    u = re.sub(r"[?#].*$", "", (u or "").strip().lower())
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^(www\.|[a-z]{2}\.)(linkedin\.com)", r"\2", u)
    return re.sub(r"^www\.", "", u).rstrip("/")


def _urls(*collections) -> set[str]:
    out = set()
    for col in collections:
        for x in col:
            for k in ("url", "linkedin_url"):
                if x.get(k):
                    out.add(norm_url(x[k]))
    return out


def match_people(llm: LLM, cfg: dict, founder: dict, people: list[dict], events: list[dict], groups: list[dict],
                 items: list[dict], today: str = "") -> dict:
    community = [i for i in digest(items) if i.get("source_group") in
                 ("communities", "universities", "brisbane_ecosystem", "linkedin_authorities", "groups_to_join",
                  "events", "inbox")]
    user = (f"TODAY {today}\n\nFOUNDER PROFILE\n{dumps(founder)}\n\nPEOPLE SEEN (this run and history)\n"
            f"{dumps(people)}\n\nGROUPS SEEN (this run and history)\n{dumps(groups)}\n\n"
            f"UPCOMING EVENTS\n{dumps(events)}\n\nCOMMUNITY ITEMS\n{dumps(community)}\n\n"
            "Return at most 10 people, 8 groups, 12 events and 10 people to follow on LinkedIn.")
    out = llm.structured(model=cfg["models"]["people"], system=PEOPLE_SYSTEM, user=user, schema=PEOPLE_SCHEMA,
                         name="rank_people", max_tokens=20000)
    known = _urls(people, events, groups, items)
    ok = lambda u: bool(u) and norm_url(u) in known
    proposed = {k: len(out[k]) for k in ("people", "groups", "events", "follow")}
    names = {p.get("name", "").lower() for p in people}
    order = {"alta": 0, "media": 1, "baja": 2}
    out["people"] = sorted((p for p in out["people"] if p["name"].lower() in names), key=lambda p: -p["alignment"])
    out["groups"] = sorted((g for g in out["groups"] if ok(g["url"])), key=lambda g: order.get(g["priority"], 3))
    out["events"] = sorted((e for e in out["events"] if ok(e["url"]) and (not today or e["date"] >= today)),
                           key=lambda e: e["date"])
    out["follow"] = [f for f in out["follow"] if "linkedin.com/" in f["linkedin_url"] and ok(f["linkedin_url"])
                     and f["name"].lower() in names]
    log.info("network: kept %s of proposed %s", {k: len(out[k]) for k in proposed}, proposed)
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
                          schema=STRATEGY_SCHEMA, name="weekly_strategy", max_tokens=16000)
