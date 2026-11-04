"""SQLite history: items, terms, people, events and idea scores across runs. Committed to the repo by CI."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, run_date TEXT, n_items INTEGER, n_new INTEGER,
    cost_aud REAL, errors TEXT);
CREATE TABLE IF NOT EXISTS items (key TEXT PRIMARY KEY, url TEXT, title TEXT, source_group TEXT, source_name TEXT,
    source_type TEXT, published TEXT, summary TEXT, themes TEXT, sentiment REAL, ai_related INTEGER,
    first_seen_run TEXT, first_seen_date TEXT);
CREATE TABLE IF NOT EXISTS item_terms (item_key TEXT, term TEXT, kind TEXT, seen_date TEXT);
CREATE INDEX IF NOT EXISTS ix_terms_date ON item_terms(seen_date);
CREATE TABLE IF NOT EXISTS people (key TEXT PRIMARY KEY, name TEXT, role TEXT, org TEXT, url TEXT, themes TEXT,
    why TEXT, first_seen TEXT, last_seen TEXT, mentions INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (key TEXT PRIMARY KEY, name TEXT, event_date TEXT, location TEXT, url TEXT,
    why TEXT, first_seen TEXT);
CREATE TABLE IF NOT EXISTS groups (key TEXT PRIMARY KEY, name TEXT, platform TEXT, url TEXT, focus TEXT,
    activity TEXT, access TEXT, first_seen TEXT, last_seen TEXT, mentions INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ideas (key TEXT PRIMARY KEY, title TEXT, domain TEXT, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS idea_scores (idea_key TEXT, run_id TEXT, run_date TEXT, composite REAL, passes INTEGER,
    data TEXT, PRIMARY KEY (idea_key, run_id));
"""


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def key_of(*parts: str) -> str:
    return hashlib.sha1("|".join(norm(p) for p in parts).encode()).hexdigest()[:16]


