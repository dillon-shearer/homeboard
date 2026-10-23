import calendar
from datetime import date, timedelta


def _dates(first: date, repeat: str, start: date, end: date):
    if repeat == "never":
        if start <= first <= end:
            yield first
    elif repeat == "weekly":
        d = first
        if d < start:
            d += timedelta(days=-(-(start - d).days // 7) * 7)
        while d <= end:
            yield d
            d += timedelta(days=7)
    elif repeat == "monthly":
        lo = max(first, start)
        y, m = lo.year, lo.month
        while (y, m) <= (end.year, end.month):
            if first.day <= calendar.monthrange(y, m)[1]:
                d = date(y, m, first.day)
                if lo <= d <= end:
                    yield d
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    elif repeat == "yearly":
        lo = max(first, start)
        for y in range(lo.year, end.year + 1):
            if first.month == 2 and first.day == 29 and not calendar.isleap(y):
                continue
            d = date(y, first.month, first.day)
            if lo <= d <= end:
                yield d


def expand(events, start: date, end: date) -> list[dict]:
    out = []
    for ev in events:
        first = date.fromisoformat(ev["date"])
        for d in _dates(first, ev["repeat"], start, end):
            out.append(
                {
                    "id": ev["id"],
                    "title": ev["title"],
                    "date": d.isoformat(),
                    "time": ev["time"],
                    "repeat": ev["repeat"],
                }
            )
    out.sort(key=lambda o: (o["date"], o["time"] is not None, o["time"] or "", o["id"]))
    return out
