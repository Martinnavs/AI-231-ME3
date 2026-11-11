# baby-onit: the text UI (S8)

The baby-onit UI is a one-way display: it renders loop events for the human and never talks back to the model. It is built on rich (panels, tables, syntax highlighting) and does three things:

- Banner: it shows the host, model, working directory, and tool count at startup.
- Live rendering: each on_event is painted, including streamed tokens, tool calls, tool results, and the final answer.
- REPL: a prompt with line history, plus a few backslash commands: \reset clears session memory, \clear drops the current endpoint config, \model switches models, and so on.

The strict one-way rule is the point. The baby-onit UI can be replaced by a web frontend, a voice frontend, or nothing at all, and the loop does not change. That is exactly the separation onit makes between its many frontends and its core.
