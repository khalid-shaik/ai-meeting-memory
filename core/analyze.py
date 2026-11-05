"""Offline meeting analysis: decisions, action items with owners/dates, questions, topics, speaker stats, health."""
import re
from collections import Counter
from datetime import date, timedelta

from .dates import find_date
from .transcript import fmt_ts

STOP = set("""a an the and or but if then so to of in on at for with from by as is are was were be been it its this that these those i you he she we
they me my your our their him her them us not no yes do does did have has had will would can could should may might just also very more most
some any all about into over under again there here what which who whom when where why how than too out up down off only own same like okay ok
yeah right well um uh going get got let lets one two thing things really think know see want need make good great sure thanks thank please hi
hello morning afternoon everyone team meeting today tomorrow week next last still maybe actually basically able before after while since because every""".split())
SMALLTALK = re.compile(r"^(good (morning|afternoon|evening)|hi|hello|hey|thanks|thank you|okay|ok|sure|great|right|yes|yeah|alright|can you (all )?hear|you're on mute|let's get started|let's start|shall we start|welcome)\b", re.I)
AFFIRM = re.compile(r"^(sure|yes|yep|yeah|okay|ok|will do|on it|got it|no problem|absolutely|of course|can do)\b", re.I)
DECISION = [r"\b(we|team|everyone|all|i)\b.{0,30}\b(decided|agreed|resolved|settled|concluded)\b", r"^(decision|decided|agreed)\s*[:\-]",
            r"\blet'?s (go with|use|proceed with|move forward with|stick with|pick|keep)\b", r"\bwe('ll| will| are going to|'re going to)\s+(go with|use|proceed|adopt|switch|move|launch|ship|keep|stick)\b",
            r"(?<!not )(?<!n't )\b(approved|signed off|final call)\b", r"\bthe plan is\b", r"\bwe agreed\b"]
ACTION_LEAD = re.compile(r"^(action( item)?s?|todo|to-do|ai|next steps?)\s*[:\-]\s*", re.I)
VERBS = r"(send|share|write|fix|update|review|prepare|check|schedule|book|create|draft|finish|complete|follow|contact|call|email|submit|test|deploy|investigate|look|set|add|build|publish|confirm|arrange|reach|get|make|run|document|clean|migrate|raise|ask|provide)"
ACTION_PATTERNS = [
    (re.compile(rf"\b(?:i'll|i will|i can take|i am going to|i'm going to|i'll go ahead and)\s+(?:also\s+)?{VERBS}\b", re.I), "self"),
    (re.compile(rf"^([A-Z][a-z]+(?: [A-Z][a-z]+)?)\s+(?:will|to|should|needs to|is going to|can|'ll)\s+(?:also\s+)?{VERBS}\b"), "named"),
    (re.compile(rf"^([A-Z][a-z]+),\s*(?:can|could|would|will) you\s+(?:also\s+)?{VERBS}\b"), "addressed"),
    (re.compile(rf"^(?:yes|sure|okay|ok|absolutely|of course)[,.]?\s+(?:we|i) (?:can|will|'ll)\s+(?:also\s+)?{VERBS}\b", re.I), "self"),
    (re.compile(rf"\b(?:can|could|would) you\s+(?:also\s+)?{VERBS}\b", re.I), "ask"),
    (re.compile(rf"\b(?:please)\s+{VERBS}\b", re.I), "ask"),
    (re.compile(rf"\b(?:we|someone|somebody) (?:need|needs|have|should|must) to\s+{VERBS}\b", re.I), "team"),
    (re.compile(r"\bfollow[- ]up (?:on|with)\b", re.I), "team"),
]
TEAM_WORDS = {"we", "everyone", "someone", "somebody", "team"}
AFFIRM_LEAD = re.compile(r"^(yes|sure|okay|ok|absolutely|of course)[,.]?\s+", re.I)
WEEKDAY_ONLY = re.compile(r"^(next |this )?(mon|tue|wed|thu|fri|sat|sun)[a-z]*$")
OPEN_ITEM = re.compile(r"\b(parking lot|tbd|to be decided|to be confirmed|revisit|open question|not sure yet|circle back|we still need|unresolved|blocker)\b", re.I)


def _sentences(seg_text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])", seg_text) if s.strip()]


def _walk(segments):
    for k, seg in enumerate(segments):
        for s in _sentences(seg["text"]):
            yield k, seg, s


def _match_owner(name: str, speakers: list[str]) -> str:
    if not name:
        return ""
    cands = [sp for sp in speakers if sp.lower() == name.lower() or sp.lower().split()[0] == name.lower().split()[0]]
    return cands[0] if len(cands) == 1 else name


