from datetime import date
from io import BytesIO

import pytest
from docx import Document

from core import analyze, export, llm, sample, store as store_mod, transcript as T

TODAY = date(2026, 11, 4)  # Wednesday


# ---------- transcript parsing ----------
def test_fmt_ts():
    assert T.fmt_ts(75) == "1:15" and T.fmt_ts(3725) == "1:02:05" and T.fmt_ts(None) == ""


def test_parse_timestamped_text_and_continuations():
    segs = T.parse_transcript("[00:00:05] Meera: Hello team.\n[00:01:10] Alex: I'll send it\nby Friday.\nRahul (00:02:00): Sounds good.\n00:03:00 Priya: Done.")
    assert [(s["speaker"], s["start"]) for s in segs] == [("Meera", 5), ("Alex", 70), ("Rahul", 120), ("Priya", 180)]
    assert segs[1]["text"] == "I'll send it by Friday."
    assert [s["idx"] for s in segs] == [0, 1, 2, 3]


def test_parse_vtt_with_voice_tags_merging_and_cue_ids():
    vtt = ("WEBVTT\n\nNOTE a comment\n\n1\n00:00:01.000 --> 00:00:03.000\n<v Dana>Hello</v>\n\n2\n00:00:03.500 --> 00:00:05.000\n<v Dana>everyone.</v>\n\n"
           "3\n00:00:20.000 --> 00:00:22.000\nAlex: A new turn.\n")
    segs = T.parse_transcript(vtt)
    assert len(segs) == 2 and segs[0]["text"] == "Hello everyone." and segs[0]["speaker"] == "Dana" and segs[0]["end"] == 5
    assert segs[1]["speaker"] == "Alex" and segs[1]["start"] == 20


def test_parse_srt_and_hour_timestamps():
    srt = "1\n01:02:03,000 --> 01:02:05,500\nSam: Long meeting.\n"
    seg = T.parse_transcript(srt)[0]
    assert seg["start"] == 3723 and seg["speaker"] == "Sam"


def test_parse_plain_labels_notes_and_annotations():
    segs = T.parse_transcript("Meera: Let's begin.\nAction: Alex to send the report.\n\nDecision: use option B\njust a loose note line")
    assert segs[0]["speaker"] == "Meera"
    assert segs[1]["speaker"] == "" and segs[1]["text"].startswith("Action:")
    assert any(s["text"].startswith("Decision:") for s in segs) and all(s["speaker"] != "Decision" for s in segs)
    assert T.parse_transcript("") == [] and T.parse_transcript("   \n\n") == []


def test_read_file_encodings_and_docx():
    assert T.read_file("a.txt", "héllo".encode("utf-8")) == "héllo"
    assert T.read_file("a.vtt", "\ufeffWEBVTT".encode("utf-8")).startswith("WEBVTT")
    assert "ñ" in T.read_file("a.txt", "ñ".encode("utf-16"))
    d = Document()
    d.add_paragraph("Alex: Hello")
    buf = BytesIO()
    d.save(buf)
    assert T.read_file("m.docx", buf.getvalue()).strip() == "Alex: Hello"
    with pytest.raises(ValueError):
        T.read_file("a.pdf", b"")


# ---------- analysis ----------
def _an(text, participants=()):
    segs = T.parse_transcript(text)
    return analyze.analyze_meeting(segs, TODAY, participants)


def test_decisions_found_and_negations_ignored():
    a = _an("Meera: We decided to use the warehouse export.\nRahul: Finance has not approved the budget yet.\nMeera: Let's go with option B.\nAlex: Should we decide today?")
    texts = [d["text"] for d in a["decisions"]]
    assert any("warehouse export" in t for t in texts) and any("option B" in t for t in texts)
    assert not any("not approved" in t for t in texts) and not any(t.endswith("?") for t in texts)


def test_actions_owner_due_and_reply_resolution():
    a = _an("Meera: Priya, can you update the KPI definitions?\nPriya: Sure, I'll update the KPI definitions by Thursday.\nAlex: I will send the report tomorrow.\n"
            "Meera: Rahul will fix the bug by next Tuesday.\nMeera: We need to review the budget.", participants=["Rahul"])
    got = {x["owner"]: x for x in a["actions"]}
    assert set(got) == {"Priya", "Alex", "Rahul", "Team"}
    assert got["Priya"]["due"] == date(2026, 11, 5) and got["Alex"]["due"] == date(2026, 11, 5) and got["Rahul"]["due"] == date(2026, 11, 10)
    assert len([x for x in a["actions"] if "KPI" in x["text"]]) == 1  # ask + reply merged


def test_unknown_capitalised_word_is_not_an_owner_and_weekday_rollover():
    a = _an("Meera: Dashboard will be ready soon enough.\nMeera: I'll send the notes by Wednesday.")
    assert [x["owner"] for x in a["actions"]] == ["Meera"]
    assert a["actions"][0]["due"] == date(2026, 11, 11)  # said on a Wednesday -> next Wednesday


