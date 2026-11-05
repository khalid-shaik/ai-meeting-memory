"""Optional local AI (Ollama): structured minutes from long transcripts (chunked), memory Q&A and follow-up emails."""
import json
import re

import ollama

CHUNK_CHARS = 5000
MAX_CHUNKS = 8
SYSTEM = ("You are a meticulous meeting secretary. Use ONLY what is in the transcript; never invent decisions, owners, dates or numbers. "
          "Reply with a single valid JSON object only.")


def parse_json(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise ValueError("Model did not return JSON.")
        return json.loads(m.group(0))


def list_models(host: str | None = None) -> list[str]:
    try:
        client = ollama.Client(host=host) if host else ollama.Client()
        resp = client.list()
        models = resp["models"] if isinstance(resp, dict) else resp.models
        return sorted((m["model"] if isinstance(m, dict) else m.model) for m in models)
    except Exception:
        return []


def _chat(prompt, model, host, temperature) -> dict:
    client = ollama.Client(host=host) if host else ollama.Client()
    resp = client.chat(model=model, messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                       format="json", options={"temperature": temperature})
    content = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
    return parse_json(content)


def _list(v):
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def _actions(v):
    out = []
    for a in v if isinstance(v, list) else []:
        if isinstance(a, dict) and str(a.get("task", "")).strip():
            due = str(a.get("due", "")).strip()
            out.append({"task": str(a["task"]).strip(), "owner": str(a.get("owner", "")).strip() or "Unassigned",
                        "due": due if re.match(r"^\d{4}-\d{2}-\d{2}$", due) else ""})
    return out


def normalize_chunk(d: dict) -> dict:
    return {"summary": str(d.get("summary", "")).strip(), "topics": _list(d.get("topics")), "decisions": _list(d.get("decisions")),
            "action_items": _actions(d.get("action_items")), "open_questions": _list(d.get("open_questions")), "risks": _list(d.get("risks"))}


def _dedupe(items, key=lambda x: x):
    seen, out = set(), []
    for x in items:
        k = re.sub(r"\W+", " ", str(key(x)).lower()).strip()[:80]
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def merge_chunks(chunks: list[dict], overall: str = "") -> dict:
    return {"summary": overall or " ".join(c["summary"] for c in chunks if c["summary"]),
            "topics": _dedupe([t for c in chunks for t in c["topics"]]), "decisions": _dedupe([t for c in chunks for t in c["decisions"]]),
            "action_items": _dedupe([a for c in chunks for a in c["action_items"]], key=lambda a: a["task"]),
            "open_questions": _dedupe([t for c in chunks for t in c["open_questions"]]), "risks": _dedupe([t for c in chunks for t in c["risks"]])}


def chunk_transcript(segments: list[dict], max_chars: int = CHUNK_CHARS) -> list[str]:
    from .transcript import fmt_ts
    chunks, cur = [], ""
    for s in segments:
        ts = f"[{fmt_ts(s['start'])}] " if s.get("start") is not None else ""
        line = f"{ts}{s['speaker'] + ': ' if s['speaker'] else ''}{s['text']}\n"
        while len(line) > max_chars:  # one enormous turn
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        if len(cur) + len(line) > max_chars and cur:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    return chunks


def generate_minutes(segments, title, meeting_date, model="llama3.2", host=None, temperature=0.2) -> tuple[dict, bool]:
    """Returns (minutes, truncated). Long transcripts are processed in chunks then merged."""
    chunks = chunk_transcript(segments)
    truncated = len(chunks) > MAX_CHUNKS
    parts = []
    for ch in chunks[:MAX_CHUNKS]:
        prompt = (f"MEETING: {title} ({meeting_date})\nTRANSCRIPT PART:\n{ch}\n\n"
                  'Return JSON: {"summary": "2-3 sentences", "topics": ["..."], "decisions": ["clear statements of what was decided"], '
                  '"action_items": [{"task": "...", "owner": "person or Unassigned", "due": "YYYY-MM-DD or empty"}], "open_questions": ["..."], "risks": ["..."]}')
        parts.append(normalize_chunk(_chat(prompt, model, host, temperature)))
    overall = ""
    if len(parts) > 1:
        joined = "\n".join(f"- {p['summary']}" for p in parts if p["summary"])
        overall = str(_chat(f"Combine these partial summaries of one meeting into a single 3-4 sentence summary.\n{joined}\nReturn JSON: "
                            '{"summary": "..."}', model, host, temperature).get("summary", "")).strip()
    elif parts:
        overall = parts[0]["summary"]
    return merge_chunks(parts, overall), truncated


def normalize_answer(d: dict, n_sources: int) -> dict:
    cited = []
    for c in d.get("cited", []) if isinstance(d.get("cited"), list) else []:
        try:
            c = int(c)
        except (TypeError, ValueError):
            continue
        if 1 <= c <= n_sources and c not in cited:
            cited.append(c)
    return {"answer": str(d.get("answer", "")).strip(), "cited": cited}


def answer_question(question: str, sources: list[str], model="llama3.2", host=None, temperature=0.1) -> dict:
    listing = "\n".join(f"[{i}] {s}" for i, s in enumerate(sources, 1))
    prompt = (f"Answer the question using ONLY these meeting excerpts. If they do not contain the answer, say so. Max 4 sentences. "
              f"Cite excerpt numbers.\n\nEXCERPTS:\n{listing}\n\nQUESTION: {question}\n"
              'Return JSON: {"answer": "...", "cited": [1, 2]}')
    return normalize_answer(_chat(prompt, model, host, temperature), len(sources))


def draft_followup(facts: str, tone: str, fallback: dict, model="llama3.2", host=None, temperature=0.4) -> dict:
    prompt = (f"Write a short {tone.lower()} follow-up email to meeting attendees using ONLY these facts:\n{facts}\n\n"
              'Return JSON: {"subject": "...", "body": "..."}')
    d = _chat(prompt, model, host, temperature)
    return {"subject": str(d.get("subject", "")).strip() or fallback["subject"], "body": str(d.get("body", "")).strip() or fallback["body"]}
