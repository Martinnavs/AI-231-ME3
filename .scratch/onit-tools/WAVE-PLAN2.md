# Wave plan 2: the delegate server and the spoken notices

Context: the voice agent hands big tasks (a website, hard math with code, an audit) to haiku-5.5 (fallback: the class Qwen server) working in a sandbox. Wave 1 (shell tools, subagent runner) is already in the repo. Design: `PLAN.md`; results: `EXPERIMENT.md`.

One wave, two tickets, in this order (05b reads a file that 05a creates, so they are not parallel):

1. `05a-delegate-server` (root project): `jobs.py`, the `delegate` MCP server, `config/delegate.toml`, the golden notice file, tests.
2. `05b-pipeline-notices` (the `pipeline/` project): `notices.py`, the agent starts the delegate server and speaks finished jobs after the next answer, the system prompt gains the delegate case.

Both are covered by their own Proof commands; the reviewer re-runs them.