def find_decisions(segments) -> list[dict]:
    out, seen = [], set()
    for k, seg, s in _walk(segments):
        if s.endswith("?") or len(s.split()) < 4:
            continue
        if any(re.search(p, s, re.I) for p in DECISION):
            key = re.sub(r"\W+", " ", s.lower())[:70]
            if key not in seen:
                seen.add(key)
                out.append({"text": s, "speaker": seg["speaker"], "ts": seg["start"], "idx": seg["idx"]})
    return out


def _content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z-]{2,}", text.lower()) if w not in STOP}


def find_actions(segments, meeting_date: date, speakers: list[str]) -> list[dict]:
    firsts = {sp.lower().split()[0]: sp for sp in speakers if sp}
    found = []
    for k, seg, s in _walk(segments):
        if s.endswith("?") and not re.search(r"\b(can|could|would) you\b", s, re.I):
            continue
        text, kind, owner = ACTION_LEAD.sub("", s), None, ""
        explicit = bool(ACTION_LEAD.match(s))
        for pat, how in ACTION_PATTERNS:
            m = pat.search(text)
            if not m:
                continue
            if how in ("named", "addressed"):
                first = m.group(1).split()[0].lower()
                if first in TEAM_WORDS:
                    kind, owner = "team", "Team"
                    break
                if first not in firsts:
                    continue  # not a known person, e.g. "Dashboard will ..."
                owner = firsts[first]
            elif how == "self":
                owner = seg["speaker"]
            elif how == "ask":
                nxt = segments[k + 1] if k + 1 < len(segments) else None
                if nxt and nxt["speaker"] and nxt["speaker"] != seg["speaker"] and AFFIRM.match(nxt["text"]):
                    owner = nxt["speaker"]
            elif how == "team":
                owner = "Team"
            kind = how
            break
        if not kind and not explicit:
            continue
        if explicit and not owner:
            m = re.match(r"^([A-Z][a-z]+)\s*[:\-–]\s*(.+)$", text) or re.match(r"^([A-Z][a-z]+)\s+(?:to|will)\s+(.+)$", text)
            owner = firsts.get(m.group(1).lower(), "") if m else ""
        text = AFFIRM_LEAD.sub("", text)
        text = text[:1].upper() + text[1:]
        if len(text.split()) < 4:
            continue
        due, due_text = find_date(text, meeting_date)
        if due == meeting_date and WEEKDAY_ONLY.match(due_text):
            due = due + timedelta(days=7)  # "by Wednesday" said on a Wednesday means next week
        found.append({"text": text if len(text) <= 220 else text[:217] + "…", "owner": owner or "Unassigned", "due": due, "due_text": due_text,
                      "speaker": seg["speaker"], "ts": seg["start"], "idx": seg["idx"], "_how": kind})
    return _dedupe_actions(found)


def _dedupe_actions(found: list[dict]) -> list[dict]:
    by_idx = {a["idx"] for a in found}
    kept = []
    for a in found:
        if a["_how"] in ("ask", "addressed") and (a["idx"] + 1) in by_idx:
            continue  # the reply in the next turn captures the real commitment
        dup = next((b for b in kept if len(_content(a["text"]) & _content(b["text"])) / max(1, min(len(_content(a["text"])), len(_content(b["text"])))) >= 0.7), None)
        if dup:
            if a["due"] and not dup["due"]:
                dup.update(due=a["due"], due_text=a["due_text"])
            if dup["owner"] in ("Unassigned", "Team") and a["owner"] not in ("Unassigned", "Team"):
                dup["owner"] = a["owner"]
            continue
        kept.append(a)
    for a in kept:
        a.pop("_how", None)
    return kept


def find_questions(segments, limit: int = 12) -> list[dict]:
    out = [{"text": s, "speaker": seg["speaker"], "ts": seg["start"], "idx": seg["idx"]} for k, seg, s in _walk(segments)
           if s.endswith("?") and len(s.split()) >= 5 and not re.search(r"\b(can|could|would) you\b", s, re.I)]
    return out[:limit]


def find_open_items(segments) -> list[dict]:
    return [{"text": s, "speaker": seg["speaker"], "ts": seg["start"], "idx": seg["idx"]} for k, seg, s in _walk(segments) if OPEN_ITEM.search(s) and len(s.split()) >= 4][:10]


def speaker_stats(segments) -> list[dict]:
    words, turns, secs = Counter(), Counter(), Counter()
    has_time = False
    for seg in segments:
        sp = seg["speaker"]
        if not sp:
            continue
        words[sp] += len(seg["text"].split())
        turns[sp] += 1
        if seg.get("start") is not None and seg.get("end") is not None:
            secs[sp] += max(0, seg["end"] - seg["start"])
            has_time = True
    total = sum(words.values()) or 1
    return [{"name": n, "words": w, "turns": turns[n], "share": round(100 * w / total), "seconds": secs[n] if has_time else None}
            for n, w in words.most_common()]


