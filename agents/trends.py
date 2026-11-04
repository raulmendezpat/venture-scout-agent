"""Stage 2: deterministic trend analysis over the stored history (no model calls)."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

STOP = set("""a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he her here
hers him his how i if in into is it its itself just like more most my new no nor not now of off on once only or other
our out over own same she should so some such than that the their them then there these they this those through to too
under until up very was we were what when where which while who whom why will with would you your australia australian
australians brisbane queensland qld year years one two first said says say get gets via per across many make makes
made use used using way ways time based help helps including include includes well within new latest according
announced launch launched launches today week month news report reports me us vs eg ie st th nd rd""".split())

WORD = re.compile(r"[a-z][a-z0-9+\-]{1,}")


def words(text: str) -> list[str]:
    """Unigrams and bigrams without stop words, unique per text."""
    toks = [t.strip("-") for t in WORD.findall((text or "").lower())]
    toks = [t for t in toks if t and t not in STOP]
    grams = set(toks) | {f"{a} {b}" for a, b in zip(toks, toks[1:])}
    return sorted(grams)


def _windows(today: str, days: int) -> tuple[tuple[str, str], tuple[str, str]]:
    t = date.fromisoformat(today)
    cur = ((t - timedelta(days=days)).isoformat(), today)
    prev = ((t - timedelta(days=2 * days)).isoformat(), cur[0])
    return cur, prev


def rising(cur: dict[str, int], prev: dict[str, int], min_count: int, top: int = 20) -> list[dict]:
    """Terms ranked by smoothed lift of the current window over the previous one, weighted by volume."""
    n_cur, n_prev = max(sum(cur.values()), 1), max(sum(prev.values()), 1)
    out = []
    for term, c in cur.items():
        if c < min_count:
            continue
        p = prev.get(term, 0)
        lift = ((c + 1) / n_cur) / ((p + 1) / n_prev)
        out.append({"term": term, "count": c, "previous": p, "lift": round(lift, 2),
                    "score": round(math.log(lift + 1e-9) * math.log(c + 1), 3)})
    return sorted(out, key=lambda x: -x["score"])[:top]


def analyse(store, today: str, cfg: dict) -> dict:
    days, min_count = cfg["run"]["trend_window_days"], cfg["run"]["min_term_count"]
    (c0, c1), (p0, p1) = _windows(today, days)
    cur_tags, prev_tags = store.term_counts(c0, c1, "tag"), store.term_counts(p0, p1, "tag")
    cur_words, prev_words = store.term_counts(c0, c1, "word"), store.term_counts(p0, p1, "word")
    items = store.items_between(c0, c1)

    by_theme: dict[str, list[float]] = defaultdict(list)
    by_group: dict[str, list[float]] = defaultdict(list)
    for it in items:
        for th in it["themes"]:
            by_theme[th.lower().strip()].append(it["sentiment"])
        by_group[it["source_group"]].append(it["sentiment"])
    themes = sorted(({"theme": t, "mentions": len(v), "sentiment": round(sum(v) / len(v), 2)}
                     for t, v in by_theme.items()), key=lambda x: (-x["mentions"], -x["sentiment"]))

    return {
        "window": {"current": [c0, c1], "previous": [p0, p1], "items": len(items),
                   "ai_share": round(sum(i["ai_related"] for i in items) / len(items), 2) if items else 0},
        "top_tags": Counter(cur_tags).most_common(25),
        "top_words": [w for w in Counter(cur_words).most_common(60) if " " in w[0]][:20]
                     + [w for w in Counter(cur_words).most_common(25) if " " not in w[0]][:15],
        "rising_tags": rising(cur_tags, prev_tags, min_count),
        "rising_words": rising(cur_words, prev_words, min_count),
        "themes": themes[:30],
        "sentiment_by_group": {g: round(sum(v) / len(v), 2) for g, v in by_group.items()},
        "history": history(store, today, cfg),
    }


def _week(d: str) -> str:
    y, w, _ = date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def _slope(vals: list[float]) -> float:
    """Least squares slope per week over the weeks that have data."""
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(pts) < 2:
        return 0.0
    mx, my = sum(i for i, _ in pts) / len(pts), sum(v for _, v in pts) / len(pts)
    den = sum((i - mx) ** 2 for i, _ in pts)
    return round(sum((i - mx) * (v - my) for i, v in pts) / den, 3) if den else 0.0


def history(store, today: str, cfg: dict) -> dict:
    """Whole-history view: cumulative counts, persistent terms, weekly series, theme sentiment over time,
    recurring people. Weeks are ISO weeks; the series cover the last `history_weeks` weeks."""
    h = cfg["run"].get("history_weeks", 8)
    persist_min = cfg["run"].get("persistent_min_weeks", 3)
    t = date.fromisoformat(today)
    weeks = [_week((t - timedelta(weeks=k)).isoformat()) for k in range(h - 1, -1, -1)]
    start = (t - timedelta(weeks=h)).isoformat()
    everything = "0000-00-00"

    cum_tags, cum_words = store.term_counts(everything, today, "tag"), store.term_counts(everything, today, "word")

    # weekly distinct-item counts per term
    series: dict[str, Counter] = defaultdict(Counter)
    for term, _key, d in store.term_rows(start, today, "tag"):
        series[term][_week(d)] += 1
    week_totals = Counter()
    items = store.items_between(start, today)
    for it in items:
        week_totals[_week(it["first_seen_date"])] += 1

    def row(term: str) -> dict:
        counts = [series[term].get(w, 0) for w in weeks]
        share = [round(c / week_totals[w], 3) if week_totals[w] else None for c, w in zip(counts, weeks)]
        return {"term": term, "weekly": counts, "weeks_present": sum(1 for c in counts if c),
                "total": sum(counts), "share_slope": _slope(share)}

    rows = [row(term) for term in series]
    persistent = sorted((r for r in rows if r["weeks_present"] >= persist_min),
                        key=lambda r: (-r["weeks_present"], -r["total"]))[:15]
    gaining = sorted((r for r in rows if r["total"] >= cfg["run"]["min_term_count"] and r["share_slope"] > 0),
                     key=lambda r: -r["share_slope"])[:10]
    fading = sorted((r for r in rows if r["weeks_present"] >= 2 and r["share_slope"] < 0),
                    key=lambda r: r["share_slope"])[:10]

    # theme sentiment by week
    th_week: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for it in items:
        for th in it["themes"]:
            th_week[th.lower().strip()][_week(it["first_seen_date"])].append(it["sentiment"])
    themes = []
    for th, by_w in th_week.items():
        sent = [round(sum(by_w[w]) / len(by_w[w]), 2) if by_w.get(w) else None for w in weeks]
        n = sum(len(v) for v in by_w.values())
        themes.append({"theme": th, "mentions": n, "weeks_present": sum(1 for s in sent if s is not None),
                       "weekly_sentiment": sent, "sentiment_slope": _slope(sent),
                       "avg_sentiment": round(sum(sum(v) for v in by_w.values()) / n, 2)})
    themes.sort(key=lambda x: (-x["weeks_present"], -x["mentions"]))

    return {
        "since": store.first_item_date(), "runs": store.run_count(), "weeks": weeks,
        "items_per_week": [week_totals.get(w, 0) for w in weeks],
        "cumulative_tags": Counter(cum_tags).most_common(20),
        "cumulative_phrases": [w for w in Counter(cum_words).most_common(80) if " " in w[0]][:15],
        "persistent_terms": persistent, "gaining_terms": gaining, "fading_terms": fading,
        "theme_sentiment": themes[:12],
        "recurring_people": store.recurring_people(),
    }
