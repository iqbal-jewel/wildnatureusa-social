"""Prove that API-scheduled Facebook posts actually fire on this Page.

Meta Business Suite does not list posts created through the Graph API -- their
`admin_creator` is the app, not a person -- so the Content -> Scheduled tab
reads "No scheduled posts" even when the queue is full. That is a display gap,
but it means you cannot confirm from the UI that a 6-month plan will publish.

This settles it end to end: it schedules one real photo post a few minutes
out through the identical code path the pipeline uses, waits for Meta to fire
it, confirms publication, and deletes it again.

It borrows the LAST fact post in the plan -- on-brand, accurate and attributed
-- rather than posting visible test junk, so the worst case if the delete fails
is a good post published early. It never writes state.json, so the borrowed
post still publishes normally on its own date.

    python tools/test_fire.py            # dry run: shows the plan, sends nothing
    python tools/test_fire.py --live     # schedule, wait, verify, delete
    python tools/test_fire.py --live --keep   # leave the post up for inspection

Run this once per Page before trusting a schedule to it.
"""
import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import meta, plan, publish  # noqa: E402

PLAN = ROOT / "plan" / "content_plan_fixed.xlsx"
LEAD_MINUTES = 12          # Meta rejects anything under 10
POLL_SECONDS = 20
GIVE_UP_AFTER = 15 * 60    # generous: Meta's scheduler is not to-the-second


def log(msg):
    print(f"[{dt.datetime.now(dt.timezone.utc):%H:%M:%S}Z] {msg}", flush=True)


def get(url, token, fields):
    r = requests.get(url, params={"fields": fields, "access_token": token},
                     timeout=30)
    try:
        return r.json()
    except ValueError:
        return {"_raw": r.text[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="actually schedule and publish; without it nothing is sent")
    ap.add_argument("--keep", action="store_true",
                    help="do not delete the post after confirming it fired")
    args = ap.parse_args()

    creds = meta.credentials()
    page_id, token = creds["page_id"], creds["token"]

    page = get(f"{meta.GRAPH}/{page_id}", token, "name,username")
    posts = plan.load(PLAN)
    post = [p for p in posts if p.platform == "Facebook" and not p.is_quiz][-1]

    fire_at = dt.datetime.now(plan.ET) + dt.timedelta(minutes=LEAD_MINUTES)

    log(f"page      : {page.get('name')} ({page.get('username')}) {page_id}")
    log(f"borrowing : {post.post_id} — {post.animal}")
    log(f"            normally publishes {post.publish_at:%Y-%m-%d %H:%M %Z}")
    log(f"fires at  : {fire_at:%H:%M:%S %Z} "
        f"({fire_at.astimezone(dt.timezone.utc):%H:%M:%S}Z)")

    if not args.live:
        log("DRY RUN — pass --live to actually run the test")
        return 0

    img = publish.image_for(post)
    caption = publish.caption_for(post)
    log(f"image     : {img.name} ({img.stat().st_size // 1024} KB)")

    rid = meta.fb_photo(page_id, token, img, caption, scheduled_at=fire_at)
    log(f"SCHEDULED : {rid}")

    # A scheduled photo returns a photo id; the post is page_id_photoid.
    full_id = rid if "_" in str(rid) else f"{page_id}_{rid}"

    before = get(f"{meta.GRAPH}/{full_id}", token,
                 "is_published,scheduled_publish_time")
    log(f"state now : is_published={before.get('is_published')} "
        f"scheduled_publish_time={before.get('scheduled_publish_time')}")

    deadline = time.time() + (fire_at - dt.datetime.now(plan.ET)).total_seconds() \
        + GIVE_UP_AFTER
    log(f"waiting for Meta to fire it (polling every {POLL_SECONDS}s)...")

    published = False
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        cur = get(f"{meta.GRAPH}/{full_id}", token,
                  "is_published,permalink_url,created_time")
        if cur.get("is_published") is True:
            published = True
            log("PUBLISHED — Meta fired the scheduled post")
            log(f"  permalink: {cur.get('permalink_url')}")
            log(f"  created  : {cur.get('created_time')}")
            break
        left = int(deadline - time.time())
        log(f"  not yet (is_published={cur.get('is_published')}), {left}s left")

    if not published:
        log("TIMED OUT — the post did not publish in the window.")
        log("Leaving it in place for inspection; do NOT trust the schedule yet.")
        log(f"Inspect: {meta.GRAPH}/{full_id}")
        return 1

    if args.keep:
        log(f"--keep set; leaving {full_id} up. Delete it yourself when done.")
        return 0

    d = requests.delete(f"{meta.GRAPH}/{full_id}",
                        params={"access_token": token}, timeout=60).json()
    if not d.get("success"):
        d = requests.delete(f"{meta.GRAPH}/{rid}",
                            params={"access_token": token}, timeout=60).json()
    log(f"delete    : {d}")

    gone = get(f"{meta.GRAPH}/{full_id}", token, "id")
    log(f"verify    : {'gone' if 'error' in gone or not gone.get('id') else 'STILL PRESENT — remove by hand'}")

    log("")
    log("RESULT: API-scheduled posts fire correctly on this Page.")
    log("Business Suite simply does not display them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
