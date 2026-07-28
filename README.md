# WildNatureUSA social automation

Publishes the 6-month content plan to a Facebook Page and an Instagram
Business account: 1,288 posts across 184 days, Aug 1 2026 – Jan 31 2027.

| Platform  | Daily cadence                                      |
|-----------|----------------------------------------------------|
| Facebook  | 4 text facts (8am, 12pm, 4pm, 8pm ET) + 1 quiz image (6pm ET) |
| Instagram | 2 quiz images (11am, 7pm ET)                       |

Every quiz post gets its answer as the first comment 60 minutes later.

## How it works

**Facebook** accepts image bytes directly and supports native scheduling, so
posts are handed to Meta's scheduler up to a week ahead. They fire even if
this runner is down.

**Instagram** has neither: image posts need a publicly fetchable URL, and the
publish call has to happen at the moment the post goes live. The rendered
PNGs are committed to this repo and served from `raw.githubusercontent.com`,
and the hourly workflow makes the call at the right slot.

Times in the plan are US Eastern and the run crosses the Nov 1 2026 DST
boundary, so the code uses a real `zoneinfo` timezone, never a fixed offset.
The workflow runs hourly and each run is a no-op unless something is due, so
a delayed or failed run self-heals on the next tick.

`Post_ID` is the idempotency key. A post recorded in `state/state.json` is
never sent twice, no matter how often a run repeats.

## Layout

```
plan/    wildnatureusa_6month_content_plan.xlsx   original, read-only
         content_plan_fixed.xlsx                  what actually publishes
tools/   grammar.py, rewrite_plan.py, validate_plan.py
src/     plan.py, render.py, meta.py, state.py, publish.py
images/  552 rendered quiz PNGs (public -- Instagram fetches these)
state/   state.json  run-time status, committed after each run
```

## Setup

**1. Push this to a public GitHub repo.** Public matters for two reasons:
Actions minutes are unlimited, and Instagram must be able to fetch the images
over `raw.githubusercontent.com`. Nothing secret lives in the repo — tokens
are Actions secrets and `.env` is gitignored.

**2. Add the Actions secrets** (Settings → Secrets and variables → Actions):

- `WILDNATUREUSA_PAGE_ID`
- `WILDNATUREUSA_PAGE_TOKEN`
- `WILDNATUREUSA_IG_USER_ID`

These already exist in `Automation/.env` from the reels pipeline.

**3. Use a System User token.** A normal long-lived Page token expires after
~60 days; this plan runs 184. Create a System User in Meta Business Manager,
assign it to the Page, and generate a token that never expires. Check what
you have with `python -m src.publish status` — it reports days remaining.

Required permissions:

```
pages_manage_posts  pages_read_engagement  pages_show_list
instagram_content_publish  instagram_manage_comments
```

`instagram_manage_comments` is the one the workbook's instructions omit —
without it the Instagram answer comments fail.

**4. Optional: bundle a font.** The renderer looks for `fonts/Poppins-Bold.ttf`
first (per the Style tab), then Nunito, Montserrat, and finally whatever the
host has. Dropping Poppins-Bold.ttf into `fonts/` and committing it keeps
local and CI renders identical.

**5. Turn it on** by setting the repo variable `PUBLISH_ENABLED` to `true`
(Settings → Secrets and variables → Actions → Variables). Until then the
hourly workflow runs as a dry run, so merging it does not start posting.

## Commands

Nothing publishes without `--live`.

```bash
python -m src.publish status
```

```bash
python -m src.publish schedule-fb --days 7
```

```bash
python -m src.publish run --live
```

Simulate a moment to see what would go out (dry run only):

```bash
python -m src.publish run --now 2026-08-01T11:05
```

Re-render images after editing the plan:

```bash
python -m src.render
```

## Editing the content

`plan/content_plan_fixed.xlsx` is what publishes. After editing it, re-render
the affected images and re-run the validator:

```bash
python tools/validate_plan.py plan/content_plan_fixed.xlsx
```

It exits non-zero on any grammar defect, checking for stacked copulas
("is lays"), subjects with no verb, a/an disagreement, doubled articles,
naive plurals, and unfilled template slots.

`tools/rewrite_plan.py` regenerates the fixed workbook from the original. It
repaired 261 defects in the source, including 180-odd captions where a raw
predicate had been glued onto a subject without a linking verb, and widened
the fact-post phrasing from 10 rotating openers to 56 templates.

## Notes

- Instagram allows 50 posts per rolling 24h. This plan uses 2.
- Facebook rejects a scheduled time less than 10 minutes out; the scheduler
  keeps a 20-minute lead.
- A scheduled Facebook photo returns a photo id. Comments need the post id,
  which the publisher resolves via `page_story_id` once the post is live.
