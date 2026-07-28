"""Publisher entrypoint.

Facebook posts are handed to Meta's native scheduler a week ahead, so they
fire even if this runner is down. Instagram has no scheduling API, so those
go out at their slot. Answer comments run off a due-queue.

Nothing publishes unless --live is passed; the default is a dry run.

    python -m src.publish status
    python -m src.publish schedule-fb --days 7          # dry run
    python -m src.publish schedule-fb --days 7 --live
    python -m src.publish run --live
"""
import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

from . import meta, plan, render
from .state import State

ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = ROOT / "plan" / "content_plan_fixed.xlsx"
IMAGES = ROOT / "images"

# Meta rejects a scheduled time less than 10 minutes out.
MIN_LEAD = dt.timedelta(minutes=20)

# Overridden by --now for dry-run testing.
_NOW = None


def now_et():
    return _NOW or dt.datetime.now(plan.ET)


def image_url(post) -> str:
    base = os.environ.get("IMAGE_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError(
            "IMAGE_BASE_URL is not set. Instagram needs a public URL for the "
            "image, e.g. https://raw.githubusercontent.com/<owner>/<repo>/main/images"
        )
    return f"{base}/{post.image_name}"


def log(msg):
    print(f"[{dt.datetime.now(plan.ET):%Y-%m-%d %H:%M %Z}] {msg}", flush=True)


# --- commands -------------------------------------------------------------
def cmd_schedule_fb(args, posts, st):
    now = now_et()
    horizon = now + dt.timedelta(days=args.days)
    todo = [p for p in posts
            if p.platform == "Facebook"
            and now + MIN_LEAD <= p.publish_at <= horizon
            and not st.is_done(p.post_id)]

    log(f"{len(todo)} Facebook posts to schedule through {horizon:%Y-%m-%d}")
    if not todo:
        return 0

    creds = meta.credentials() if args.live else None
    failures = 0
    for p in todo:
        if not args.live:
            log(f"  DRY  {p.post_id} {p.publish_at:%m-%d %H:%M} {p.kind:4} "
                f"{p.caption.splitlines()[0][:60]}")
            continue
        try:
            if p.is_quiz:
                img = IMAGES / p.image_name
                if not img.exists():
                    raise FileNotFoundError(f"{img} not rendered")
                rid = meta.fb_photo(creds["page_id"], creds["token"], img,
                                    p.caption, scheduled_at=p.publish_at)
            else:
                rid = meta.fb_text(creds["page_id"], creds["token"],
                                   p.caption, scheduled_at=p.publish_at)
            st.record_post(p.post_id, "Facebook", rid, status="scheduled",
                           publish_at=p.publish_at.isoformat())
            if p.is_quiz and p.answer:
                st.queue_comment(p.post_id,
                                 p.publish_at + dt.timedelta(minutes=p.delay_minutes),
                                 p.answer, "Facebook")
            log(f"  OK   {p.post_id} scheduled -> {rid}")
        except Exception as e:  # keep going; one bad row shouldn't stall the batch
            failures += 1
            st.record_failure(p.post_id, e)
            log(f"  FAIL {p.post_id}: {e}")
        st.save()
    return 1 if failures else 0


def cmd_run(args, posts, st):
    now = now_et()
    todo = [p for p in plan.due(posts, now, args.window)
            if p.platform == "Instagram" and not st.is_done(p.post_id)]

    log(f"{len(todo)} Instagram posts due")
    creds = meta.credentials() if args.live else None
    failures = 0

    for p in todo:
        if not args.live:
            log(f"  DRY  {p.post_id} {p.publish_at:%m-%d %H:%M} {p.animal}")
            continue
        try:
            url = image_url(p)
            rid = meta.ig_image(creds["ig_user_id"], creds["token"], url, p.caption)
            st.record_post(p.post_id, "Instagram", rid)
            if p.answer:
                st.queue_comment(p.post_id,
                                 p.publish_at + dt.timedelta(minutes=p.delay_minutes),
                                 p.answer, "Instagram")
            log(f"  OK   {p.post_id} posted -> {rid}")
        except Exception as e:
            failures += 1
            st.record_failure(p.post_id, e)
            log(f"  FAIL {p.post_id}: {e}")
        st.save()

    failures += _run_comments(args, st, creds)
    st.save()
    return 1 if failures else 0


