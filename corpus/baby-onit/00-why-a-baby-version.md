# baby-onit: why a baby version exists

baby-onit is a teaching narrative for the baby_onit.py codebase: a roughly 1,200-line, single-file agent harness distilled from onit.

onit is a large, production agent system, roughly 60,000 lines across 104 files, with terminal, web, and voice frontends, MCP servers, load balancers, and a verification layer. That is far too much to learn from in one sitting.

baby-onit is the same idea reduced to the minimum that still works: one Python file, one chat loop, a handful of tools, and a text UI. Every section header in the source (S1 to S9) names the concept it teaches and the onit file it came from, so a reader can always walk from the toy to the real thing.

The whole baby-onit program is one sentence: the model proposes; the harness acts; repeat until the model stops proposing. Everything else in baby-onit is an unpacking of that sentence.
