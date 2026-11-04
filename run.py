"""Orchestration: collect, store, trends, ideas, people, (weekly) strategy, report, email.

Usage:
  python run.py                    # real run (needs ANTHROPIC_API_KEY, GMAIL_USER, GMAIL_APP_PASSWORD)
  python run.py --dry-run          # real sources and Claude, writes the email to out/ instead of sending it
  python run.py --mock             # no network: fake Claude, writes to out/ (for testing)
  python run.py --groups news,events --weekly
"""
from __future__ import annotations

import argparse
import logging
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

from agents import analyst, collector, report, trends
from agents.common import LLM, ROOT, load_config, load_founder, load_sources
from agents.store import Store

log = logging.getLogger("venture-scout")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--weekly", action="store_true", help="force the weekly strategic review")
    ap.add_argument("--groups", default="", help="comma separated source groups (default: all)")
    ap.add_argument("--db", default="")
    ap.add_argument("--today", default="", help="override the run date (YYYY-MM-DD), for tests")
    ap.add_argument("--test-email", action="store_true", help="only send a short test email and exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg, sources, founder = load_config(), load_sources(), load_founder()
    if args.test_email:
        to = report.recipient(cfg)
        report.send(f"{cfg['email']['subject_prefix']} Correo de prueba",
                    "# Venture Scout\n\nEl envío por Gmail funciona. Los reportes llegarán a esta dirección.\n", to)
        log.info("Test email sent to %s", to)
        return 0
    now = datetime.now(ZoneInfo(cfg["run"]["timezone"]))
    if args.today:
        now = datetime.fromisoformat(args.today).replace(tzinfo=now.tzinfo)
    today, run_id = now.date().isoformat(), now.strftime("%Y-%m-%d_%H%M")
    weekly = args.weekly or now.weekday() == cfg["run"]["weekly_review_weekday"] or now.day == 1
    out_dir = ROOT / "out" / run_id
    outbox = out_dir if (args.dry_run or args.mock) else None

    if args.mock:
        from tests.fakes import fake_llm
        llm = LLM(fake=fake_llm)
    else:
        llm = LLM()
    out_dir.mkdir(parents=True, exist_ok=True)
    store = Store(args.db or str((out_dir if args.mock else ROOT / "data") / "venture.db"))
    only = [g for g in args.groups.split(",") if g] or None

    # 1. Collect (web groups plus anything pasted into inbox/)
    found, errors = collector.collect_all(llm, cfg, sources, now.date(), only)
    inbox_files = []
    if not args.mock:
        try:
            extra, inbox_files = collector.collect_inbox(llm, cfg, now.date())
            for k in ("items", "people", "events", "groups"):
                found[k].extend(extra.get(k) or [])
        except Exception as exc:
            errors.append(f"inbox: {exc}")

    # 2. Store, dedupe and tag
    new_items = []
    for it in found["items"]:
        if store.add_item(it, run_id, today):
            new_items.append(it)
    for p in found["people"]:
        store.upsert_person(p, today)
    for e in found["events"]:
        if e.get("date", "") >= today:
            store.add_event(e, today)
    for g in found["groups"]:
        if g.get("url"):
            store.upsert_group(g, today)
    store.commit()
    log.info("Items: %d found, %d new", len(found["items"]), len(new_items))

    # 3. Trends over the stored history
    tr = trends.analyse(store, today, cfg)

    # 4. Ideas, people, weekly strategy
    ledger = store.idea_ledger()
    evidence = new_items or store.items_between(tr["window"]["current"][0], today)
    ideas = analyst.analyse_ideas(llm, cfg, founder, evidence, tr, ledger)
    for idea in ideas["ideas"]:
        store.save_idea(idea, run_id, today)
    store.commit()
    ledger = store.idea_ledger()
    try:
        people = analyst.match_people(llm, cfg, founder, store.people(), store.upcoming_events(today),
                                      store.groups(), evidence, today)
    except Exception as exc:
        log.exception("People matcher failed")
        errors.append(f"people: {exc}")
        people = {"people": [], "groups": [], "events": [], "follow": []}
    strategy = None
    if weekly:
        try:
            strategy = analyst.weekly_strategy(llm, cfg, founder, ledger, tr)
        except Exception as exc:
            log.exception("Weekly strategy failed")
            errors.append(f"strategy: {exc}")

    # 5. Report: Markdown committed to the repo, HTML email
    cost = llm.cost(cfg["pricing"])
    stats = {"groups": len(only or sources["groups"]), "seen": len(found["items"]), "new": len(new_items),
             "cost_aud": cost["aud"], "errors": errors, "notes": found["notes"]}
    md = report.build_markdown(today, ideas, people, tr, ledger, strategy, stats)
    reports_dir = (out_dir if args.mock else ROOT) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"{today}.md").write_text(md, encoding="utf-8")
    (reports_dir / "latest.md").write_text(md, encoding="utf-8")
    report.send(report.subject_line(cfg["email"]["subject_prefix"], today, ideas, bool(strategy)), md,
                report.recipient(cfg), outbox)
    store.save_run(run_id, today, len(found["items"]), len(new_items), cost["aud"], errors)

    if not args.dry_run:   # processed hand-pasted posts move out of the inbox
        done = ROOT / "inbox" / "processed"
        for f in inbox_files:
            done.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), done / f"{today}_{f.name}")
    log.info("Done. Cost AUD %.2f. Errors: %s", cost["aud"], errors or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
