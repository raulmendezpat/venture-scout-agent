import email
from datetime import date, timedelta

import run
from agents import analyst, trends
from agents.common import load_config
from agents.store import Store


def test_mock_run_end_to_end(tmp_path):
    db = tmp_path / "v.db"
    assert run.main(["--mock", "--weekly", "--db", str(db), "--today", "2026-11-05"]) == 0
    s = Store(str(db))
    assert s.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 4 * 8
    ledger = s.idea_ledger()
    assert {l["title"] for l in ledger} >= {"Recepcionista IA para clínicas"}
    # second run two days later: items dedupe, ideas keep one history row per run
    assert run.main(["--mock", "--db", str(db), "--today", "2026-11-07"]) == 0
    assert s.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 4 * 8
    assert s.db.execute("SELECT COUNT(*) FROM idea_scores").fetchone()[0] == 6


def test_filters_and_evidence():
    cfg = load_config()
    from tests.fakes import fake_llm
    from agents.common import LLM
    items = [{"url": "https://example.org/news/0", "title": "t", "summary": "s"}]
    out = analyst.analyse_ideas(LLM(fake=fake_llm), cfg, {}, items, {}, [])
    by = {i["title"]: i for i in out["ideas"]}
    good = by["Recepcionista IA para clínicas"]
    assert good["passes"] and good["evidence_urls"] == ["https://example.org/news/0"]
    assert not by["Plataforma SaaS de cumplimiento"]["passes"]
    assert "conflicto alto con QFD" in by["Asesoría de resiliencia para consejos municipales"]["fails"]
    assert out["ideas"][0]["title"] == good["title"]


def test_rising_terms(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    cfg = load_config()
    today = date(2026, 11, 20)
    old = (today - timedelta(days=20)).isoformat()
    for n in range(4):
        s.add_item({"url": f"https://a/{n}", "title": "x", "keywords": ["ai agents"], "_words": []}, "r", old)
        s.add_item({"url": f"https://b/{n}", "title": "y", "keywords": ["ai receptionist", "ai agents"], "_words": []},
                   "r", today.isoformat())
    tr = trends.analyse(s, today.isoformat(), cfg)
    assert tr["rising_tags"][0]["term"] == "ai receptionist"


def test_words_drop_stopwords():
    w = trends.words("The new AI receptionist for Brisbane clinics")
    assert "ai receptionist" in w and "the" not in w and "brisbane" not in w


def test_mock_email_written(tmp_path, monkeypatch):
    db = tmp_path / "v.db"
    run.main(["--mock", "--db", str(db), "--today", "2026-11-05"])
    emls = sorted(run.ROOT.glob("out/2026-11-05_*/report.eml"))
    msg = email.message_from_bytes(emls[-1].read_bytes())
    assert "Venture Scout" in msg["Subject"] and "Recepcionista" in msg["Subject"]


def test_email_uses_secrets(monkeypatch):
    from agents import report
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): sent["host"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, m): sent["to"], sent["from"] = m["To"], m["From"]

    monkeypatch.setattr(report.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("GMAIL_USER", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.delenv("EMAIL_TO", raising=False)
    cfg = load_config()
    assert report.recipient(cfg) == "me@gmail.com"
    report.send("s", "# hola", report.recipient(cfg))
    assert sent["login"] == ("me@gmail.com", "abcdefghijklmnop") and sent["to"] == "me@gmail.com"
    monkeypatch.setenv("EMAIL_TO", "other@example.com")
    assert report.recipient(cfg) == "other@example.com"
    assert run.main(["--test-email"]) == 0 and sent["to"] == "other@example.com"


def _msg(content, stop):
    import anthropic
    return anthropic.types.Message.model_validate({
        "id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5", "content": content,
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0}}})


SEARCH = {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
    {"type": "web_search_result", "url": "https://example.org/a", "title": "A", "page_age": "2 days ago",
     "encrypted_content": "x"}]}
SCHEMA = {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}


def _client(responses, calls):
    class M:
        def create(self, **kw):
            calls.append(kw)
            return responses.pop(0) if responses else _msg([{"type": "text", "text": "still thinking"}], "end_turn")

    class C:
        messages = M()
    return C()


def test_truncated_answer_is_retried_with_more_tokens():
    from agents.common import LLM
    calls = []
    cut = _msg([{"type": "tool_use", "id": "t1", "name": "report", "input": {"items": [1]}}], "max_tokens")
    ok = _msg([{"type": "tool_use", "id": "t2", "name": "report", "input": {"items": [1, 2]}}], "tool_use")
    llm = LLM(client=_client([cut, ok], calls))
    out = llm.structured(model="claude-haiku-5-5", system="s", user="u", schema=SCHEMA, name="report",
                         max_tokens=1000, server_tools=[{"type": "web_search_20250305", "name": "web_search"}])
    assert out == {"items": [1, 2]} and calls[1]["max_tokens"] == 2000
    assert calls[1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_salvage_when_model_never_answers():
    from agents.common import LLM
    calls = []
    first = _msg([{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "q"}},
                  SEARCH, {"type": "text", "text": "Found A."}], "end_turn")
    responses = [first] + [_msg([{"type": "text", "text": "more"}], "end_turn") for _ in range(7)]
    responses.append(_msg([{"type": "tool_use", "id": "t9", "name": "report", "input": {"items": ["A"]}}], "tool_use"))
    llm = LLM(client=_client(responses, calls))
    out = llm.structured(model="claude-haiku-5-5", system="s", user="u", schema=SCHEMA, name="report",
                         server_tools=[{"type": "web_search_20250305", "name": "web_search"}])
    assert out == {"items": ["A"]}
    last = calls[-1]
    assert all(t.get("name") != "web_search" for t in last["tools"]) and "https://example.org/a" in last["messages"][0]["content"]
