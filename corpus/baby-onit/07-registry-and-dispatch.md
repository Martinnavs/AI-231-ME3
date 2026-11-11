# baby-onit: registry and dispatch (S6)

The model emits tool calls as JSON. The baby-onit harness has to turn that JSON into a real function call, safely. The dispatch function, dispatch(name, args), is that bridge, and it is the most defensive function in the file. It does five things:

1. Unknown tool: return an error string listing what is available. The loop never crashes on a hallucinated tool name.
2. Malformed args: run them through json_repair, because the model sometimes emits slightly broken JSON. If that fails, return an error, not an exception.
3. Type coercion: _coerce_args reads the function's type hints and casts "5" to 5, "true" to True, and so on. Models are loose about types; the harness is not.
4. Repeated-call guard: a (name, args) pair that has already run returns the cached result instead of re-running, so a stuck model cannot burn the turn budget on the same call.
5. NEVER_ASK guard: for bash, the blocklist is enforced here too.

The result of every tool is a string, wrapped by _tool_message into a tool-role message and appended to the conversation. Read-only tools may run in parallel; mutating ones run serially. That is a small but important distinction the loop makes.

Teaching point: dispatch is where "the model proposes" meets "the harness acts." Every failure mode in baby-onit (bad name, bad JSON, wrong type, repeat, forbidden command) is handled by returning a message the model can read, never by raising an exception that kills the loop.
