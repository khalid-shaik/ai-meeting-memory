"""Parse meeting transcripts (VTT, SRT, timestamped text, 'Name: text', plain notes) into segments."""
import re
from io import BytesIO

_TS = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,]\d{1,3})?"
_TS_RE = re.compile(_TS)
_CUE = re.compile(rf"^\s*({_TS})\s*-->\s*({_TS})")
NOT_SPEAKERS = {"action", "action item", "actions", "decision", "decisions", "note", "notes", "agenda", "summary", "topic", "todo", "to-do",
                "ai", "next steps", "attendees", "date", "time", "location", "re", "fyi", "update", "reminder", "question", "questions", "risk", "blocker"}
_LABEL = re.compile(r"^([A-Z][\w'’.-]*(?: [A-Z][\w'’.-]*){0,3}|[A-Z][\w'’.-]*(?: [\w'’.-]+){0,3}\s?\([^)]{1,25}\))\s*:\s*(.*)$")


def to_sec(h, m, s) -> int:
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def fmt_ts(sec) -> str:
    if sec is None:
        return ""
    sec = int(sec)
    return f"{sec // 3600:d}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:d}:{sec % 60:02d}"


def read_file(name: str, data: bytes) -> str:
    n = name.lower()
    if n.endswith(".docx"):
        from docx import Document
        doc = Document(BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for t in doc.tables:
            for row in t.rows:
                parts.append(" ".join(c.text for c in row.cells))
        return "\n".join(parts)
    if n.endswith((".txt", ".md", ".vtt", ".srt", ".text")):
        for enc in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeError:
                continue
    raise ValueError("Unsupported file type. Use .txt, .vtt, .srt, .md or .docx")


def _clean(t: str) -> str:
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _split_speaker(line: str):
    m = re.match(r"^<v\s+([^>]+)>(.*)$", line.strip())
    if m:
        return m[1].strip(), _clean(m[2])
    m = _LABEL.match(line.strip())
    if m and m[1].lower().split(" (")[0] not in NOT_SPEAKERS and len(m[1]) <= 40:
        return re.sub(r"\s*\([^)]*\)$", "", m[1]).strip(), m[2].strip()
    return None, line.strip()


def _cues(lines):
    """VTT/SRT: yield (start, end, text lines)."""
    i, n = 0, len(lines)
    while i < n:
        m = _CUE.match(lines[i])
        if m:
            g = _TS_RE.findall(lines[i])
            start, end = to_sec(*g[0]), to_sec(*g[1])
            i += 1
            text = []
            while i < n and lines[i].strip():
                text.append(lines[i])
                i += 1
            yield start, end, text
        else:
            i += 1


def _parse_cues(text: str) -> list[dict]:
    segs, last = [], "Unknown"
    for start, end, tl in _cues(text.replace("\r\n", "\n").split("\n")):
        raw = " ".join(tl)
        speaker, body = _split_speaker(tl[0]) if tl else (None, "")
        if speaker:
            body = _clean(" ".join([body] + tl[1:]))
            last = speaker
        else:
            body = _clean(raw)
        if not body:
            continue
        if segs and segs[-1]["speaker"] == last and start - (segs[-1]["end"] or start) <= 5:
            segs[-1]["text"] += " " + body
            segs[-1]["end"] = end
        else:
            segs.append({"start": start, "end": end, "speaker": last, "text": body})
    return segs


_TS_PATTERNS = [
    re.compile(rf"^[\[(]\s*({_TS})\s*[\])]\s*(.*)$"),            # [00:12:34] Alice: text
    re.compile(rf"^({_TS})\s+(?=\S)(.*)$"),                      # 00:12:34 Alice: text
    re.compile(rf"^(.{{1,40}}?)\s*[\[(]\s*({_TS})\s*[\])]\s*:?\s*(.*)$"),  # Alice (00:12:34): text
]


def _line_ts(line: str):
    m = _TS_PATTERNS[0].match(line) or _TS_PATTERNS[1].match(line)
    if m:
        g = _TS_RE.match(m[1])
        return to_sec(*g.groups()), m.group(m.lastindex)
    m = _TS_PATTERNS[2].match(line)
    if m and not re.search(r"\d", m[1]):
        g = _TS_RE.search(m[2])
        return to_sec(*g.groups()), f"{m[1]}: {m.group(m.lastindex)}"
    return None, line


def _parse_lines(text: str) -> list[dict]:
    segs, labelled = [], 0
    lines = [l for l in text.replace("\r\n", "\n").split("\n")]
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        ts, rest = _line_ts(line)
        speaker, body = _split_speaker(rest)
        if speaker:
            labelled += 1
            segs.append({"start": ts, "end": None, "speaker": speaker, "text": body, "new": True})
        elif ts is not None:
            segs.append({"start": ts, "end": None, "speaker": segs[-1]["speaker"] if segs else "", "text": body, "new": True})
        elif segs and segs[-1].get("speaker") and not re.match(r"^(action( item)?s?|decisions?|notes?|todo|next steps|agenda)\s*[:\-]", line, re.I) and not segs[-1]["text"].endswith(":") and labelled:
            segs[-1]["text"] += " " + line  # continuation of the previous speaker's turn
        else:
            segs.append({"start": None, "end": None, "speaker": "", "text": line, "new": True})
    return [{k: v for k, v in s.items() if k != "new"} for s in segs if s["text"].strip()]


def parse_transcript(text: str) -> list[dict]:
    text = (text or "").lstrip("\ufeff")
    segs = _parse_cues(text) if "-->" in text else _parse_lines(text)
    out = []
    for i, s in enumerate(segs):
        out.append({"idx": i, "start": s["start"], "end": s.get("end"), "speaker": s["speaker"].strip(), "text": _clean(s["text"])})
    return [s for s in out if s["text"]]
