"""Stage 1: collectors. One Claude call per source group, with web search, returning structured items."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from .common import LLM, ROOT, dumps

log = logging.getLogger("venture-scout")

ITEM = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "url": {"type": "string", "description": "Exact URL from the search results. Never invent one."},
        "source_name": {"type": "string", "description": "Publication, group, university or author"},
        "source_type": {"type": "string", "enum": ["news", "government", "university", "forum", "social",
                                                  "blog", "event", "report", "other"]},
        "published": {"type": "string", "description": "YYYY-MM-DD if known, else empty"},
        "summary": {"type": "string", "description": "2 to 3 sentences in English, in your own words"},
        "keywords": {"type": "array", "items": {"type": "string"}, "description": "3 to 8 short lower-case tags"},
        "themes": {"type": "array", "items": {"type": "string"},
                   "description": "Business themes or niches the item is evidence for (e.g. 'ai receptionist for clinics')"},
        "sentiment": {"type": "number", "description": "-1 (negative, failing, saturated) to 1 (strong demand, growth)"},
        "ai_related": {"type": "boolean"},
        "idea_signal": {"type": "string", "description": "Opportunity this suggests for a bootstrapped founder, or empty"},
    },
    "required": ["title", "url", "source_name", "source_type", "summary", "keywords", "themes", "sentiment",
                 "ai_related"],
}
PERSON = {
    "type": "object",
    "properties": {
        "name": {"type": "string"}, "role": {"type": "string"}, "org": {"type": "string"},
        "url": {"type": "string", "description": "Public profile or post URL seen in results, else empty"},
        "themes": {"type": "array", "items": {"type": "string"}},
        "why": {"type": "string", "description": "Why this person is an authority or relevant, from the evidence"},
    },
    "required": ["name", "role", "org", "why"],
}
EVENT = {
    "type": "object",
    "properties": {
        "name": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD"},
        "location": {"type": "string"}, "url": {"type": "string"}, "why": {"type": "string"},
    },
    "required": ["name", "date", "location", "url"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": ITEM},
        "people": {"type": "array", "items": PERSON},
        "events": {"type": "array", "items": EVENT},
        "notes": {"type": "string", "description": "Gaps, blocked sources or anything that limited the search. One or two short sentences in Spanish, without dashes as punctuation"},
    },
    "required": ["items", "people", "events", "notes"],
}

SYSTEM = """You are a research collector for a bootstrapped founder in Brisbane, Australia, who is looking for an
AI and agents venture he can start part time with almost no capital. You search the web, read the results and
return only items that are real, recent and useful for spotting business opportunities, trends, programs,
communities and people to meet.

Rules:
- Use only what the search results show. Copy URLs exactly; never invent URLs, names, dates or numbers.
- Write summaries in your own words; do not copy sentences from the sources.
- Skip items older than the lookback window unless they are an active program or an upcoming event.
- People: only real, named people whose public role is visible in the results (founders, investors, program
  leads, researchers, community organisers). Do not guess contact details.
- Events: only events with a date on or after today that a person in Brisbane can attend (in person or online).
- Prefer primary and authoritative sources (government, universities, the organisation itself) over aggregators."""


def _fill(q: str, today: date) -> str:
    return q.format(year=today.year, month=today.strftime("%B"))


def collect_group(name: str, group: dict, llm: LLM, cfg: dict, today: date) -> dict:
    run = cfg["run"]
    queries = [_fill(q, today) for q in group.get("queries", [])]
    watch = group.get("watchlist") or []
    lookback = group.get("lookback_days", run["lookback_days"])
    user = (f"Today is {today.isoformat()} (Brisbane). Lookback window: {lookback} days.\n"
            f"Source group: {group['label']} ({name}).\nFocus: {group['focus']}\n"
            f"Preferred domains: {', '.join(group.get('domains_hint', [])) or 'any authoritative source'}\n"
            f"Start from these searches and adapt them as you learn:\n- " + "\n- ".join(queries) +
            (f"\nAlso check recent public posts by: {', '.join(watch)}" if watch else "") +
            f"\n\nReturn at most {run['max_items_per_group']} items.")
    tool = {"type": cfg["web_search_tool"], "name": "web_search", "max_uses": run["max_searches_per_group"],
            "user_location": {"type": "approximate", "city": "Brisbane", "region": "Queensland", "country": "AU",
                              "timezone": "Australia/Brisbane"}}
    out = llm.structured(model=cfg["models"]["collector"], system=SYSTEM, user=user, schema=SCHEMA,
                         name="report_findings", max_tokens=12000, server_tools=[tool])
    for it in out.get("items", []):
        it["source_group"] = name
    return out


def collect_inbox(llm: LLM, cfg: dict, today: date, inbox: Path | None = None) -> tuple[dict, list[Path]]:
    """Posts you paste by hand (for example from private Facebook or LinkedIn groups) as .txt or .md files."""
    inbox = inbox or ROOT / "inbox"
    files = sorted(p for p in inbox.glob("*") if p.suffix in (".txt", ".md") and p.name != "README.md")
    if not files:
        return {"items": [], "people": [], "events": [], "notes": ""}, []
    text = "\n\n".join(f"=== {p.name} ===\n{p.read_text(encoding='utf-8')[:6000]}" for p in files)
    user = (f"Today is {today.isoformat()}. These are posts the founder copied by hand from groups he belongs to. "
            "Extract items (use url 'inbox:<file name>' when no link is given), people and events.\n\n" + text)
    out = llm.structured(model=cfg["models"]["collector"], system=SYSTEM, user=user, schema=SCHEMA,
                         name="report_findings", max_tokens=6000)
    for it in out.get("items", []):
        it["source_group"] = "inbox"
    return out, files


def collect_all(llm: LLM, cfg: dict, sources: dict, today: date, only: list[str] | None = None) -> tuple[dict, list[str]]:
    merged = {"items": [], "people": [], "events": [], "notes": {}}
    errors: list[str] = []
    for name, group in sources["groups"].items():
        if only and name not in only:
            continue
        try:
            res = collect_group(name, group, llm, cfg, today)
        except Exception as exc:   # one failing source group must not stop the run
            log.exception("Collector %s failed", name)
            errors.append(f"{name}: {exc}")
            continue
        log.info("%s: %d items, %d people, %d events", name, len(res["items"]), len(res["people"]),
                 len(res["events"]))
        for k in ("items", "people", "events"):
            merged[k].extend(res.get(k, []))
        if res.get("notes"):
            merged["notes"][name] = res["notes"]
    return merged, errors


if __name__ == "__main__":   # quick look at one group: python -m agents.collector news
    import sys
    from .common import load_config, load_sources
    print(dumps(collect_group(sys.argv[1], load_sources()["groups"][sys.argv[1]], LLM(), load_config(), date.today())))
