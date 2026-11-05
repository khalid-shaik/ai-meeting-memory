import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from core import analyze, export, llm, sample, store as store_mod, transcript

st.set_page_config(page_title="AI Meeting Memory", page_icon="🧠", layout="wide")
DB_PATH = Path(os.environ.get("MEETING_DB", Path(__file__).parent / "data" / "meetings.db"))
store = store_mod.MeetingStore(DB_PATH)
TODAY = date.today()
ss = st.session_state
for k, v in {"draft": None, "flash": "", "ver": 0, "title": "", "m_date": TODAY, "people": "", "paste": "", "followup": None,
             "qa_result": None, "sel": None, "sender": ""}.items():
    ss.setdefault(k, v)


# ---------- helpers ----------
def ai_cfg():
    return ss.get("model", "llama3.2"), ss.get("host")


def items_df(a: dict) -> pd.DataFrame:
    rows = [{"keep": True, "kind": "decision", "text": d["text"], "owner": "", "due": None, "ts": d["ts"], "idx": d["idx"]} for d in a["decisions"]]
    rows += [{"keep": True, "kind": "action", "text": x["text"], "owner": x["owner"], "due": x["due"], "ts": x["ts"], "idx": x["idx"]} for x in a["actions"]]
    rows += [{"keep": False, "kind": "question", "text": q["text"], "owner": "", "due": None, "ts": q["ts"], "idx": q["idx"]} for q in a["open_items"] + a["questions"][:5]]
    return pd.DataFrame(rows, columns=["keep", "kind", "text", "owner", "due", "ts", "idx"])


def fmt_due(v):
    return None if v is None or (not isinstance(v, date) and pd.isna(v)) else pd.Timestamp(v).date().isoformat()


# ---------- callbacks ----------
def analyze_cb():
    ss.flash = ""
    up = ss.get("upload")
    try:
        text = transcript.read_file(up.name, up.getvalue()) if up else ss.paste
        name = up.name if up else "pasted"
        segs = transcript.parse_transcript(text)
        if not segs:
            ss.flash = "No transcript text found."
            return
        title = ss.title.strip() or (Path(name).stem.replace("_", " ").title() if up else "Untitled meeting")
        people = [p.strip() for p in ss.people.split(",") if p.strip()]
        a = analyze.analyze_meeting(segs, ss.m_date, people)
        df = items_df(a)
        ss.draft = {"title": title, "date": ss.m_date, "segments": segs, "a": a, "source": name, "ai": None, "people": people}
        ss.items_src, ss.items_live = df, df
        ss.ver += 1
    except Exception as e:
        ss.flash = f"Could not read the file: {e}"


def ai_minutes_cb():
    d = ss.draft
    try:
        model, host = ai_cfg()
        mins, truncated = llm.generate_minutes(d["segments"], d["title"], d["date"], model, host)
        d["ai"] = mins
        ss.flash = "AI minutes ready." + (" Only the first part of a very long transcript was processed." if truncated else "")
    except Exception as e:
        ss.flash = f"AI unavailable ({e}). Showing the offline analysis."


def use_ai_items_cb():
    d = ss.draft
    if not d or not d["ai"]:
        return
    rows = [{"keep": True, "kind": "decision", "text": t, "owner": "", "due": None, "ts": None, "idx": None} for t in d["ai"]["decisions"]]
    for x in d["ai"]["action_items"]:
        rows.append({"keep": True, "kind": "action", "text": x["task"], "owner": x["owner"], "due": date.fromisoformat(x["due"]) if x["due"] else None, "ts": None, "idx": None})
    rows += [{"keep": False, "kind": "question", "text": t, "owner": "", "due": None, "ts": None, "idx": None} for t in d["ai"]["open_questions"]]
    df = pd.DataFrame(rows, columns=["keep", "kind", "text", "owner", "due", "ts", "idx"])
    ss.items_src, ss.items_live = df, df
    ss.ver += 1