def keywords(segments, speakers, top: int = 10) -> list[str]:
    names = {w.lower() for sp in speakers for w in sp.split()}
    words = [w for seg in segments for w in re.findall(r"[a-z][a-z-]{3,}(?![a-z'])", seg["text"].lower()) if w not in STOP and w not in names]
    stems: dict[str, Counter] = {}
    for w in words:
        stems.setdefault(re.sub(r"(ing|ed|es|s)$", "", w), Counter())[w] += 1
    ranked = sorted(stems.items(), key=lambda kv: -sum(kv[1].values()))
    return [c.most_common(1)[0][0] for _, c in ranked if sum(c.values()) >= 2][:top]


def topic_flow(segments, speakers, parts: int | None = None) -> list[dict]:
    if not segments:
        return []
    parts = parts or max(1, min(6, len(segments) // 8))
    total = sum(len(s["text"].split()) for s in segments)
    target, chunks, cur, acc = total / parts, [], [], 0
    for s in segments:
        cur.append(s)
        acc += len(s["text"].split())
        if acc >= target and len(chunks) < parts - 1:
            chunks.append(cur)
            cur, acc = [], 0
    if cur:
        chunks.append(cur)
    out = []
    for c in chunks:
        kws = keywords(c, speakers, 3) or keywords(c, speakers, 3)
        out.append({"label": ", ".join(kws) if kws else "discussion", "start": c[0]["start"], "first_idx": c[0]["idx"], "segments": len(c)})
    return out


def summarize(segments, decisions, actions, n: int = 4) -> list[str]:
    sents = [(k, seg, s) for k, seg, s in _walk(segments) if len(s.split()) >= 6 and not SMALLTALK.match(s) and not s.endswith("?")]
    if not sents:
        return []
    freq = Counter(w for _, _, s in sents for w in re.findall(r"[a-z][a-z'-]{3,}", s.lower()) if w not in STOP)
    top = max(freq.values()) if freq else 1
    special = {d["text"] for d in decisions} | {a["text"] for a in actions}
    scored = []
    for pos, (k, seg, s) in enumerate(sents):
        ws = [w for w in re.findall(r"[a-z][a-z'-]{3,}", s.lower()) if w not in STOP]
        score = sum(freq[w] / top for w in ws) / (max(1, len(ws)) ** 0.6)
        if s in special:
            score += 0.5
        scored.append((score, pos, s))
    best = sorted(sorted(scored, reverse=True)[:n], key=lambda x: x[1])
    return [s if len(s.split()) <= 40 else " ".join(s.split()[:40]) + "…" for _, _, s in best]


def health(segments, decisions, actions, stats) -> dict:
    assigned = [a for a in actions if a["owner"] not in ("Unassigned", "", "Team")]
    dated = [a for a in actions if a["due"]]
    top = stats[0]["share"] if stats else 0
    dur = None
    ts = [s["end"] or s["start"] for s in segments if (s["end"] or s["start"]) is not None]
    if ts:
        dur = max(ts) - min([s["start"] for s in segments if s["start"] is not None] or [0])
    checks = [
        (25, bool(decisions), "At least one decision recorded", "No clear decisions captured. End meetings by stating what was decided."),
        (25, bool(actions) and len(assigned) / len(actions) >= 0.7, "Action items have owners", "Most action items have no owner. Assign a name to every task."),
        (15, bool(actions) and len(dated) / len(actions) >= 0.5, "Action items have due dates", "Add due dates to action items."),
        (20, len(stats) >= 2 and top <= 60, "Participation is balanced", "One person did most of the talking. Invite others to speak."),
        (15, dur is None or dur <= 90 * 60, "Reasonable length", "The meeting ran over 90 minutes. Consider splitting it."),
    ]
    return {"score": sum(w for w, ok, *_ in checks if ok), "duration": dur,
            "checks": [{"ok": ok, "label": good if ok else bad} for _, ok, good, bad in checks]}


def analyze_meeting(segments: list[dict], meeting_date: date, participants=()) -> dict:
    stats = speaker_stats(segments)
    speakers = sorted({*(s["name"] for s in stats), *[p for p in participants if p]})
    decisions = find_decisions(segments)
    actions = find_actions(segments, meeting_date, speakers)
    h = health(segments, decisions, actions, stats)
    return {"summary": summarize(segments, decisions, actions), "decisions": decisions, "actions": actions, "questions": find_questions(segments),
            "open_items": find_open_items(segments), "topics": topic_flow(segments, speakers), "speakers": stats, "keywords": keywords(segments, speakers),
            "health": h, "duration": h["duration"], "participants": speakers, "words": sum(s["words"] for s in stats)}