def _run_comments(args, st, creds):
    due = st.due_comments()
    log(f"{len(due)} answer comments due")
    failures = 0
    for post_id, c in due:
        if not args.live:
            log(f"  DRY  comment on {post_id}: {c['message'][:60]}")
            continue
        try:
            target = st.remote_id(post_id)
            if not target:
                raise RuntimeError("no remote id recorded for the parent post")
            if c["platform"] == "Facebook":
                # A scheduled photo yields a photo id; comments need the story id.
                if "_" not in str(target):
                    story = meta.fb_story_id(target, creds["token"])
                    if not story:
                        raise RuntimeError("parent post is not live yet")
                    target = story
                rid = meta.fb_comment(target, creds["token"], c["message"])
            else:
                rid = meta.ig_comment(target, creds["token"], c["message"])
            st.mark_comment(post_id, "posted", rid)
            log(f"  OK   comment on {post_id} -> {rid}")
        except Exception as e:
            failures += 1
            st.mark_comment(post_id, "pending", error=e)
            log(f"  FAIL comment on {post_id}: {e}")
    return failures


def cmd_status(args, posts, st):
    now = now_et()
    counts, pending = st.summary()
    upcoming = [p for p in posts if p.publish_at > now]
    quizzes = [p for p in posts if p.is_quiz]
    missing = [p for p in quizzes if not (IMAGES / p.image_name).exists()]

    log(f"plan: {len(posts)} posts, {posts[0].publish_at:%Y-%m-%d} "
        f"to {posts[-1].publish_at:%Y-%m-%d}")
    log(f"upcoming: {len(upcoming)}   recorded: {sum(counts.values())} {counts}")
    log(f"answer comments pending: {pending}")
    log(f"quiz images: {len(quizzes) - len(missing)}/{len(quizzes)} rendered")
    if missing:
        log(f"  missing: {', '.join(p.post_id for p in missing[:5])}"
            f"{' ...' if len(missing) > 5 else ''}")

    for key in ("WILDNATUREUSA_PAGE_ID", "WILDNATUREUSA_PAGE_TOKEN",
                "WILDNATUREUSA_IG_USER_ID", "IMAGE_BASE_URL"):
        log(f"env {key}: {'set' if os.environ.get(key) else 'MISSING'}")

    token = os.environ.get("WILDNATUREUSA_PAGE_TOKEN")
    if token:
        try:
            info = meta.debug_token(token)
            days = meta.token_expiry(token)
            log(f"token type: {info.get('type', '?')}  "
                f"expires in: {'never' if days is None else f'{days} days'}")
            if days is not None and days < 14:
                log("  WARNING: expires soon -- switch to a System User token")

            # Separate from token expiry: data access can lapse at 90 days even
            # on a token that never expires, which would stall a 184-day run.
            dae = info.get("data_access_expires_at")
            if dae:
                left = int((dae - time.time()) // 86400)
                log(f"data access expires in: {left} days")
                if left < 200:
                    log("  NOTE: shorter than the plan. Re-authorise before it "
                        "lapses, or use a System User token (no data-access clock).")
            else:
                log("data access: no expiry")

            granted, missing = meta.scope_report(token)
            log(f"scopes granted ({len(granted)}): {', '.join(granted) or 'none'}")
            if missing:
                log(f"  MISSING required scopes: {', '.join(missing)}")
                log("  Re-generate the token with those ticked; if one keeps "
                    "dropping, the IG account is not linked to the Page as a "
                    "Business/Creator account.")
            else:
                log("  all required scopes present")
        except Exception as e:
            log(f"token check failed: {e}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "schedule-fb", "run"])
    ap.add_argument("--live", action="store_true",
                    help="actually publish; without it nothing is sent")
    ap.add_argument("--days", type=int, default=7,
                    help="scheduling horizon for schedule-fb")
    ap.add_argument("--window", type=int, default=90,
                    help="minutes back from now still counted as due")
    ap.add_argument("--plan", default=str(PLAN_PATH))
    ap.add_argument("--now", help="simulate a moment in ET, e.g. 2026-08-01T11:05 "
                                  "(dry-run testing only)")
    args = ap.parse_args(argv)
    if args.now:
        if args.live:
            ap.error("--now is for dry-run testing; refusing to combine with --live")
        globals()["_NOW"] = dt.datetime.fromisoformat(args.now).replace(tzinfo=plan.ET)

    posts = plan.load(args.plan)
    st = State()
    if not args.live and args.command != "status":
        log("DRY RUN -- pass --live to publish")

    handler = {"status": cmd_status, "schedule-fb": cmd_schedule_fb, "run": cmd_run}
    return handler[args.command](args, posts, st)


if __name__ == "__main__":
    sys.exit(main())