def save_cb():
    d = ss.draft
    live = ss.items_live
    items = []
    for _, r in live.iterrows():
        if r["keep"] and str(r["text"]).strip():
            ts = None if pd.isna(r["ts"]) else int(r["ts"])
            idx = None if pd.isna(r["idx"]) else int(r["idx"])
            items.append({"kind": r["kind"], "text": str(r["text"]).strip(), "owner": str(r["owner"] or "").strip(), "due": fmt_due(r["due"]), "ts": ts, "idx": idx})
    summary = (d["ai"]["summary"] if d["ai"] and d["ai"]["summary"] else " ".join(d["a"]["summary"]))
    a = d["a"]
    store.add_meeting(d["title"], d["date"].isoformat(), a["participants"], d["source"], summary, d["segments"], items, a["health"]["score"], a["duration"])
    ss.draft, ss.title, ss.paste = None, "", ""
    ss.flash = f"Saved '{d['title']}' with {len(items)} items."


def discard_cb():
    ss.draft = None


def load_sample_cb():
    existing = {(m["title"], m["date"]) for m in store.meetings()}
    n = 0
    for m in sample.sample_meetings(TODAY):
        if (m["title"], m["date"].isoformat()) in existing:
            continue
        segs = transcript.parse_transcript(m["text"])
        a = analyze.analyze_meeting(segs, m["date"], m["participants"])
        items = ([{"kind": "decision", "text": d["text"], "owner": "", "due": None, "ts": d["ts"], "idx": d["idx"]} for d in a["decisions"]]
                 + [{"kind": "action", "text": x["text"], "owner": x["owner"], "due": x["due"].isoformat() if x["due"] else None, "ts": x["ts"], "idx": x["idx"]} for x in a["actions"]]
                 + [{"kind": "question", "text": q["text"], "owner": "", "due": None, "ts": q["ts"], "idx": q["idx"]} for q in a["open_items"]])
        store.add_meeting(m["title"], m["date"].isoformat(), a["participants"], m["file"], " ".join(a["summary"]), segs, items, a["health"]["score"], a["duration"])
        n += 1
    ss.flash = f"Loaded {n} sample meeting(s)."


def save_actions_cb():
    live = ss.get("actions_live")
    if live is None:
        return
    for _, r in live.iterrows():
        iid = int(r["id"])
        store.update_item(iid, owner=str(r["owner"] or "").strip(), due=fmt_due(r["due"]), text=str(r["text"]).strip() or "(empty)")
        store.set_status(iid, "done" if r["done"] else "open")
    ss.flash = "Action items updated."


def delete_meeting_cb(mid):
    store.delete_meeting(mid)
    ss.flash = "Meeting deleted."


def ask_cb():
    q = ss.question.strip()
    ss.qa_result = None
    if not q:
        return
    hits = store.search(q, limit=8)
    items = store.search_items(q, limit=8)
    res = {"q": q, "hits": hits, "items": items, "ai": None}
    if ss.get("use_ai") and hits:
        sources = [f"{h['title']} ({h['date']}) {transcript.fmt_ts(h['start'])} {h['speaker']}: {h['text']}" for h in hits]
        try:
            model, host = ai_cfg()
            res["ai"] = llm.answer_question(q, sources, model, host)
        except Exception as e:
            ss.flash = f"AI unavailable ({e}). Showing search results."
    ss.qa_result = res


def followup_cb(m, its):
    fb = export.followup_template(m, its, ss.fu_tone, ss.sender)
    out = fb
    if ss.get("use_ai"):
        facts = export.minutes_md(m, its)
        try:
            model, host = ai_cfg()
            out = llm.draft_followup(facts, ss.fu_tone, fb, model, host)
        except Exception as e:
            ss.flash = f"AI unavailable ({e}). Used the template."
    ss.fu_subject, ss.fu_body = out["subject"], out["body"]


# ---------- sidebar ----------
with st.sidebar:
    st.header("⚙️ Settings")
    host = st.text_input("Ollama host", value="http://localhost:11434", key="host")
    installed = llm.list_models(host)
    if installed:
        st.selectbox("Model", installed, key="model")
        st.checkbox("Use AI features", value=True, key="use_ai")
    else:
        ss["use_ai"] = False
        st.caption("Ollama not detected. Everything works offline; AI adds fluent minutes and answers.")
    st.text_input("Your name (follow-up emails)", key="sender")
    st.divider()
    st.button("Load sample meetings", on_click=load_sample_cb, use_container_width=True)
    st.caption(f"Your meeting memory is stored locally in {DB_PATH.name}.")

