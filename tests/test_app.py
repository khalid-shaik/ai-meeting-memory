from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import llm, store as store_mod

APP = str(Path(__file__).resolve().parent.parent / "app.py")
TRANSCRIPT = ("[00:00:05] Meera: Let's start with the launch.\n[00:00:30] Meera: We decided to launch on the 20th.\n"
              "[00:01:00] Alex: I'll write the release notes by Friday.\n[00:01:30] Rahul: I will fix the login bug tomorrow.")


@pytest.fixture
def at(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_DB", str(tmp_path / "m.db"))
    monkeypatch.setattr(llm, "list_models", lambda host=None: [])
    app = AppTest.from_file(APP, default_timeout=60).run()
    app.store = store_mod.MeetingStore(tmp_path / "m.db")
    return app


def _click(app, label, sidebar=False):
    pool = app.sidebar.button if sidebar else app.button
    [b for b in pool if label in b.label][0].click().run()


def test_empty_state(at):
    assert not at.exception and len(at.info) >= 3


def test_load_sample_and_views(at):
    _click(at, "Load sample meetings", sidebar=True)
    assert not at.exception and len(at.store.meetings()) == 3
    _click(at, "Load sample meetings", sidebar=True)
    assert len(at.store.meetings()) == 3  # loading twice does not duplicate
    labels = {m.label: m.value for m in at.metric}
    assert labels["Meetings"] == "3" and int(labels["Open"]) >= 5


def test_analyze_edit_and_save_flow(at):
    at.text_input(key="title").set_value("Launch sync")
    at.text_area(key="paste").set_value(TRANSCRIPT).run()
    _click(at, "Analyze meeting")
    assert not at.exception and at.session_state["draft"]["title"] == "Launch sync"
    assert any(m.label == "Action items" and m.value == "2" for m in at.metric)
    _click(at, "Save to memory")
    assert not at.exception and at.session_state["draft"] is None
    [m] = at.store.meetings()
    assert m["title"] == "Launch sync" and m["actions"] == 2 and m["decisions"] == 1
    owners = {i["owner"] for i in at.store.items(kind="action")}
    assert owners == {"Alex", "Rahul"}


def test_empty_or_bad_transcript_is_handled(at):
    at.text_area(key="paste").set_value("   \n  ").run()
    assert [b for b in at.button if "Analyze" in b.label][0].disabled


def test_ask_memory_and_followup(at):
    _click(at, "Load sample meetings", sidebar=True)
    at.text_input(key="question").set_value("What did we decide about the launch date?").run()
    assert not at.exception and at.session_state["qa_result"]["hits"] and at.session_state["qa_result"]["items"]
    _click(at, "Draft follow-up")
    assert not at.exception and "Action items:" in at.session_state["fu_body"]


def test_ai_failure_in_answer_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_DB", str(tmp_path / "m.db"))
    monkeypatch.setattr(llm, "list_models", lambda host=None: ["m:latest"])

    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(llm, "answer_question", boom)
    app = AppTest.from_file(APP, default_timeout=60).run()
    _click(app, "Load sample meetings", sidebar=True)
    app.text_input(key="question").set_value("launch date").run()
    assert not app.exception and app.session_state["qa_result"]["ai"] is None and app.session_state["qa_result"]["hits"]
