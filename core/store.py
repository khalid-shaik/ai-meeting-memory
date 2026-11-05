"""SQLite memory of meetings, transcripts and extracted items, with full-text search (FTS5 when available)."""
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, date TEXT NOT NULL, participants TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', health INTEGER, duration INTEGER, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS segments (id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id INTEGER NOT NULL, idx INTEGER NOT NULL, start INTEGER, speaker TEXT NOT NULL DEFAULT '', text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
  due TEXT, status TEXT NOT NULL DEFAULT 'open', done_at TEXT, ts INTEGER, seg_idx INTEGER);
CREATE INDEX IF NOT EXISTS idx_seg_meeting ON segments(meeting_id, idx);
CREATE INDEX IF NOT EXISTS idx_items_meeting ON items(meeting_id);
"""
STOP = set("""a an the and or but if to of in on at for with from by as is are was were be been it its this that these those i you he she we they me my your our their
what which who whom when where why how did do does done about decide decided decision decisions discuss discussed say said tell told meeting meetings last any anything
have has had will would can could should there here is any some""".split())
ITEM_EDITABLE = {"text", "owner", "due", "status", "done_at", "kind"}


def query_terms(q: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", (q or "").lower()) if t not in STOP and len(t) > 1]


class MeetingStore:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as c:
            c.executescript(SCHEMA)
            try:
                c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS seg_fts USING fts5(text, speaker, meeting_id UNINDEXED, idx UNINDEXED, tokenize='porter unicode61')")
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False
            c.commit()

    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    # ----- write -----
    def add_meeting(self, title, date, participants, source, summary, segments, items, health=None, duration=None) -> int:
        with closing(self._conn()) as c:
            mid = c.execute("INSERT INTO meetings (title,date,participants,source,summary,health,duration,created) VALUES (?,?,?,?,?,?,?,?)",
                            (title.strip() or "Untitled meeting", date, json.dumps(list(participants)), source, summary, health, duration,
                             datetime.now().isoformat(timespec="seconds"))).lastrowid
            for s in segments:
                c.execute("INSERT INTO segments (meeting_id,idx,start,speaker,text) VALUES (?,?,?,?,?)", (mid, s["idx"], s.get("start"), s["speaker"], s["text"]))
                if self.fts:
                    c.execute("INSERT INTO seg_fts (text,speaker,meeting_id,idx) VALUES (?,?,?,?)", (s["text"], s["speaker"], mid, s["idx"]))
            for it in items:
                c.execute("INSERT INTO items (meeting_id,kind,text,owner,due,status,ts,seg_idx) VALUES (?,?,?,?,?,?,?,?)",
                          (mid, it["kind"], it["text"], it.get("owner", ""), it.get("due"), it.get("status", "open"), it.get("ts"), it.get("idx")))
            c.commit()
            return mid

    def delete_meeting(self, mid: int):
        with closing(self._conn()) as c:
            for t in ("segments", "items"):
                c.execute(f"DELETE FROM {t} WHERE meeting_id=?", (mid,))
            if self.fts:
                c.execute("DELETE FROM seg_fts WHERE meeting_id=?", (mid,))
            c.execute("DELETE FROM meetings WHERE id=?", (mid,))
            c.commit()

    def update_item(self, item_id: int, **fields):
        fields = {k: v for k, v in fields.items() if k in ITEM_EDITABLE}
        if fields:
            with closing(self._conn()) as c:
                c.execute(f"UPDATE items SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), item_id))
                c.commit()

    def set_status(self, item_id: int, status: str):
        self.update_item(item_id, status=status, done_at=datetime.now().isoformat(timespec="seconds") if status == "done" else None)

    # ----- read -----
    def meetings(self) -> list[dict]:
        with closing(self._conn()) as c:
            rows = c.execute("""SELECT m.*, (SELECT COUNT(*) FROM items i WHERE i.meeting_id=m.id AND i.kind='action') AS actions,
                                (SELECT COUNT(*) FROM items i WHERE i.meeting_id=m.id AND i.kind='decision') AS decisions
                                FROM meetings m ORDER BY m.date DESC, m.id DESC""").fetchall()
        return [{**dict(r), "participants": json.loads(r["participants"])} for r in rows]

    def get(self, mid: int) -> dict | None:
        return next((m for m in self.meetings() if m["id"] == mid), None)

    def segments(self, mid: int) -> list[dict]:
        with closing(self._conn()) as c:
            return [dict(r) for r in c.execute("SELECT idx,start,speaker,text FROM segments WHERE meeting_id=? ORDER BY idx", (mid,))]

    def items(self, meeting_id=None, kind=None, status=None, owner=None) -> list[dict]:
        q, args = "SELECT i.*, m.title AS meeting_title, m.date AS meeting_date FROM items i JOIN meetings m ON m.id=i.meeting_id WHERE 1=1", []
        for col, val in (("i.meeting_id", meeting_id), ("i.kind", kind), ("i.status", status), ("i.owner", owner)):
            if val is not None:
                q, args = q + f" AND {col}=?", args + [val]
        with closing(self._conn()) as c:
            return [dict(r) for r in c.execute(q + " ORDER BY m.date DESC, i.id", args)]

    def search(self, query: str, limit: int = 15, meeting_id: int | None = None) -> list[dict]:
        terms = query_terms(query)
        if not terms:
            return []
        for joiner in (" AND ", " OR "):
            rows = self._fts(terms, joiner, limit, meeting_id) if self.fts else self._like(terms, joiner.strip(), limit, meeting_id)
            if rows:
                return rows
        return []

    def _fts(self, terms, joiner, limit, meeting_id):
        match = joiner.join(f'"{t}"*' for t in terms)
        sql = ("SELECT f.meeting_id, f.idx AS seg_idx, f.speaker, f.text, snippet(seg_fts, 0, '[', ']', '…', 14) AS snippet, m.title, m.date, s.start "
               "FROM seg_fts f JOIN meetings m ON m.id=f.meeting_id JOIN segments s ON s.meeting_id=f.meeting_id AND s.idx=f.idx "
               "WHERE seg_fts MATCH ?" + (" AND f.meeting_id=?" if meeting_id else "") + " ORDER BY bm25(seg_fts) LIMIT ?")
        args = [match] + ([meeting_id] if meeting_id else []) + [limit]
        try:
            with closing(self._conn()) as c:
                return [dict(r) for r in c.execute(sql, args)]
        except sqlite3.OperationalError:
            return []

    def _like(self, terms, op, limit, meeting_id):
        cond = f" {op} ".join("lower(s.text) LIKE ?" for _ in terms)
        sql = ("SELECT s.meeting_id, s.idx AS seg_idx, s.speaker, s.text, s.text AS snippet, m.title, m.date, s.start FROM segments s "
               "JOIN meetings m ON m.id=s.meeting_id WHERE (" + cond + ")" + (" AND s.meeting_id=?" if meeting_id else "") + " ORDER BY m.date DESC LIMIT ?")
        args = [f"%{t}%" for t in terms] + ([meeting_id] if meeting_id else []) + [limit]
        with closing(self._conn()) as c:
            return [dict(r) for r in c.execute(sql, args)]

    def search_items(self, query: str, kind: str | None = None, limit: int = 15) -> list[dict]:
        terms = query_terms(query)
        if not terms:
            return []
        cond = " OR ".join("lower(i.text) LIKE ?" for _ in terms)
        sql = ("SELECT i.*, m.title AS meeting_title, m.date AS meeting_date FROM items i JOIN meetings m ON m.id=i.meeting_id "
               "WHERE (" + cond + ")" + (" AND i.kind=?" if kind else "") + " ORDER BY m.date DESC LIMIT ?")
        with closing(self._conn()) as c:
            return [dict(r) for r in c.execute(sql, [f"%{t}%" for t in terms] + ([kind] if kind else []) + [limit])]

    def trend(self, term: str) -> list[dict]:
        """How many transcript segments per meeting mention the term."""
        terms = query_terms(term)
        out = []
        for m in reversed(self.meetings()):
            hits = len(self.search(" ".join(terms), limit=500, meeting_id=m["id"])) if terms else 0
            out.append({"meeting_id": m["id"], "date": m["date"], "title": m["title"], "hits": hits})
        return out