st.title("🧠 AI Meeting Memory")
if ss.flash:
    st.toast(ss.flash)
    ss.flash = ""

meetings = store.meetings()
all_actions = store.items(kind="action")
t_new, t_meet, t_act, t_ask, t_ins = st.tabs(["➕ New meeting", "🗂️ Meetings", "✅ Action tracker", "🔎 Ask your memory", "📊 Insights"])

# ---------- new meeting ----------
with t_new:
    if ss.draft is None:
        a1, b1, c1 = st.columns([2, 1, 2])
        a1.text_input("Meeting title", key="title", placeholder="e.g. Sprint planning")
        b1.date_input("Date", key="m_date")
        c1.text_input("Attendees (optional, comma separated)", key="people")
        st.file_uploader("Upload a transcript (.txt, .vtt, .srt, .md, .docx)", type=["txt", "vtt", "srt", "md", "docx"], key="upload")
        st.text_area("...or paste a transcript or your notes", key="paste", height=220,
                     placeholder="[00:01:10] Alex: I'll send the report by Friday.\nMeera: We decided to go with option B.")
        st.button("🔍 Analyze meeting", type="primary", on_click=analyze_cb, disabled=not (ss.get("upload") or ss.paste.strip()))
        st.caption("Supports Teams / Zoom / Meet .vtt, .srt, '[00:01:10] Name: text', 'Name: text' and plain notes.")
    else:
        d, a = ss.draft, ss.draft["a"]
        st.subheader(d["title"])
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Speakers", len(a["speakers"]))
        m2.metric("Decisions", len(a["decisions"]))
        m3.metric("Action items", len(a["actions"]))
        m4.metric("Duration", transcript.fmt_ts(a["duration"]) if a["duration"] else "n/a")
        m5.metric("Meeting health", f"{a['health']['score']}/100")
        st.markdown("**Summary**")
        for s_ in a["summary"] or ["(too little text to summarize)"]:
            st.write(f"- {s_}")
        if ss.get("use_ai"):
            st.button("✨ Generate AI minutes", on_click=ai_minutes_cb)
        if d["ai"]:
            st.success(d["ai"]["summary"] or "AI minutes generated.")
            if d["ai"]["topics"]:
                st.caption("Topics: " + ", ".join(d["ai"]["topics"]))
            for r in d["ai"]["risks"]:
                st.write(f"⚠️ {r}")
            st.button("Use the AI decisions and action items below", on_click=use_ai_items_cb)
        l, r = st.columns(2)
        with l:
            st.markdown("**Topic flow**")
            for t in a["topics"]:
                st.write(f"- {transcript.fmt_ts(t['start']) or '·'}  {t['label']}")
            st.markdown("**Meeting health**")
            for c in a["health"]["checks"]:
                st.write(("✅ " if c["ok"] else "⚠️ ") + c["label"])
        with r:
            st.markdown("**Who spoke**")
            if a["speakers"]:
                st.bar_chart(pd.Series({s["name"]: s["share"] for s in a["speakers"]}, name="% of words"))
        st.markdown("**Extracted items** (edit, untick what you do not want to keep)")
        st.caption("Decisions and action items are pre-selected. Open questions are optional.")
        ss.items_live = st.data_editor(
            ss.items_src, hide_index=True, use_container_width=True, num_rows="dynamic", key=f"items_{ss.ver}", disabled=["ts", "idx"],
            column_config={"keep": st.column_config.CheckboxColumn("Save?"), "kind": st.column_config.SelectboxColumn("Type", options=["decision", "action", "question"], required=True),
                           "text": st.column_config.TextColumn("Text", width="large"), "due": st.column_config.DateColumn("Due"),
                           "ts": None, "idx": None})
        c1, c2, _ = st.columns([1, 1, 4])
        c1.button("💾 Save to memory", type="primary", on_click=save_cb)
        c2.button("Discard", on_click=discard_cb)

