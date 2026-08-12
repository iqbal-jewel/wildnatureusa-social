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
    kind: str              # "text" | "quiz" | "reel"
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
    def is_reel(self) -> bool:
        return self.kind == "reel"

    @property
    def image_name(self) -> str:
        # Quiz cards are flat colour and stay lossless; fact cards are
        # photographs, where PNG costs ~2 MB against ~200 KB for JPEG. Reels
        # render straight to MP4 via render_fact_reel and never touch this --
        # kept only so nothing breaks if it's ever inspected for a reel row.
        return f"{self.post_id}.png" if self.is_quiz else f"{self.post_id}.jpg"


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
        is_reel = r[C_TYPE] == "Reel Post (Fact)"
        posts.append(Post(
            post_id=r[C_ID],
            platform=r[C_PLATFORM],
            kind="quiz" if is_quiz else ("reel" if is_reel else "text"),
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


def due(posts, now=None, max_late_minutes=360):
    """Posts whose slot has passed and which are still worth publishing.

    This is a lateness tolerance, not a narrow window around `now`. GitHub's
    scheduled runners are throttled and routinely skip hours at a time -- gaps
    of 91 to 254 minutes were observed on this repo against an hourly cron --
    so a tight window silently drops any post whose slot fell inside a gap,
    with no error and no retry. The tolerance has to comfortably exceed the
    worst expected gap.

    It is still bounded: publishing yesterday's post today is worse than not
    publishing it. Anything past the bound is reported by `overdue` so it can
    be recorded as a deliberate skip rather than vanishing.
    """
    now = now or dt.datetime.now(ET)
    lo = now - dt.timedelta(minutes=max_late_minutes)
    return [p for p in posts if lo <= p.publish_at <= now]


def overdue(posts, now=None, max_late_minutes=360):
    """Posts whose slot passed too long ago to publish now."""
    now = now or dt.datetime.now(ET)
    lo = now - dt.timedelta(minutes=max_late_minutes)
    return [p for p in posts if p.publish_at < lo]
