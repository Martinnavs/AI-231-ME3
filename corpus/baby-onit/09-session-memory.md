# baby-onit: session memory (S7.5)

Session memory in baby-onit: in the REPL, every line you type is a fresh agent loop call. A fresh call has no memory of the previous line, so the harness gives it some.

build_session_messages replays the prior conversation: each stored {task, response} pair becomes a user message plus an assistant message, oldest first, and those are prepended to the current task.

Because a long session would blow the context budget, _trim_history keeps the most recent turns in full and head-trims the older ones: answer bodies are cut to a short head, and questions are kept whole. The result is a conversation that is almost the full history but bounded in size.

Teaching point: this is a cheap stand-in for real memory. onit persists sessions and can resume them across processes; baby-onit keeps the history in a history.jsonl file and replays it in-process. Same idea, smaller footprint.
