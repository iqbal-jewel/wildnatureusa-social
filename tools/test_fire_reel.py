"""Prove that an API-scheduled Facebook Reel actually fires on this Page.

Same reasoning as test_fire.py: correct API state (video_state=SCHEDULED) is
not proof of behaviour, and reels use a different Meta endpoint (resumable
upload + /video_reels) than the photo posts that path already proved. Nothing
before this has ever scheduled a reel against a live Page.

Schedules one real reel a few minutes out through the identical code path
production scheduling uses (video_for + meta.fb_reel), waits for Meta to fire
it, confirms publication, and deletes it.

Borrows the LAST FB-RL post in the plan -- a genuine, on-brand fact reel --
rather than posting visible test junk, so a failed delete leaves a good post
published early instead of debris. Never writes state.json, so the borrowed
post still publishes normally on its own date.

    python tools/test_fire_reel.py            # dry run
    python tools/test_fire_reel.py --live      # schedule, wait, verify, delete
    python tools/test_fire_reel.py --live --keep
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
LEAD_MINUTES = 12
POLL_SECONDS = 20
GIVE_UP_AFTER = 15 * 60


def log(msg):
    print(f"[{dt.datetime.now(dt.timezone.utc):%H:%M:%S}Z] {msg}", flush=True)


def get(url, token, fields):
    r = requests.get(url, params={"fields": fields, "access_token": token}, timeout=30)
    try:
        return r.json()
    except ValueError:
        return {"_raw": r.text[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    creds = meta.credentials()
    page_id, token = creds["page_id"], creds["token"]

    page = get(f"{meta.GRAPH}/{page_id}", token, "name,username")
    posts = plan.load(PLAN)
    post = [p for p in posts if p.post_id.startswith("FB-RL-")][-1]

    fire_at = dt.datetime.now(plan.ET) + dt.timedelta(minutes=LEAD_MINUTES)

    log(f"page      : {page.get('name')} ({page.get('username')}) {page_id}")
    log(f"borrowing : {post.post_id} -- {post.animal}")
    log(f"            normally publishes {post.publish_at:%Y-%m-%d %H:%M %Z}")
    log(f"fires at  : {fire_at:%H:%M:%S %Z} "
        f"({fire_at.astimezone(dt.timezone.utc):%H:%M:%S}Z)")

    if not args.live:
        log("DRY RUN -- pass --live to actually run the test")
        return 0

    log("rendering the reel (~60s)...")
    video = publish.video_for(post)
    log(f"video     : {video.name} ({video.stat().st_size // 1024} KB)")

    try:
        rid = meta.fb_reel(page_id, token, video, publish.caption_for(post),
                           scheduled_at=fire_at)
    finally:
        video.unlink()
    log(f"SCHEDULED : video_id={rid}")

    before = get(f"{meta.GRAPH}/{rid}", token, "status,published")
    log(f"state now : {before}")

    deadline = time.time() + (fire_at - dt.datetime.now(plan.ET)).total_seconds() \
        + GIVE_UP_AFTER
    log(f"waiting for Meta to fire it (polling every {POLL_SECONDS}s)...")

    published = False
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        cur = get(f"{meta.GRAPH}/{rid}", token,
                  "published,permalink_url,created_time,status")
        if cur.get("published") is True:
            published = True
            log("PUBLISHED -- Meta fired the scheduled reel")
            log(f"  permalink: {cur.get('permalink_url')}")
            log(f"  created  : {cur.get('created_time')}")
            break
        left = int(deadline - time.time())
        log(f"  not yet (published={cur.get('published')}, "
            f"status={cur.get('status')}), {left}s left")

    if not published:
        log("TIMED OUT -- the reel did not publish in the window.")
        log(f"Leaving it in place for inspection: {meta.GRAPH}/{rid}")
        return 1

    if args.keep:
        log(f"--keep set; leaving {rid} up. Delete it yourself when done.")
        return 0

    d = requests.delete(f"{meta.GRAPH}/{rid}", params={"access_token": token},
                        timeout=60).json()
    log(f"delete    : {d}")
    gone = get(f"{meta.GRAPH}/{rid}", token, "id")
    log(f"verify    : {'gone' if 'error' in gone or not gone.get('id') else 'STILL PRESENT -- remove by hand'}")

    log("")
    log("RESULT: an API-scheduled reel fires correctly on this Page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
