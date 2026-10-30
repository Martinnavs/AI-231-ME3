# Wave plan: shell tools and subagent runner

Context: the voice agent will hand hard tasks (build a small local website, multi-step math, terminal work) to a stronger model that runs in a background loop with tools, inside a sandbox. Design: `.scratch/onit-tools/PLAN.md`; experiment: `EXPERIMENT.md`.

One wave, two tickets, run in this order:

1. `01-shell-tools`: `bash`, `edit_file`, `grep` on the existing `code` MCP server, a command policy, and a `host_readonly` mode for the bwrap runner.
2. `04-subagent-runner`: `voice_agent/agent/subagent.py`, a model loop with limits and a spend ledger.

They touch disjoint files and nothing flows from one to the other, but run them in order to keep the work copy simple. Both are covered by their own Proof commands; the reviewer re-runs them.