def test_explicit_action_lines_and_unassigned():
    a = _an("Action: Alex to send the final report by Friday.\nAction item: review the contract draft")
    owners = {x["owner"] for x in a["actions"]}
    assert "Alex" in owners or "Unassigned" in owners
    assert any("contract" in x["text"] and x["owner"] == "Unassigned" for x in a["actions"])


def test_questions_open_items_and_stats():
    a = _an("Meera: What is the plan for the launch date?\nAlex: Not sure yet, that is a parking lot item for later.\nMeera: Okay.")
    assert any("launch date" in q["text"] for q in a["questions"]) and a["open_items"]
    sp = {s["name"]: s for s in a["speakers"]}
    assert sp["Meera"]["turns"] == 2 and sum(s["share"] for s in a["speakers"]) in (99, 100, 101)


def test_health_flags_dominance_and_missing_decisions():
    a = _an("Meera: " + "We talked for a long time about many details. " * 20 + "\nAlex: ok.")
    assert not any(c["ok"] for c in a["health"]["checks"][:2]) and a["health"]["score"] < 60
    assert any("talking" in c["label"] for c in a["health"]["checks"])


def test_sample_meetings_end_to_end():
    res = {}
    for m in sample.sample_meetings(TODAY):
        res[m["title"]] = analyze.analyze_meeting(T.parse_transcript(m["text"]), m["date"], m["participants"])
    sp = res["Sprint planning"]
    assert any("warehouse export" in d["text"] for d in sp["decisions"]) and not any("not approved" in d["text"] for d in sp["decisions"])
    owners = {a["owner"]: a for a in sp["actions"]}
    assert {"Alex", "Rahul", "Priya", "Meera"} <= set(owners) and owners["Alex"]["due"] == date(2026, 10, 23)
    assert "dashboard" in sp["keywords"]
    client = res["Client sync: Northwind"]
    assert {a["owner"] for a in client["actions"]} >= {"Alex", "Dana"} and client["duration"] == 55
    assert any("budget" in d["text"] for d in client["decisions"])
    retro = res["Sprint retro"]
    assert any("review slot" in d["text"] for d in retro["decisions"]) and retro["open_items"]


# ---------- store ----------
@pytest.fixture
def store(tmp_path):
    s = store_mod.MeetingStore(tmp_path / "m.db")
    for m in sample.sample_meetings(TODAY):
        segs = T.parse_transcript(m["text"])
        a = analyze.analyze_meeting(segs, m["date"], m["participants"])
        items = ([{"kind": "decision", "text": d["text"], "ts": d["ts"], "idx": d["idx"]} for d in a["decisions"]]
                 + [{"kind": "action", "text": x["text"], "owner": x["owner"], "due": x["due"].isoformat() if x["due"] else None, "ts": x["ts"], "idx": x["idx"]} for x in a["actions"]])
        s.add_meeting(m["title"], m["date"].isoformat(), a["participants"], m["file"], " ".join(a["summary"]), segs, items, a["health"]["score"], a["duration"])
    return s


def test_store_listing_and_counts(store):
    ms = store.meetings()
    assert [m["title"] for m in ms] == ["Sprint retro", "Client sync: Northwind", "Sprint planning"]  # newest first
    assert ms[2]["actions"] >= 4 and ms[2]["decisions"] >= 2 and "Meera" in ms[2]["participants"]
    assert store.get(ms[0]["id"])["title"] == "Sprint retro" and store.get(999) is None
    assert len(store.segments(ms[2]["id"])) == 12


def test_search_ranks_and_scopes(store):
    hits = store.search("What did we decide about the launch date?")
    assert hits and all("launch" in h["text"].lower() for h in hits)
    assert hits[0]["title"] and "[" in hits[0]["snippet"]
    only = store.search("dashboard", meeting_id=store.meetings()[1]["id"])
    assert only and {h["meeting_id"] for h in only} == {store.meetings()[1]["id"]}
    assert store.search("zzzznotfound") == [] and store.search("the a of") == [] and store.search("") == []
    assert store.search("review slots")  # prefix/stemming


def test_search_special_characters_are_safe(store):
    for q in ['"', "AND OR NOT", "dash*board", "a'; DROP TABLE meetings;--", "(((", "col:umn"]:
        store.search(q)
    assert len(store.meetings()) == 3


def test_like_fallback_matches_fts(tmp_path, monkeypatch):
    s = store_mod.MeetingStore(tmp_path / "x.db")
    s.fts = False
    segs = T.parse_transcript("Alex: The dashboard launch is on the 20th.\nMeera: Agreed on the launch.")
    s.add_meeting("M", "2026-11-01", ["Alex"], "x", "", segs, [])
    assert len(s.search("launch dashboard")) == 1 and len(s.search("launch")) == 2


