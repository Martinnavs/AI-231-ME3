# 26: First words of an utterance are misrecognised — find the cause on recorded audio

**Repo:** /mnt/School/PhD/AI231/AI-231-ME3-agy
**Blocked by:** None
**Status:** ready-for-agent

## Why
In live doc-chat runs, 4 of about 23 utterances lost or garbled their first one or two words. The saved audio shows the words do reach the server, so the loss is in the recogniser or in how audio is cut and sent (pre-roll before the VAD onset, first-chunk size, finalize timing).

## Files
- `scripts/stt_first_words.py` (offline replay), `src/doc_chat/speech.py` / `src/lan_speech/*` (only if a fix is found), `tests/lan_speech/test_first_words.py`, `docs/STT-COMPARE.md` (results)

## Action
Collect the utterances from `out/sessions/*` that had the problem and the ones that did not. Replay each through the server with variants: (1) as sent; (2) 200 / 400 ms of leading silence or room noise added; (3) a larger pre-roll; (4) hotwords on/off for opening words ("tell me", "what", "who"). Report word error rate on the first two words per variant (n stated). Apply the smallest fix that helps, with a numeric fixture test.

## RECAP
### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | fixture of at least 8 recorded openings; assert first-two-word accuracy with the chosen fix is at least the baseline and improves on the failing ones | `uv run pytest -q tests/lan_speech/test_first_words.py` |

## Acceptance Criteria
- [ ] Cause named from data, or "not found" with what was ruled out
- [ ] If fixed: failing utterances recognised, passing ones unchanged

## Non-Goals
- Switching STT models

## Security-sensitive
no

## Execution Log
