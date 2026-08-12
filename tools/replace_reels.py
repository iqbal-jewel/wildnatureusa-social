"""Re-render and replace already-scheduled Facebook reels in place.

Used once, urgently, after fixing a crop bug (see the "Fix reels cropping
the animal out of frame" commit) that affected every reel rendered before
it: FB-RL-0013 through FB-RL-0018 were scheduled with the buggy renderer and
are still unpublished, one firing within 24 hours. Rather than inspect each
source photo individually for whether it happens to trigger the bug, this
replaces all of them unconditionally -- the new renderer is a strict
improvement (guarantees the full subject stays in frame) for every case
checked, so there is no version of "this one was already fine" that the new
render makes worse.

Facebook has no way to swap the video on an existing scheduled Reel, so this
creates the replacement first and only deletes the original once the create
succeeds -- a failure leaves a removable duplicate rather than an unfillable
slot, same pattern as replace_text_posts.py.

Does NOT touch FB-RL-0012 (Common Raven): it already published before the
fix landed, so this is a repost-or-leave-it decision, not a re-schedule.

    python tools/replace_reels.py           # dry run
    python tools/replace_reels.py --live
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import meta, plan, publish  # noqa: E402
from src.state import State  # noqa: E402

GRAPH = meta.GRAPH
PLAN_PATH = ROOT / "plan" / "content_plan_fixed.xlsx"

TARGETS = ["FB-RL-0013", "FB-RL-0014", "FB-RL-0015",
          "FB-RL-0016", "FB-RL-0017", "FB-RL-0018"]


def fetch(video_id, token):
    r = requests.get(f"{GRAPH}/{video_id}", params={
        "fields": "id,published,status",
        "access_token": token,
    }, timeout=30)
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    creds = meta.credentials()
    token, page_id = creds["token"], creds["page_id"]

    st = State()
    posts = {p.post_id: p for p in plan.load(PLAN_PATH)}

    print(f"{len(TARGETS)} reels targeted for replacement")
    if not args.live:
        print("DRY RUN -- pass --live to replace\n")

    replaced = skipped = failed = 0
    for pid in TARGETS:
        post = posts.get(pid)
        rec = st.data["posts"].get(pid, {})
        old_id = rec.get("remote_id")

        if post is None or old_id is None or rec.get("status") != "scheduled":
            print(f"  SKIP {pid}: not a currently-scheduled post in state")
            skipped += 1
            continue

        remote = fetch(old_id, token)
        if remote.get("published") is True:
            print(f"  SKIP {pid}: already published, not touching it")
            skipped += 1
            continue
        if "error" in remote:
            print(f"  SKIP {pid}: cannot read {old_id} ({remote['error'].get('message')})")
            skipped += 1
            continue

        if not args.live:
            print(f"  DRY  {pid} {post.publish_at:%m-%d %H:%M} {post.animal}"
                 f" -> new reel, then delete {old_id}")
            replaced += 1
            continue

        video_path = None
        try:
            print(f"  rendering {pid} ({post.animal})...")
            video_path = publish.video_for(post)
            new_id = meta.fb_reel(page_id, token, video_path,
                                  publish.caption_for(post),
                                  scheduled_at=post.publish_at)

            d = requests.delete(f"{GRAPH}/{old_id}",
                                params={"access_token": token}, timeout=60).json()
            if not d.get("success"):
                print(f"  WARN {pid}: created {new_id} but delete of {old_id} "
                     f"failed ({d}); remove it by hand")

            st.record_post(pid, "Facebook", new_id, status="scheduled",
                           publish_at=post.publish_at.isoformat())
            st.save()
            replaced += 1
            print(f"  OK   {pid} {post.publish_at:%m-%d %H:%M} {post.animal}"
                 f" -> {new_id}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {pid}: {type(e).__name__}: {e}")
        finally:
            if video_path and video_path.exists():
                video_path.unlink()

    print(f"\nreplaced {replaced}, skipped {skipped}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
