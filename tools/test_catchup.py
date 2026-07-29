"""Check that an Instagram slot survives a realistic Actions gap.

Reproduces the failure the 90-minute window had: the runner does not tick for
several hours, and the post whose slot fell inside the gap must still go out
on the next run instead of disappearing.

    python tools/test_catchup.py
"""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import plan  # noqa: E402

PLAN = ROOT / "plan" / "content_plan_fixed.xlsx"

# The gaps actually observed between scheduled runs on this repo.
OBSERVED_GAPS_MIN = [123, 91, 113, 254, 197]


def main():
    posts = plan.load(PLAN)
    ig = [p for p in posts if p.platform == "Instagram"]
    slot = ig[0]                      # 2026-08-01 11:00 ET
    print(f"slot under test: {slot.post_id} at {slot.publish_at:%Y-%m-%d %H:%M %Z}")

    failures = 0
    print(f"\n{'gap':>5}  {'old (90m)':>10}  {'new (360m)':>11}")
    for gap in OBSERVED_GAPS_MIN:
        # The runner wakes `gap` minutes after the slot passed.
        now = slot.publish_at + dt.timedelta(minutes=gap)
        old = slot in plan.due(ig, now, 90)
        new = slot in plan.due(ig, now, 360)
        print(f"{gap:>4}m  {'PUBLISH' if old else 'LOST':>10}  "
              f"{'PUBLISH' if new else 'LOST':>11}")
        if not new:
            failures += 1

    # Beyond the tolerance it must be an explicit skip, not a silent loss.
    way_late = slot.publish_at + dt.timedelta(minutes=600)
    in_due = slot in plan.due(ig, way_late, 360)
    in_overdue = slot in plan.overdue(ig, way_late, 360)
    print(f"\n600m late -> due={in_due} overdue={in_overdue}")
    if in_due or not in_overdue:
        print("FAIL: a slot past tolerance must appear in overdue(), not due()")
        failures += 1

    # And every post must be in exactly one bucket -- never lost between them.
    now = slot.publish_at + dt.timedelta(minutes=200)
    d = set(p.post_id for p in plan.due(ig, now, 360))
    o = set(p.post_id for p in plan.overdue(ig, now, 360))
    past = {p.post_id for p in ig if p.publish_at <= now}
    if d & o:
        print(f"FAIL: {len(d & o)} posts in both buckets")
        failures += 1
    if past != (d | o):
        print(f"FAIL: {len(past - (d | o))} past posts in neither bucket")
        failures += 1

    print("\nOK -- no slot is silently dropped" if not failures
          else f"\n{failures} FAILURE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