def url_key(url: str, title: str) -> str:
    u = re.sub(r"[?#].*$", "", (url or "").strip().lower()).rstrip("/")
    u = re.sub(r"^https?://(www\.)?", "", u)
    return key_of(u) if u else key_of(title)


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the first runs, keeping the committed history."""
        added = {"people": {"linkedin_url": "TEXT"},
                 "events": {"format": "TEXT", "cost": "TEXT", "organiser": "TEXT"}}
        for table, cols in added.items():
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.db.commit()

    # items ---------------------------------------------------------------------------------------
    def add_item(self, item: dict, run_id: str, today: str) -> bool:
        """Insert an item and its terms. Returns False when the item was already stored."""
        k = url_key(item.get("url", ""), item.get("title", ""))
        if self.db.execute("SELECT 1 FROM items WHERE key=?", (k,)).fetchone():
            return False
        self.db.execute(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (k, item.get("url", ""), item.get("title", ""), item.get("source_group", ""),
             item.get("source_name", ""), item.get("source_type", ""), item.get("published", ""),
             item.get("summary", ""), json.dumps(item.get("themes", [])), float(item.get("sentiment", 0) or 0),
             1 if item.get("ai_related") else 0, run_id, today))
        for term in item.get("keywords", []):
            self.db.execute("INSERT INTO item_terms VALUES (?,?,?,?)", (k, norm(term), "tag", today))
        item["key"] = k
        return True

    def items_between(self, start: str, end: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM items WHERE first_seen_date>? AND first_seen_date<=?",
                               (start, end)).fetchall()
        return [dict(r) | {"themes": json.loads(r["themes"] or "[]")} for r in rows]

    def term_counts(self, start: str, end: str, kind: str) -> dict[str, int]:
        """Distinct items per term. Words and phrases are not stored (they would bloat the database that CI
        commits every run); they are recomputed from each item's title and summary."""
        if kind == "word":
            from .trends import words
            c: dict[str, int] = {}
            for r in self.db.execute("SELECT title, summary FROM items WHERE first_seen_date>? AND first_seen_date<=?",
                                     (start, end)):
                for w in words(f"{r['title']}. {r['summary']}"):
                    c[w] = c.get(w, 0) + 1
            return c
        rows = self.db.execute(
            "SELECT term, COUNT(DISTINCT item_key) n FROM item_terms WHERE kind=? AND seen_date>? AND seen_date<=? "
            "GROUP BY term", (kind, start, end)).fetchall()
        return {r["term"]: r["n"] for r in rows}

    def term_rows(self, start: str, end: str, kind: str) -> list[tuple[str, str, str]]:
        """(term, item_key, seen_date) rows, for per-week history."""
        return [tuple(r) for r in self.db.execute(
            "SELECT DISTINCT term, item_key, seen_date FROM item_terms WHERE kind=? AND seen_date>? AND seen_date<=?",
            (kind, start, end)).fetchall()]

    def first_item_date(self) -> str | None:
        r = self.db.execute("SELECT MIN(first_seen_date) FROM items").fetchone()
        return r[0] if r else None

    def recurring_people(self, min_mentions: int = 2, limit: int = 15) -> list[dict]:
        rows = self.db.execute("SELECT name, role, org, url, mentions, first_seen, last_seen FROM people "
                               "WHERE mentions>=? ORDER BY mentions DESC, last_seen DESC LIMIT ?",
                               (min_mentions, limit))
        return [dict(r) for r in rows]

    def run_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    # people and events ---------------------------------------------------------------------------
    def upsert_person(self, p: dict, today: str) -> None:
        k = key_of(p.get("name", ""), p.get("org", ""))
        row = self.db.execute("SELECT mentions FROM people WHERE key=?", (k,)).fetchone()
        if row:
            self.db.execute("UPDATE people SET last_seen=?, mentions=mentions+1, role=COALESCE(NULLIF(?,''),role), "
                            "url=COALESCE(NULLIF(?,''),url), linkedin_url=COALESCE(NULLIF(?,''),linkedin_url), why=? "
                            "WHERE key=?",
                            (today, p.get("role", ""), p.get("url", ""), p.get("linkedin_url", ""), p.get("why", ""), k))
        else:
            self.db.execute("INSERT INTO people (key, name, role, org, url, themes, why, first_seen, last_seen, mentions, "
                            "linkedin_url) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                            (k, p.get("name", ""), p.get("role", ""), p.get("org", ""), p.get("url", ""),
                             json.dumps(p.get("themes", [])), p.get("why", ""), today, today, p.get("linkedin_url", "")))

    def people(self, limit: int = 60) -> list[dict]:
        rows = self.db.execute("SELECT * FROM people ORDER BY last_seen DESC, mentions DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def add_event(self, e: dict, today: str) -> bool:
        k = key_of(e.get("name", ""), e.get("date", ""))
        if self.db.execute("SELECT 1 FROM events WHERE key=?", (k,)).fetchone():
            return False
        self.db.execute("INSERT INTO events (key, name, event_date, location, url, why, first_seen, format, cost, "
                        "organiser) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (k, e.get("name", ""), e.get("date", ""), e.get("location", ""), e.get("url", ""),
                         e.get("why", ""), today, e.get("format", ""), e.get("cost", ""), e.get("organiser", "")))
        return True

    def upsert_group(self, g: dict, today: str) -> None:
        k = key_of(g.get("platform", ""), g.get("name", ""))
        if self.db.execute("SELECT 1 FROM groups WHERE key=?", (k,)).fetchone():
            self.db.execute("UPDATE groups SET last_seen=?, mentions=mentions+1, url=COALESCE(NULLIF(?,''),url), "
                            "activity=COALESCE(NULLIF(?,''),activity) WHERE key=?",
                            (today, g.get("url", ""), g.get("activity", ""), k))
        else:
            self.db.execute("INSERT INTO groups VALUES (?,?,?,?,?,?,?,?,?,1)",
                            (k, g.get("name", ""), g.get("platform", ""), g.get("url", ""), g.get("focus", ""),
                             g.get("activity", ""), g.get("access", "unknown"), today, today))

    def groups(self, limit: int = 40) -> list[dict]:
        rows = self.db.execute("SELECT * FROM groups ORDER BY mentions DESC, last_seen DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def upcoming_events(self, today: str, days: int = 45) -> list[dict]:
        end = (date.fromisoformat(today) + timedelta(days=days)).isoformat()
        rows = self.db.execute("SELECT * FROM events WHERE event_date>=? AND event_date<=? ORDER BY event_date",
                               (today, end))
        return [dict(r) for r in rows]

    # ideas ---------------------------------------------------------------------------------------
    def save_idea(self, idea: dict, run_id: str, today: str) -> None:
        k = idea.get("id") or key_of(idea.get("title", ""))
        idea["id"] = k
        if self.db.execute("SELECT 1 FROM ideas WHERE key=?", (k,)).fetchone():
            self.db.execute("UPDATE ideas SET last_seen=?, title=? WHERE key=?", (today, idea.get("title", ""), k))
        else:
            self.db.execute("INSERT INTO ideas VALUES (?,?,?,?,?)",
                            (k, idea.get("title", ""), idea.get("domain", ""), today, today))
        self.db.execute("INSERT OR REPLACE INTO idea_scores VALUES (?,?,?,?,?,?)",
                        (k, run_id, today, idea.get("composite", 0), 1 if idea.get("passes") else 0,
                         json.dumps(idea, ensure_ascii=False)))

    def idea_ledger(self, limit: int = 30) -> list[dict]:
        """Latest score of each known idea with its score history (oldest first)."""
        out = []
        rows = self.db.execute(
            "SELECT i.*, (SELECT composite FROM idea_scores s WHERE s.idea_key=i.key ORDER BY run_date DESC LIMIT 1) last_c "
            "FROM ideas i ORDER BY last_seen DESC, last_c DESC LIMIT ?", (limit,)).fetchall()
        for r in rows:
            hist = self.db.execute("SELECT run_date, composite, data FROM idea_scores WHERE idea_key=? "
                                   "ORDER BY run_date", (r["key"],)).fetchall()
            last = json.loads(hist[-1]["data"]) if hist else {}
            out.append({"id": r["key"], "title": r["title"], "domain": r["domain"], "first_seen": r["first_seen"],
                        "history": [(h["run_date"], h["composite"]) for h in hist], "last": last})
        return out

    # runs ----------------------------------------------------------------------------------------
    def save_run(self, run_id: str, today: str, n_items: int, n_new: int, cost_aud: float, errors: list[str]):
        self.db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
                        (run_id, today, n_items, n_new, cost_aud, json.dumps(errors)))
        self.db.execute("DELETE FROM item_terms WHERE kind='word'")   # from databases written before this change
        self.db.commit()
        self.db.execute("VACUUM")

    def commit(self):
        self.db.commit()
