"""Re-create already-scheduled Facebook text posts as scheduled photo posts.

Facebook has no way to attach media to an existing scheduled status update, so
converting one means creating a replacement and deleting the original.

Order matters. The new photo post is created *first*, then the old text post is
deleted. If that ordering is reversed and the create then fails, the slot is
gone and nothing publishes at that time. This way the worst case is a visible
duplicate that can be removed by hand before it fires.

Only posts that are FB-TXT-*, recorded as "scheduled" in state, and confirmed
by the API to be an unpublished status update with no attachments are touched.
Anything else is left alone and reported.

    python tools/replace_text_posts.py           # dry run
    python tools/replace_text_posts.py --live
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


def fetch_post(post_id, token):
    """Current server-side view of a scheduled post, or None if it is gone."""
    r = requests.get(f"{GRAPH}/{post_id}", params={
        "fields": "id,message,is_published,scheduled_publish_time,status_type,"
                  "attachments{media_type}",
        "access_token": token,
    }, timeout=60)
    data = r.json()
    if "error" in data:
        return {"_error": data["error"].get("message", "unknown")}
    return data


def is_plain_scheduled_text(remote):
    """True only for an unpublished status update carrying no media."""
    if remote.get("is_published", True):
        return False, "already published"
    if not remote.get("scheduled_publish_time"):
        return False, "not scheduled"
    if remote.get("attachments", {}).get("data"):
        return False, "already has an attachment"
    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="actually replace; without it nothing is changed")
    args = ap.parse_args()

    creds = meta.credentials()
    token = creds["token"]
    page_id = creds["page_id"]

    st = State()
    posts = {p.post_id: p for p in plan.load(PLAN_PATH)}

    targets = [
        pid for pid, rec in st.data["posts"].items()
        if pid.startswith("FB-TXT-")
        and rec.get("status") == "scheduled"
        and rec.get("platform") == "Facebook"
    ]
    targets.sort(key=lambda pid: posts[pid].publish_at if pid in posts else None)

    print(f"{len(targets)} scheduled text posts recorded in state")
    if not args.live:
        print("DRY RUN -- pass --live to replace\n")

    replaced = skipped = failed = 0
    for pid in targets:
        post = posts.get(pid)
        if post is None:
            print(f"  SKIP {pid}: not in the plan any more")
            skipped += 1
            continue

        old_id = st.remote_id(pid)
        remote = fetch_post(old_id, token)
        if "_error" in remote:
            print(f"  SKIP {pid}: cannot read {old_id} ({remote['_error']})")
            skipped += 1
            continue

        ok, why = is_plain_scheduled_text(remote)
        if not ok:
            print(f"  SKIP {pid}: {why}")
            skipped += 1
            continue

        when = dt.datetime.fromtimestamp(remote["scheduled_publish_time"],
                                         dt.timezone.utc)
        if not args.live:
            print(f"  DRY  {pid} {post.publish_at:%m-%d %H:%M} {post.animal}"
                  f" -> photo post ({post.image_name})")
            replaced += 1
            continue

        try:
            # 1. create the replacement, keeping the original slot exactly
            img = publish.image_for(post)
            new_id = meta.fb_photo(page_id, token, img,
                                   publish.caption_for(post),
                                   scheduled_at=post.publish_at)

            # 2. only once that succeeded, remove the text original
            d = requests.delete(f"{GRAPH}/{old_id}",
                                params={"access_token": token}, timeout=60).json()
            if not d.get("success"):
                print(f"  WARN {pid}: created {new_id} but delete of {old_id} "
                      f"failed ({d}); remove it by hand before "
                      f"{when:%m-%d %H:%M} UTC")

            st.record_post(pid, "Facebook", new_id, status="scheduled",
                           publish_at=post.publish_at.isoformat())
            st.save()
            replaced += 1
            print(f"  OK   {pid} {post.publish_at:%m-%d %H:%M} {post.animal}"
                  f" -> {new_id}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {pid}: {type(e).__name__}: {e}")

    print(f"\nreplaced {replaced}, skipped {skipped}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
