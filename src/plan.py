"""Load the content calendar into normalised post records.

The workbook is the source of truth and is never written to by the publisher;
run-time status lives in state.json instead.
"""
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl

# Times in the workbook are US Eastern. The plan spans the Nov 2026 DST
# boundary, so this must be a real timezone, never a fixed offset.
ET = ZoneInfo("America/New_York")

C_ID, C_PLATFORM, C_TYPE, C_DATE = 0, 1, 2, 3
C_TIME, C_ANIMAL, C_CAPTION = 5, 6, 8
C_ANSWER, C_DELAY = 10, 11
C_BG, C_BG_NAME, C_FG, C_OVERLAY, C_DIMS, C_HASHTAGS = 12, 13, 14, 15, 16, 17


@dataclass(frozen=True)
class Post:
    post_id: str
    platform: str          # "Facebook" | "Instagram"
    kind: str              # "text" | "quiz"
    publish_at: dt.datetime  # timezone-aware, US Eastern
    animal: str
    caption: str           # caption + hashtags, ready to publish
    answer: str | None     # first comment for quiz posts
    delay_minutes: int
    bg_hex: str
    fg_hex: str
    overlay: str
    width: int
    height: int

    @property
    def is_quiz(self) -> bool:
        return self.kind == "quiz"

    @property
    def image_name(self) -> str:
        return f"{self.post_id}.png"


def _parse_when(date_str, time_str) -> dt.datetime:
    """Combine the workbook's date and '8:00 AM' style time into ET."""
    d = dt.date.fromisoformat(str(date_str).strip()[:10])
    t = dt.datetime.strptime(str(time_str).strip().upper(), "%I:%M %p").time()
    return dt.datetime.combine(d, t, tzinfo=ET)


def load(path: str | Path) -> list[Post]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    posts = []
    for r in wb["Content Calendar"].iter_rows(min_row=2, values_only=True):
        if not r[C_ID]:
            continue
        caption = (r[C_CAPTION] or "").strip()
        tags = (r[C_HASHTAGS] or "").strip()
        dims = str(r[C_DIMS] or "1080x1080").lower().split("x")
        is_quiz = r[C_TYPE] == "Image Post (Quiz)"
        posts.append(Post(
            post_id=r[C_ID],
            platform=r[C_PLATFORM],
            kind="quiz" if is_quiz else "text",
            publish_at=_parse_when(r[C_DATE], r[C_TIME]),
            animal=r[C_ANIMAL],
            caption=f"{caption}\n\n{tags}".strip() if tags else caption,
            answer=(r[C_ANSWER] or "").strip() or None if is_quiz else None,
            delay_minutes=int(r[C_DELAY] or 60),
            bg_hex=(r[C_BG] or "#2E7D32").strip(),
            fg_hex=(r[C_FG] or "#FFFFFF").strip(),
            overlay=(r[C_OVERLAY] or "").strip(),
            width=int(dims[0]),
            height=int(dims[1]),
        ))
    wb.close()
    posts.sort(key=lambda p: (p.publish_at, p.post_id))
    return posts


def due(posts, now=None, window_minutes=90):
    """Posts whose slot has arrived within the trailing window."""
    now = now or dt.datetime.now(ET)
    lo = now - dt.timedelta(minutes=window_minutes)
    return [p for p in posts if lo <= p.publish_at <= now]
