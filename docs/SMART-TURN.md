# Smart Turn v3 as the end-of-turn detector: experiment

Question: can a small model that judges "has the speaker finished?" replace our fixed pause
(`PauseDetector`, 0.5-0.6 s of quiet), ending turns sooner without cutting people off?

Model: Pipecat Smart Turn v3.2 (CPU), `smart-turn-v3.2-cpu.onnx`, 8.7 MB (8M parameters, Whisper
Tiny encoder plus a linear head, 8 s input window, returns P(turn finished)). Downloaded to
`models/smart-turn/` (git-ignored; from `huggingface.co/pipecat-ai/smart-turn-v3`). Needs
`transformers` for the Whisper feature extractor: run the scripts with `uv run --with transformers`.

## Method (one speaker, my own recordings)

- `scripts/smart_turn_eval.py`: cuts each clip at the end of speech (+ s ms of quiet) and at the
  quiet gaps inside it (energy based), and scores the model at several silence lengths.
- `scripts/smart_turn_dangling.py`: cuts real recordings after a word that cannot end a request
  ("by", "the", "and", "to"...) using Parakeet word times, appends s ms of room noise, and asks the
  model. A fixed pause would end these turns at 0.5 s; a good turn-end model should wait.
  Controls: the full clips (real ends).
- The model itself was checked on synthetic speech first: "I was thinking that we could go to the"
  scores 0.07 (waits), "What is the capital of France?" 0.98 (ends). So the feature pipeline is right.

## Results

| test (threshold 0.5) | finished turns ended | unfinished cuts wrongly ended |
|---|---|---|
| inner energy gaps, 120 clips (133 gaps) | 98% at 200 ms | 100% |
| after a dangling word, short commands (95 clips, 234 cuts) | 100% at 200 ms | 99% at 200 ms, 99% at 500 ms |
| after a dangling word, read prose (25 clips, 99 cuts) | 96% at 200 ms | 93% |
| after a dangling word, threshold 0.9, commands | 97% | 97% |

Cost per check on this laptop, 1 thread: features 13-18 ms + model 64-84 ms = 77-102 ms p50 (the
project quotes about 12 ms on a modern CPU; we did not reproduce that).

Synthetic probes (Supertonic voice, same method): chatty sentences ("I was thinking that we could
go to the", "...and then") score 0.01-0.1, so the model does wait there. Request-style phrasing
does not: "Please play something by" 0.97, "...Taylor Swift in the" 0.99, "Play some music by"
0.98, "Please call my mother and then" 0.87.

## Reading

Out of the box it cannot tell a finished request from an unfinished one in the kind of speech
this agent hears (short imperative requests): it calls nearly everything finished. Ending turns
on its score would cut people off at the pauses a fixed 0.5 s hold already survives, and it
would not hold the turn open where we need that (names after "by", "call my..."). It does not
reduce engineering, because it would still need our hold as the safety net.

It does behave well on conversational, disfluent speech, which is not what we serve first.

## What would still be worth trying

1. Use its score only as a veto that extends the hold (when it is very low), never to end sooner.
   Cheap to add, but the data above shows it rarely goes low on requests, so the gain is small.
2. Fine-tune on request-style prefixes (the training code is in the Smart Turn repo): cut our
   command and entity phrases at every word boundary, label dangling prefixes "incomplete".
3. A text-based end-of-turn check on the Kroko interim transcript (LiveKit's turn-detector style):
   cheaper than audio, and "play something by" is obviously unfinished as text.
4. Test on more than one speaker; every number here is from one voice.

Not done: running it inside the live loop, or under Parakeet load.

## Text-based check (Turnsense, added after the audio result)

The audio model judged request-style pauses wrongly, so I tried a model that reads the words:
[Turnsense](https://huggingface.co/latishab/turnsense) (Apache-2.0, SmolLM2-135M, int8 ONNX 176 MB,
`models/turn-detector/`, outputs P(finished)). Not used: LiveKit's turn-detector, whose licence
allows use "only with the LiveKit Agents framework", not standalone or with other frameworks.

Cost: 24 ms p50 per check on 1 thread (16 ms for short texts), unpadded input. The sample code
pads to 256 tokens, which costs about 195 ms for the same answer; do not do that.

`scripts/text_turn_eval.py` (words from Parakeet, same cuts as above), threshold 0.5:

| set | finished turns ended | dangling cuts wrongly ended |
|---|---|---|
| commands, words as Parakeet writes them | 98% (93/95) | 13% (31/234) |
| commands, lower case no punctuation | 92% (87/95) | 13% |
| read prose | 80% (20/25) | 15% (15/99) |
| commands, threshold 0.7 (Parakeet-written) | 88% | 3% (6/234) |

The audio model wrongly ended 99% of the same dangling cuts. Words are a far better signal for
requests. But Parakeet's words only exist about 340 ms after a pause starts, no earlier than the
fixed 0.5 s hold. What exists at a 200 ms pause is Kroko's interim text, which lags the speech
(it had 83% of the words at the end of finished clips and often a cut-off last word:
"put on blinding", "play some bad bunny in the living ro"). `scripts/text_turn_kroko.py`:

| pause | threshold | finished turns ended | dangling wrongly ended |
|---|---|---|---|
| 200 ms | 0.5 | 69% (66/95) | 34% (79/234) |
| 200 ms | 0.7 | 48% | 21% |
| 500 ms | 0.5 | 80% (76/95) | 39% (92/234) |
| 500 ms | 0.7 | 55% | 26% |

So on the words available early, the text check ends 69% of finished turns about 300 ms sooner
but also cuts off a third of the unfinished ones. "Other cuts" such as "play opalite" (62% judged
complete) are ambiguous by nature: the speaker may be done or may go on.

## Reading

Neither model is a drop-in replacement for the fixed pause. The text model is the better signal
but needs good words early, and the early words are Kroko's. Where it does help without risk is
as a veto after the pause: if the transcript clearly ends on a dangling word, do not send it
as is. That is the place for a "got it, anything to add?" prompt (proposed, not built).

Not done: the continuation prompt and stitching; any run inside the live loop; other speakers.
