# 🧠 AI Meeting Memory

**Project 7 of 60 AI agents.** Turn meeting transcripts into minutes, then keep them in a searchable memory so you can ask *"What did we decide about the launch date?"* weeks later. Everything runs locally and **works offline**. Ollama only adds fluent minutes, answers and follow-up emails.

## Features
- **Transcript formats:** Teams / Zoom / Meet `.vtt`, `.srt`, `[00:01:10] Name: text`, `Name: text`, plain notes, `.docx`, or paste
- **Offline minutes:** summary, **decisions**, **action items with owner and due date** (dates resolve against the meeting date), open questions and blockers, topic flow with timestamps, who-spoke chart
- **Meeting health score:** decisions captured, owners and due dates assigned, balanced participation, sensible length, each with a tip
- **Review before saving:** edit, retype or untick anything in the extracted items table
- **Memory (SQLite + FTS5 full-text search):** every transcript line is searchable. Ask in plain English, see matching decisions and the exact moments with timestamps and speakers
- **Action tracker across all meetings:** owners, due dates, overdue flags, mark done, CSV export, quick-add lines for the AI Productivity Manager (Project 5)
- **Decision log and topic trend:** track how often a topic comes up from meeting to meeting
- **Insights:** meetings per week, action completion rate, health over time, who attends most
- **Follow-up email** drafts (template or AI) and Markdown minutes export
- **AI (optional):** long transcripts are processed in chunks and merged; answers cite their sources; nothing is invented

## Tech stack
Python · Streamlit · SQLite (FTS5, with a LIKE fallback) · pandas · python-docx · Ollama (optional) · pytest · GitHub Actions

## Quick start
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```
Click **Load sample meetings** in the sidebar, then try **Ask your memory** with `What did we decide about the launch date?`. For AI features install Ollama and run `ollama pull llama3.2`.

## Getting a transcript
- **Microsoft Teams:** Meeting → Recap → Transcript → Download (.vtt or .docx)
- **Zoom:** Recordings → Audio transcript → download the `.vtt`
- **Google Meet:** transcripts are saved as a Google Doc, so download it as `.docx` or `.txt`
- **No transcript?** Paste your own notes. Lines like `Action: Alex to send the report by Friday` and `Decision: use option B` are understood.

## Project structure
```
app.py             Streamlit UI (5 tabs)
core/transcript.py VTT / SRT / text parsing into speaker segments
core/analyze.py    decisions, actions, questions, topics, speaker stats, health
core/dates.py      deadline detection relative to the meeting date
core/store.py      SQLite memory with full-text search
core/llm.py        optional Ollama helpers (chunked minutes, Q&A, follow-up)
core/export.py     minutes, follow-up email, CSV, quick-add lines
core/sample.py     three sample meetings
tests/             pytest unit and UI tests
samples/           sample transcripts (.txt, .vtt)
data/              your meeting database (git-ignored)
```

## Limits
- Detection is rule-based and tuned for English. It finds clear commitments ("I'll send...", "Rahul to write...") and decisions ("we decided...") but can miss subtle ones, so review the items before saving.
- It reads transcripts, not audio. Use your meeting tool's transcript or a speech-to-text tool first.
- Speaker names come from the transcript labels.

## Tests
```bash
pytest -q
```

## Roadmap
- [ ] Audio transcription with a local Whisper model
- [ ] Recurring-meeting view that shows which actions carried over
- [ ] Reminders for overdue actions