# ---------- meetings ----------
with t_meet:
    if not meetings:
        st.info("No meetings saved yet. Add one in **New meeting** or click **Load sample meetings** in the sidebar.")
    else:
        df = pd.DataFrame([{"Date": m["date"], "Title": m["title"], "People": len(m["participants"]), "Decisions": m["decisions"], "Actions": m["actions"],
                            "Health": m["health"]} for m in meetings])
        st.dataframe(df, use_container_width=True, hide_index=True)
        mid = st.selectbox("Open a meeting", [m["id"] for m in meetings], format_func=lambda i: next(f"{m['date']}  ·  {m['title']}" for m in meetings if m["id"] == i), key="sel_m")
        m = next(x for x in meetings if x["id"] == mid)
        its = store.items(meeting_id=mid)
        st.markdown(f"### {m['title']}  \n*{m['date']} · {', '.join(m['participants'])}*")
        if m["summary"]:
            st.write(m["summary"])
        for kind, label in (("decision", "Decisions"), ("action", "Action items"), ("question", "Open questions")):
            sub = [i for i in its if i["kind"] == kind]
            if sub:
                st.markdown(f"**{label}**")
                for i in sub:
                    when = f" ({transcript.fmt_ts(i['ts'])})" if i["ts"] is not None else ""
                    extra = f"**{i['owner']}**: " if kind == "action" and i["owner"] else ""
                    due = f" · due {i['due']}" if kind == "action" and i["due"] else ""
                    st.write(f"- {'✅ ' if i['status'] == 'done' else ''}{extra}{i['text']}{due}{when}")
        with st.expander("Transcript"):
            for s in store.segments(mid):
                st.write(f"`{transcript.fmt_ts(s['start'])}` **{s['speaker']}** {s['text']}" if s["speaker"] else f"`{transcript.fmt_ts(s['start'])}` {s['text']}")
        d1, d2 = st.columns(2)
        d1.download_button("📝 Minutes (.md)", export.minutes_md(m, its), f"minutes_{m['date']}_{mid}.md", "text/markdown", use_container_width=True)
        with st.expander("✉️ Follow-up email"):
            st.selectbox("Tone", ["Professional", "Friendly", "Formal"], key="fu_tone")
            st.button("Draft follow-up" + (" (AI)" if ss.get("use_ai") else ""), on_click=followup_cb, args=(m, its), key=f"fu_{mid}")
            if ss.get("fu_body"):
                st.text_input("Subject", key="fu_subject")
                st.text_area("Body (edit freely)", key="fu_body", height=260)
        with st.expander("Danger zone"):
            st.button("Delete this meeting", on_click=delete_meeting_cb, args=(mid,), key=f"del_{mid}")

# ---------- actions ----------
with t_act:
    if not all_actions:
        st.info("No action items yet.")
    else:
        today_iso = TODAY.isoformat()
        open_a = [a for a in all_actions if a["status"] == "open"]
        overdue = [a for a in open_a if a["due"] and a["due"] < today_iso]
        wk = (TODAY - timedelta(days=7)).isoformat()
        m1, m2, m3 = st.columns(3)
        m1.metric("Open", len(open_a))
        m2.metric("Overdue", len(overdue))
        m3.metric("Done in last 7 days", sum(1 for a in all_actions if a["status"] == "done" and (a["done_at"] or "")[:10] >= wk))
        f1, f2, f3 = st.columns(3)
        status_f = f1.selectbox("Status", ["open", "done", "all"], key="status_f")
        owners = sorted({a["owner"] or "Unassigned" for a in all_actions})
        owner_f = f2.multiselect("Owner", owners, default=owners)
        over_only = f3.checkbox("Overdue only")
        shown = [a for a in all_actions if (status_f == "all" or a["status"] == status_f) and (a["owner"] or "Unassigned") in owner_f
                 and (not over_only or a in overdue)]
        if shown:
            df = pd.DataFrame([{"id": a["id"], "done": a["status"] == "done", "owner": a["owner"], "text": a["text"],
                                "due": date.fromisoformat(a["due"]) if a["due"] else None, "meeting": f"{a['meeting_date']} {a['meeting_title']}",
                                "overdue": "⚠️" if a in overdue else ""} for a in shown])
            ss.actions_live = st.data_editor(df, hide_index=True, use_container_width=True, key=f"act_{status_f}_{len(shown)}_{hash(tuple(owner_f))}",
                                             disabled=["id", "meeting", "overdue"], column_config={"id": None, "text": st.column_config.TextColumn(width="large"),
                                                                                                   "due": st.column_config.DateColumn()})
            st.button("💾 Save changes", on_click=save_actions_cb, type="primary")
            st.markdown("**Open actions by owner**")
            st.bar_chart(pd.Series([a["owner"] or "Unassigned" for a in open_a]).value_counts().rename("open"))
        else:
            st.info("Nothing matches these filters.")
        e1, e2 = st.columns(2)
        e1.download_button("📊 Export CSV", export.actions_csv(all_actions), "action_items.csv", "text/csv", use_container_width=True)
        st.markdown("**Send to your task manager** (paste into Project 5's quick-add)")
        st.code("\n".join(export.quickadd_lines(all_actions)) or "No open actions.", language=None)

