# 05: PLAY_MUSIC gets a slot, so the router plays a named artist, song, genre or mood itself

Run on the cluster. Repo `AI-231-ME3`, branch `agy/interrupts` (pull first), working dir `finetune/`.
Report the bottom line first, say plainly what is the owner's decision, never overstate.

## In one paragraph
The live router is `kd-web4-s0`. Since ticket 04 (and the laptop's call-policy rule V1) every "play <a named artist, song,
genre or mood>" escalates, because PLAY_MUSIC has no slot and the artist would be lost. A spoken session on 2026-11-06 was
music-heavy: 34 of 61 turns went to the hosted model, and those turns took 2.3-3.6 s from end of speech to sound against
1.1-1.6 s for router turns. The music tool already takes a query (the hosted model fills it). This ticket gives the
router's PLAY_MUSIC an optional slot holding the user's own words for what to play, so these turns stay local.

## The change to the line protocol (spec)
- `PLAY_MUSIC` moves from `HOME_NO_SLOT` to `HOME_SLOT_FORMS` with the form `"what to play, in the user's words (an artist,
  a song, a genre, a mood, a playlist), or empty for plain music"`. `PLAY_MUSIC` alone (no `|`) stays valid and means plain
  music. Keep the production enum order (`HOME_ORDER`) unchanged.
- The slot is the user's words, trimmed of the verb and filler ("can you play", "please", "for me"): "play some Taylor
  Swift please" -> `PLAY_MUSIC|Taylor Swift`; "put on something chill" -> `PLAY_MUSIC|something chill`; "play Cruel Summer by
  Taylor Swift" -> `PLAY_MUSIC|Cruel Summer by Taylor Swift`. Do not correct spellings or guess an artist the user did not
  say; the music service's search handles near misses.

## Tasks
1. **Spec** (`spec/tools.py`, `data/format.py` if the slot form is rendered there): the slot form above; parsing and
   rendering accept both `PLAY_MUSIC` and `PLAY_MUSIC|<words>`. Update the spec tests.
2. **Seed** `s_play_named` (weight about 5): named artists (Filipino and international), songs with and without "by
   <artist>", genres, moods, playlists, decades -> `PLAY_MUSIC|<words>`. Use your own names, not the laptop's examples above.
   Retarget ticket 04's `s_play_target` music half (artist / genre / mood -> was `escalate|tools`) to `PLAY_MUSIC|<words>`.
   Keep its game / app / "the thing you made" / "it" half as `escalate|tools`, and bare music ("play music", "play some
   music", "play something") as plain `PLAY_MUSIC`.
3. **Follow-ups** with history: after a PLAY_MUSIC turn, "play <another artist> instead", "switch to <song>", "something
   else by them" -> `PLAY_MUSIC|<words>` ("by them" keeps the user's words; do not resolve the pronoun). "play it again" /
   "resume" stay plain `PLAY_MUSIC`.
4. **Tests** in `tests/test_data.py`: every `s_play_named` target's slot is a substring of the user text (case-insensitive,
   after removing the verb and filler); no game/app wording ever gets PLAY_MUSIC; bare music has no slot.
5. **Data and training:** the ticket-04 recipe, run names `web5`. Never GPU 6. Check the teacher on the new seed first.
6. **Push** the student adapter as `martinnavs/granite-4.0-h-350m-router-kd-web5-s0-unverified` (private).

## Acceptance criteria (cluster side)
- [ ] `pytest` in `finetune/` green, including the new tests.
- [ ] Dev exact per kind next to kd-web4-s0 (98.7% full dev, old kinds 99.1%); old kinds must not drop by more than 1 point.
- [ ] Your own held-out check, 50 cases with names and phrasings not in the banks: 25 named music (slot exact or a
      faithful trim), 10 follow-ups, 10 game/app contrasts (must escalate), 5 bare music (no slot).
- [ ] Ticket-04 held-out (70) and ticket-03 held-out (70) re-run: no regression except the music half of play-target,
      which now expects `PLAY_MUSIC|<words>`.

Laptop side after delivery (not yours, for context): the call-policy rule V1 will accept a router `PLAY_MUSIC|<words>`
(it currently sends any named target to the hosted model); then the held-out and probe re-runs as for kd-web4.

## Do not look at / copy from
`.scratch/` other than this ticket, `out/sessions/`, `docs/stress/`; no wording from `pipeline/config/eval/` or
`eval/onit_eval.jsonl`. Use your own names and phrasings.

## Hard rules
- Never read or print API keys or anything under `~/.secrets`.
- No paid API. Stop servers by PID (never `pkill -f`); GPU 6 is off limits.
- Git: add by explicit file name, trailers as before, push only when the owner asks, never force, never push main.

## Execution Log
(append dated entries below; never rewrite earlier ones)
