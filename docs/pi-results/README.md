# Pi test results go here

Created for `docs/PI-TEST-PLAN.md`. After running the tests on the Raspberry Pi:

```bash
scripts/pi_pack_results.sh        # copies out/pi/*/run.json + run.md (+ trials.json) here, one folder per run
```

then fill in `NOTES.md` (copy of the template below), commit and push:

```bash
git add docs/pi-results && git commit -m "pi: echo test results" && git push
```

The recordings (`*.wav`) stay in `out/pi/` (git-ignored, large). Copy the double-talk folders to the laptop with `scp -r` and score them with `scripts/pi_double_talk_score.py`.

Compare conditions in one table: `python scripts/pi_echo_check.py compare docs/pi-results/*selftest*`.

The runs from 2026-10-22 are in this folder; what they showed is in `docs/PI-RESULTS.md` (the `NOTES.md` hardware table lists doubletalk as "not run" for the first conditions; the later doubletalk runs are in the notes below it).
