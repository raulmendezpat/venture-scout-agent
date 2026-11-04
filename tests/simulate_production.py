"""Production rehearsal: runs run.py exactly as CI does (no --mock, no --dry-run) on a copy of the repo for several
weeks of scheduled dates. Only the outside services are replaced: the Anthropic client returns real SDK Message
objects (tool_use blocks, usage, pause_turn) and Gmail SMTP is captured. Each request is checked: model names,
web search tool shape, tool_choice and JSON schemas.

  python tests/simulate_production.py            # 8 weeks, Tue/Thu/Sun
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
from datetime import date, timedelta
from email import message_from_bytes
from pathlib import Path

import anthropic
from jsonschema import Draft202012Validator

SRC = Path(__file__).resolve().parent.parent
THEMES = {   # theme: (weight by week function, sentiment)
    "ai receptionist for clinics": (lambda w: 1 + w, 0.6),             # rising
    "compliance automation for smes": (lambda w: 4, 0.4),               # persistent
    "ai automation agency": (lambda w: max(0, 6 - w), -0.3),            # fading
    "ai governance advisory": (lambda w: 2 + (w % 2), 0.3),
    "tradie admin agents": (lambda w: 0 if w < 4 else 3, 0.5),          # appears mid way
}
PEOPLE = [("Alex Example", "Founder", "Example AI"), ("Sam Sample", "Program Lead", "Example Hub"),
          ("Jo Placeholder", "Investor", "Example Ventures")]


class Checks:
    def __init__(self):
        self.calls, self.errors, self.max_prompt = 0, [], 0


def make_client(cfg: dict, state: dict, checks: Checks, rng: random.Random):
    models = set(cfg["models"].values())
    counter = {"n": 0}

    class Messages:
        def create(self, *, model, max_tokens, system, tools, tool_choice, messages):
            checks.calls += 1
            counter["n"] += 1
            if model not in models:
                checks.errors.append(f"unknown model {model}")
            result_tool = tools[-1]
            Draft202012Validator.check_schema(result_tool["input_schema"])
            server = [t for t in tools if t.get("type", "").startswith("web_search")]
            for t in server:
                if t["type"] != cfg["web_search_tool"] or t["name"] != "web_search" or "max_uses" not in t:
                    checks.errors.append(f"bad web search tool {t}")
            if server and tool_choice.get("type") != "auto":
                checks.errors.append("forced tool_choice with server tools (the API rejects this)")
            prompt = system + json.dumps(messages, default=str)
            checks.max_prompt = max(checks.max_prompt, len(prompt))
            name = result_tool["name"]
            if name == "report_findings" and "(brisbane_ecosystem)" in messages[0]["content"] and state["fail_once"]:
                state["fail_once"] = False
                raise RuntimeError("Error code: 529, overloaded_error (injected)")
            # every 7th call with web search first pauses, as the API does on long searches
            if server and len(messages) == 1 and counter["n"] % 7 == 0:
                return _message(model, [{"type": "text", "text": "Searching..."}], "pause_turn")
            data = fake_output(name, messages[0]["content"] if isinstance(messages[0]["content"], str) else "",
                               state, rng)
            errs = list(Draft202012Validator(result_tool["input_schema"]).iter_errors(data))
            if errs:
                checks.errors.append(f"{name} fake output invalid: {errs[0].message}")
            return _message(model, [{"type": "tool_use", "id": f"toolu_{counter['n']}", "name": name, "input": data}],
                            "tool_use", searches=len(server) and 3)

    class Client:
        messages = Messages()

    return Client()


def _message(model, content, stop, searches=0):
    return anthropic.types.Message.model_validate({
        "id": "msg_sim", "type": "message", "role": "assistant", "model": model, "content": content,
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 9000, "output_tokens": 1500, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0, "server_tool_use": {"web_search_requests": searches, "web_fetch_requests": 0}}})


def fake_output(name: str, user: str, state: dict, rng: random.Random) -> dict:
    w, today = state["week"], state["today"]
    if name == "report_findings":
        group = user.split("(")[1].split(")")[0] if "Source group:" in user else "inbox"
        items = []
        for th, (weight, sent) in THEMES.items():
            for k in range(weight(w) // 2 + rng.randint(0, 1)):
                n = state["seq"] = state["seq"] + 1
                items.append({"title": f"{th.capitalize()} story {n}", "url": f"https://example.org/{group}/{n}",
                              "source_name": "Example", "source_type": "news", "published": today,
                              "summary": f"Australian businesses discuss {th}; demand and pricing details.",
                              "keywords": [th, "ai agents", rng.choice(["smes", "automation", "brisbane"])],
                              "themes": [th], "sentiment": max(-1, min(1, sent + rng.uniform(-0.2, 0.2))),
                              "ai_related": True, "idea_signal": f"Service around {th}"})
        if rng.random() < 0.3 and state["urls"]:   # the same article found again by another group
            items.append(dict(state["urls"][-1]))
        state["urls"].extend(items[-2:])
        p = rng.choice(PEOPLE)
        ev_date = (date.fromisoformat(today) + timedelta(days=rng.randint(3, 25))).isoformat()
        return {"items": items[:15],
                "people": [{"name": p[0], "role": p[1], "org": p[2], "url": "https://example.org/p",
                            "themes": ["ai agents"], "why": "Active in Brisbane AI founders"}],
                "events": [{"name": f"Brisbane AI meetup {ev_date}", "date": ev_date, "location": "Brisbane",
                            "url": "https://example.org/e", "why": "Founders"}],
                "notes": ""}
    if name == "rank_ideas":
        urls = [u for u in user.split('"url": "')[1:]][:2]
        ev = [u.split('"')[0] for u in urls]
        ideas = []
        for n, (th, (weight, sent)) in enumerate(THEMES.items()):
            ideas.append({"id": "", "title": th.title(), "domain": "AI for SMEs", "pitch": "Pitch.",
                          "target_customer": "SMEs", "revenue_model": "Retainer",
                          "market_signal": min(100, 40 + 8 * weight(w)), "founder_fit": 60 + 5 * n,
                          "part_time_feasibility": 70, "sentiment": int(50 + 40 * sent),
                          "startup_capital_aud": [800, 1500, 300, 2000, 600][n], "hours_per_week": 10,
                          "months_to_first_revenue": 3, "evidence_urls": ev, "why_now": "Now.",
                          "competition": "Some.", "risks": ["Risk"], "conflict_risk": ["none", "low"][n % 2],
                          "conflict_note": "", "first_steps": ["Uno", "Dos", "Tres"], "validation_test": "Test.",
                          "trend": "rising" if weight(w) > weight(max(0, w - 1)) else "stable"})
        return {"market_summary": "Resumen de la semana.",
                "domain_scores": [{"domain": "AI agents for SMEs", "score": 75, "reason": "Demanda"}], "ideas": ideas}
    if name == "rank_people":
        return {"people": [{"name": p[0], "role": p[1], "org": p[2], "url": "https://example.org/p", "alignment": 70,
                            "why": "Afinidad.", "how_to_approach": "Ir al meetup."} for p in PEOPLE],
                "communities": [{"name": "Comunidad", "url": "https://example.org/c", "why": "Por qué."}],
                "events": [{"name": "Meetup", "date": today, "location": "Brisbane", "url": "https://example.org/e",
                            "why": "Conocer gente."}]}
    if name == "weekly_strategy":
        return {"headline": "Probar la idea con más señal persistente.",
                "top_bets": [{"idea": "Ai Receptionist For Clinics", "why": "Sube cada semana.", "kill_criteria": "0 de 10."}],
                "thirty_day_plan": ["Semana 1", "Semana 2", "Semana 3", "Semana 4"], "watch_out": ["Política de QFD"]}
    raise KeyError(name)


def main(weeks: int = 8) -> int:
    work = Path(tempfile.mkdtemp(prefix="vs-prod-", dir=os.environ.get("SIM_DIR"))) / "repo"
    shutil.copytree(SRC, work, ignore=shutil.ignore_patterns(".git", "out", "__pycache__", ".pytest_cache"))
    (work / "data" / "venture.db").unlink(missing_ok=True)
    sys.path.insert(0, str(work))
    os.chdir(work)
    os.environ.update({"ANTHROPIC_API_KEY": "sk-sim", "GMAIL_USER": "me@gmail.com",
                       "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop"})
    os.environ.pop("EMAIL_TO", None)

    import run
    from agents import common, report
    cfg = common.load_config()
    rng, checks = random.Random(7), Checks()
    state = {"week": 0, "today": "", "seq": 0, "urls": [], "fail_once": True}
    sent = []

    class SMTP:
        def __init__(self, host, port, timeout=None):
            assert (host, port) == ("smtp.gmail.com", 465)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, user, pw): assert user == "me@gmail.com" and " " not in pw
        def send_message(self, m): sent.append(message_from_bytes(bytes(m)))

    report.smtplib.SMTP_SSL = SMTP
    common.anthropic = anthropic
    orig_init = common.LLM.__init__

    def init(self, fake=None, client=None):
        orig_init(self, fake=fake, client=client or make_client(cfg, state, checks, rng))
    common.LLM.__init__ = init

    (work / "inbox" / "facebook_group_post.txt").write_text(
        "Post in Brisbane Small Business group: anyone using AI to answer clinic phone calls? We miss 30% of calls.",
        encoding="utf-8")

    start = date(2026, 11, 10)   # a Tuesday
    days = [start + timedelta(days=d) for d in range(weeks * 7) if (start + timedelta(days=d)).weekday() in (1, 3, 6)]
    failures = []
    for n, d in enumerate(days):
        state["week"], state["today"] = (d - start).days // 7, d.isoformat()
        try:
            rc = run.main(["--today", d.isoformat()])
            if rc != 0:
                failures.append(f"{d}: exit {rc}")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            failures.append(f"{d}: {exc!r}")

    last = (work / "reports" / "latest.md").read_text(encoding="utf-8")
    db = __import__("sqlite3").connect(work / "data" / "venture.db")
    q = lambda sql: db.execute(sql).fetchone()[0]
    summary = {
        "runs": len(days), "failures": failures, "request_errors": checks.errors, "api_calls": checks.calls,
        "emails_sent": len(sent), "weekly_emails": sum("Semanal" in m["Subject"] for m in sent),
        "stored_runs": q("SELECT COUNT(*) FROM runs"), "items": q("SELECT COUNT(*) FROM items"),
        "idea_score_rows": q("SELECT COUNT(*) FROM idea_scores"),
        "run_errors_logged": [r[0] for r in db.execute("SELECT errors FROM runs WHERE errors!='[]'")],
        "inbox_processed": [p.name for p in (work / "inbox" / "processed").glob("*")],
        "reports_written": len(list((work / "reports").glob("2026-*.md"))) + len(list((work / "reports").glob("2027-*.md"))),
        "largest_prompt_chars": checks.max_prompt,
        "last_subject": sent[-1]["Subject"] if sent else None,
        "workdir": str(work),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n----- last report: historical section -----")
    i = last.find("## Análisis histórico")
    print(last[i:last.find("## Personas para conocer")] if i >= 0 else "(missing)")
    ok = not failures and not checks.errors and "## Análisis histórico" in last
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