def test_items_status_update_delete(store):
    acts = store.items(kind="action", status="open")
    assert acts and store.items(kind="action", owner="Alex")
    iid = acts[0]["id"]
    store.set_status(iid, "done")
    assert store.items(kind="action", status="done")[0]["done_at"] is not None
    store.set_status(iid, "open")
    assert [i for i in store.items() if i["id"] == iid][0]["done_at"] is None
    store.update_item(iid, owner="Zed", bogus="x")
    assert [i for i in store.items() if i["id"] == iid][0]["owner"] == "Zed"
    assert store.search_items("launch date", kind="decision")
    mid = store.meetings()[0]["id"]
    store.delete_meeting(mid)
    assert len(store.meetings()) == 2 and store.items(meeting_id=mid) == [] and store.segments(mid) == []
    assert all(h["meeting_id"] != mid for h in store.search("retro dashboard"))


def test_trend(store):
    tr = store.trend("dashboard")
    assert [t["date"] for t in tr] == sorted(t["date"] for t in tr) and all(t["hits"] > 0 for t in tr)
    assert all(t["hits"] == 0 for t in store.trend("nonexistentword"))


# ---------- export ----------
def test_exports(store):
    m = store.meetings()[2]
    items = store.items(meeting_id=m["id"])
    md = export.minutes_md(m, items)
    assert md.startswith("# Sprint planning") and "## Decisions" in md and "## Action items" in md and "**Alex**" in md
    fu = export.followup_template(m, items, "Friendly", "Alex")
    assert fu["subject"].startswith("Follow-up: Sprint planning") and "Hi all," in fu["body"] and "Action items:" in fu["body"]
    all_items = store.items(kind="action")
    csv_text = export.actions_csv(all_items)
    assert csv_text.splitlines()[0] == "meeting,meeting_date,owner,task,due,status" and "Alex" in csv_text
    lines = export.quickadd_lines(all_items, owner="Alex")
    assert lines and all(l.endswith("#meetings") for l in lines) and any(" by 2026-" in l for l in lines)
    store.set_status(all_items[0]["id"], "done")
    assert len(export.quickadd_lines(store.items(kind="action"))) == len(all_items) - 1


# ---------- llm ----------
def test_llm_normalizers_and_merge():
    assert llm.parse_json('x {"a": 1} y') == {"a": 1}
    with pytest.raises(ValueError):
        llm.parse_json("nope")
    c = llm.normalize_chunk({"summary": " s ", "decisions": ["A", ""], "action_items": [{"task": "T", "owner": "", "due": "2026-11-05"}, {"task": "U", "due": "soon"}, {"task": ""}, "bad"], "topics": "x"})
    assert c["summary"] == "s" and c["decisions"] == ["A"] and c["topics"] == []
    assert [(a["owner"], a["due"]) for a in c["action_items"]] == [("Unassigned", "2026-11-05"), ("Unassigned", "")]
    merged = llm.merge_chunks([c, llm.normalize_chunk({"summary": "t", "decisions": ["a"], "action_items": [{"task": "t!"}]})], "overall")
    assert merged["summary"] == "overall" and merged["decisions"] == ["A"] and len(merged["action_items"]) == 2
    assert llm.normalize_answer({"answer": " x ", "cited": [1, "2", 9, "z", 1]}, 3) == {"answer": "x", "cited": [1, 2]}


def test_chunking_covers_all_text():
    segs = [{"idx": i, "start": i, "speaker": "A", "text": "word " * 100} for i in range(30)]
    chunks = llm.chunk_transcript(segs, max_chars=2000)
    assert len(chunks) > 3 and all(len(c) <= 2100 for c in chunks)
    assert sum(c.count("word") for c in chunks) == 3000
    assert llm.chunk_transcript([{"idx": 0, "start": None, "speaker": "", "text": "x" * 5000}], 2000)[0].startswith("x")
    assert llm.chunk_transcript([], 100) == []


def test_generate_minutes_chunked_with_fake_model(monkeypatch):
    calls = []

    def fake(prompt, model, host, temperature):
        calls.append(prompt)
        if "Combine these" in prompt:
            return {"summary": "Overall."}
        return {"summary": "part", "decisions": ["Same decision"], "action_items": [{"task": "Do X", "owner": "Alex", "due": ""}]}
    monkeypatch.setattr(llm, "_chat", fake)
    segs = [{"idx": i, "start": i, "speaker": "A", "text": "word " * 100} for i in range(30)]
    mins, truncated = llm.generate_minutes(segs, "T", "2026-11-01")
    assert mins["summary"] == "Overall." and mins["decisions"] == ["Same decision"] and len(mins["action_items"]) == 1 and not truncated
    assert len(calls) == len(llm.chunk_transcript(segs)) + 1
    big = [{"idx": i, "start": i, "speaker": "A", "text": "w" * 4000} for i in range(60)]
    _, truncated = llm.generate_minutes(big, "T", "2026-11-01")
    assert truncated


def test_segment_index_exists_for_fast_search(store):
    import sqlite3
    sql = " ".join(r[0] or "" for r in sqlite3.connect(store.path).execute("SELECT sql FROM sqlite_master WHERE type='index'"))
    assert "segments(meeting_id, idx)" in sql  # the FTS join needs this, otherwise search is O(n^2)
