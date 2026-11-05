"""Find deadlines in email text, relative to when the email was sent."""
import calendar
import re
from datetime import date, timedelta

WD = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MO = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_WD = r"(?:mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
_MO = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


def _d(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _year(day, month, base):
    d = _d(base.year, month, day)
    return _d(base.year + 1, month, day) if d and d < base - timedelta(days=30) else d


def find_date(text: str, base: date) -> tuple[date | None, str]:
    t = (text or "").lower()
    rules = [
        (r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: _d(int(m[1]), int(m[2]), int(m[3]))),
        (rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MO})(?:\s+(\d{{4}}))?\b", lambda m: _d(int(m[3]), MO[m[2][:3]], int(m[1])) if m[3] else _year(int(m[1]), MO[m[2][:3]], base)),
        (rf"\b({_MO})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", lambda m: _d(int(m[3]), MO[m[1][:3]], int(m[2])) if m[3] else _year(int(m[2]), MO[m[1][:3]], base)),
        (r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", lambda m: _d(int(m[3]), int(m[2]), int(m[1]))),
        (r"\bday after tomorrow\b", lambda m: base + timedelta(days=2)),
        (r"\b(?:tomorrow|tmrw)\b", lambda m: base + timedelta(days=1)),
        (r"\b(?:today|tonight|eod|cob|end of (?:the )?day|close of business)\b", lambda m: base),
        (r"\b(?:end of (?:the )?week|eow)\b", lambda m: base + timedelta(days=(4 - base.weekday()) % 7)),
        (r"\b(?:end of (?:the )?month|eom)\b", lambda m: _d(base.year, base.month, calendar.monthrange(base.year, base.month)[1])),
        (r"\bnext week\b", lambda m: base + timedelta(days=7 - base.weekday())),
        (r"\bin\s+(\d+)\s+(day|days|week|weeks)\b", lambda m: base + timedelta(days=int(m[1]) * (7 if m[2].startswith("week") else 1))),
        (rf"\b(next\s+)?({_WD})\b", lambda m: base + timedelta(days=((WD[m[2][:3]] - base.weekday()) % 7) + (7 if m[1] and (WD[m[2][:3]] - base.weekday()) % 7 == 0 else 0))),
    ]
    for pat, fn in rules:
        m = re.search(pat, t)
        if m:
            d = fn(m)
            if d:
                return d, m.group(0)
    return None, ""
