# baby-onit: from toy to real onit

baby-onit is a distillation, and the distillation is the lesson. Each section header points at the onit file it came from, so the path from about 1,200 lines to about 60,000 is a straight line. What each part of baby-onit (one file) corresponds to in onit (104 files):

- DEFAULTS plus merge in baby-onit corresponds to pydantic models over defaults in onit.
- get_secret (env, keychain, file) in baby-onit corresponds to a full secrets service in onit.
- Provider (one chat method) in baby-onit corresponds to per-provider adapters plus load balancing in onit.
- The SYSTEM_PROMPT constant in baby-onit corresponds to a policy module in onit.
- The NEVER_ASK_COMMANDS set in baby-onit corresponds to command_policy.py with severity tiers in onit.
- The agent_loop while-loop in baby-onit corresponds to a supervised loop with verification in onit.
- The history.jsonl replay in baby-onit corresponds to persistent, resumable sessions in onit.
- The rich text UI in baby-onit corresponds to terminal, web, voice, and A2A frontends in onit.
- local_search BM25 in baby-onit corresponds to BM25 plus dense retrieval, fused, in onit.

The core idea survives the distillation intact: a model that can only propose, a harness that can only act, and a loop that passes messages between them. Learn that loop in one file, and the rest of onit is just scale.
