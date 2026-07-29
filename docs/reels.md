# Quiz posts as Reels -- status

Prototype phase done and proven against real content. Not wired into the
publisher yet -- nothing here posts anything on its own.

## Design decisions made

**On-demand rendering, never precomputed.** One clip took ~90s to render
locally; rendering the full 552-quiz backlog would be ~14 hours for no
benefit. Reels also don't need the public-URL treatment Instagram's *image*
posts require -- both platforms' Reels endpoints take the video as raw bytes
(same resumable-upload protocol the AdiGiVault reels pipeline already uses),
so there's nothing to gain from committing rendered video the way the 368
committed `IG-FCT-*.jpg` fact cards need to be. At the agreed first-rollout
scope (quiz only, both platforms), the daily render load is 3 clips/day
(1 Facebook + 2 Instagram) -- a few minutes of CI time per relevant run, not
a batch job.

**Facebook Reels can be natively scheduled; Instagram Reels cannot.**
Checked against Meta's docs directly rather than assumed: `/video_reels`
accepts `video_state: SCHEDULED` + `scheduled_publish_time` in the finish
step of the same resumable-upload protocol used for immediate publish --
mechanically identical to the flow already live for photos and text. So the
Facebook reel should be scheduled in the *same* `schedule-fb` run that
schedules its parent image, 30 minutes later, with zero new timing risk.
Instagram has no scheduling for any content type, same as its image posts
today -- the reel needs the same due-queue pattern already proven for the
answer-comment mechanism (`state.queue_comment` / `due_comments`), with the
same lateness tolerance this repo already learned it needs (§4 of
`playbook.md`: observed cron gaps up to 254 minutes).

**Rollout scope: quiz posts only, both platforms, for now.** Converting fact
posts too would roughly double daily Facebook volume in one step; safer to
watch one new format land cleanly first.

## What exists

- `tools/reel_engine.py` -- the animation engine. Ken Burns pan/zoom (two
  source builders: a letterbox variant for photos with a uniform background,
  and `build_kenburns_cover_source` for the general case), plus three text
  effects (`draw_wipe`, `draw_staggered_lines`, `draw_fade`) and
  `scrim_layer` -- a top/bottom gradient so text stays legible on an
  arbitrary photo without knowing its composition ahead of time.
- `src/reel.py` -- `render_quiz_reel(post, credits)`, which pulls the real
  photo + attribution from `photos/credits.json` (the same library
  `render_fact()` already uses) and renders a 6s, 1080x1920, 30fps MP4 to a
  temp directory. Caller deletes it once uploaded.

Tested against three species of very different photo shapes -- Beluga Whale
(1.9:1 landscape), Ruby-throated Hummingbird (portrait close-up), Ostrich
(wide landscape) -- all rendered cleanly with the cover-crop + scrim
approach.

## A defect this surfaced, now fixed

Ostrich's auto-fetched photo (`Struthio_Diversity.jpg`) was a two-panel
montage; a centred crop landed squarely on the seam between the panels, in
both the new reel and the existing static Facebook fact card (`render_fact`
uses the same centred-crop logic). `--force` alone re-selects the same photo
deterministically, so this needed a pin, same mechanism as the three already
in `photos/pinned.json`. Replaced with "Three ostriches walking in grass,"
CC BY-SA 4.0, genuinely in-habitat. `photos/credits.json`, `photos/pinned.json`,
`photos/ostrich.jpg`, and the committed `images/IG-FCT-0054.jpg` are all
updated. Worth a similar audit pass across the rest of the 197 -- this is
one photo out of many chosen by the same heuristic, not necessarily the only
one with a problem like this.

## What's not built yet

- `src/meta.py`: `fb_reel_schedule` / `fb_reel_publish` (resumable upload +
  `video_state`), `ig_reel` (resumable upload, no scheduling parameter).
- `src/state.py`: a reel due-queue mirroring `queue_comment` / `due_comments`
  / `mark_comment` exactly, with its own idempotency key
  (e.g. `f"{post_id}:reel"`) so a reel retry can never collide with its
  parent image post's state entry.
- `src/publish.py`: schedule the Facebook reel alongside its image in
  `schedule-fb` (image time + 30 min); queue the Instagram reel 30 min after
  its image fires, processed through the due-queue each run.
- A `test_fire`-style one-shot live proof (schedule/post one real reel,
  confirm it actually appears) before `PUBLISH_ENABLED` covers reels too.
