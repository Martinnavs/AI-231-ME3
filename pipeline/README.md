# pipeline/: the Pipecat side of the voice agent

A separate uv project (own `pyproject.toml`, `uv.lock`, `.venv`) because `pipecat-ai==1.13.0` pins `onnxruntime~=1.24` and `soundfile~=0.13`, which the root project's speech models must not take. The root `Makefile` drives it: `make pipeline-sync`, `make pipeline-check`, `make pipeline-test`, `make smart-home-dryrun`, `make check-all`, `make test-all`.

| Module | What it is |
|---|---|
| `voice_pipeline.tools.smart_home` | the `smart_home(intent, slot)` tool: Pipecat schema + handler for ME2's `POST /api/command` |
| `voice_pipeline.dryrun` | scored dry run of the tool over `config/eval/smart_home_prompts.jsonl` |
| `voice_pipeline.latency` | serial latency run with one reused connection per model |

Results, decisions and how to run: `docs/SMART-HOME-TOOL.md`.