# ---------- ask memory ----------
with t_ask:
    if not meetings:
        st.info("Save some meetings first, then ask questions across all of them.")
    else:
        st.text_input("Ask your meeting memory", key="question", on_change=ask_cb, placeholder="What did we decide about the launch date?   (press Enter)")
        res = ss.qa_result
        if res:
            if res["ai"] and res["ai"]["answer"]:
                st.success(res["ai"]["answer"])
                if res["ai"]["cited"]:
                    st.caption("Sources: " + ", ".join(f"[{i}] {res['hits'][i - 1]['title']} ({res['hits'][i - 1]['date']})" for i in res["ai"]["cited"]))
            if res["items"]:
                st.markdown("**Decisions and actions that match**")
                for i in res["items"]:
                    st.write(f"- *{i['kind']}* · {i['meeting_date']} · {i['meeting_title']}: {i['text']}" + (f" ({i['owner']})" if i["owner"] else ""))
            if res["hits"]:
                st.markdown("**Relevant moments**")
                for n, h in enumerate(res["hits"], 1):
                    st.write(f"**[{n}] {h['date']} · {h['title']}** · `{transcript.fmt_ts(h['start'])}` {h['speaker']}: {h['snippet']}")
            if not res["hits"] and not res["items"]:
                st.info("Nothing found. Try different keywords.")
        st.divider()
        st.markdown("**Decision log**")
        dec = store.items(kind="decision")
        st.dataframe(pd.DataFrame([{"Date": d["meeting_date"], "Meeting": d["meeting_title"], "Decision": d["text"]} for d in dec]), use_container_width=True, hide_index=True)
        st.markdown("**Topic trend**")
        term = st.text_input("Track a word or phrase across meetings", key="term", placeholder="dashboard")
        if term.strip():
            tr = store.trend(term)
            if any(t["hits"] for t in tr):
                st.bar_chart(pd.DataFrame(tr).set_index("date")["hits"])
            else:
                st.caption("No mentions found.")

# ---------- insights ----------
with t_ins:
    if not meetings:
        st.info("Insights appear once you have saved meetings.")
    else:
        done = [a for a in all_actions if a["status"] == "done"]
        healths = [m["health"] for m in meetings if m["health"] is not None]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Meetings", len(meetings))
        m2.metric("Decisions recorded", sum(m["decisions"] for m in meetings))
        m3.metric("Action completion", f"{round(100 * len(done) / len(all_actions))}%" if all_actions else "n/a")
        m4.metric("Avg meeting health", f"{round(sum(healths) / len(healths))}/100" if healths else "n/a")
        wk = pd.Series([pd.Timestamp(m["date"]).to_period("W").start_time.strftime("%d %b") for m in meetings]).value_counts().sort_index()
        st.markdown("**Meetings per week**")
        st.bar_chart(wk.rename("meetings"))
        st.markdown("**Meeting health over time**")
        st.line_chart(pd.DataFrame([{"date": m["date"], "health": m["health"]} for m in reversed(meetings) if m["health"] is not None]).set_index("date"))
        people = pd.Series([p for m in meetings for p in m["participants"]]).value_counts()
        st.markdown("**Who attends most**")
        st.bar_chart(people.rename("meetings"))
