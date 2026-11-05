"""Minutes, follow-up email, action CSV and quick-add lines."""
import csv
import io
import re
from datetime import date

from .transcript import fmt_ts


def minutes_md(m: dict, items: list[dict]) -> str:
    dec = [i for i in items if i["kind"] == "decision"]
    act = [i for i in items if i["kind"] == "action"]
    qs = [i for i in items if i["kind"] == "question"]
    out = [f"# {m['title']}", f"*{m['date']} · Attendees: {', '.join(m['participants']) or 'n/a'}*", ""]
    if m.get("summary"):
        out += ["## Summary", m["summary"], ""]
    if dec:
        out += ["## Decisions"] + [f"- {d['text']}" + (f" ({fmt_ts(d['ts'])})" if d.get("ts") is not None else "") for d in dec] + [""]
    if act:
        out += ["## Action items"] + [f"- [{'x' if a['status'] == 'done' else ' '}] **{a['owner'] or 'Unassigned'}**: {a['text']}" + (f" (due {a['due']})" if a.get("due") else "") for a in act] + [""]
    if qs:
        out += ["## Open questions"] + [f"- {q['text']}" for q in qs] + [""]
    return "\n".join(out)


def followup_template(m: dict, items: list[dict], tone: str = "Professional", sender: str = "") -> dict:
    greet, close = {"Friendly": ("Hi all,", "Thanks, everyone!"), "Professional": ("Hello all,", "Thank you all for your time."),
                    "Formal": ("Dear colleagues,", "Thank you for your participation.")}.get(tone, ("Hello all,", "Thank you."))
    dec = [i for i in items if i["kind"] == "decision"]
    act = [i for i in items if i["kind"] == "action" and i["status"] != "done"]
    body = [greet, "", f"Thanks for joining {m['title']} on {m['date']}. Here is a quick recap."]
    if m.get("summary"):
        body += ["", m["summary"]]
    if dec:
        body += ["", "Decisions:"] + [f"- {d['text']}" for d in dec]
    if act:
        body += ["", "Action items:"] + [f"- {a['owner'] or 'Unassigned'}: {a['text']}" + (f" (by {a['due']})" if a.get("due") else "") for a in act]
    body += ["", "Please reply if I missed anything or if any owner or date needs changing.", "", close, sender or "[Your name]"]
    return {"subject": f"Follow-up: {m['title']} ({m['date']})", "body": "\n".join(body)}


def actions_csv(items: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["meeting", "meeting_date", "owner", "task", "due", "status"])
    for i in items:
        if i["kind"] == "action":
            w.writerow([i["meeting_title"], i["meeting_date"], i["owner"], i["text"], i["due"] or "", i["status"]])
    return buf.getvalue()


def quickadd_lines(items: list[dict], owner: str | None = None) -> list[str]:
    """Lines for the AI Productivity Manager quick-add box (Project 5)."""
    lines = []
    for i in items:
        if i["kind"] != "action" or i["status"] == "done" or (owner and i["owner"] != owner):
            continue
        task = re.sub(r"\s+", " ", re.sub(r"[^\w\s,.'&/()-]", "", i["text"])).strip(" .,")
        task = task[:90].rsplit(" ", 1)[0] if len(task) > 90 else task
        lines.append(task + (f" by {i['due']}" if i["due"] else "") + " #meetings")
    return lines
