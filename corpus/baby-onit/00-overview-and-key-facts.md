# baby-onit: overview and key facts

What is baby-onit? baby-onit is a single-file agent harness, about 1,200 lines of Python in baby_onit.py, distilled from onit. It is the minimum that still works: one chat loop, a handful of tools, and a text UI. It exists to teach how an agent harness works.

What is onit? onit is a large production agent system, roughly 60,000 lines across 104 files, with terminal, web, and voice frontends, MCP servers, load balancers, and a verification layer. baby-onit is onit reduced to one file so that it can be learned in one sitting.

How does baby-onit work? The model proposes; the harness acts; repeat until the model stops proposing. The harness sends the task to the model; the model answers in plain text or emits a tool call; the harness runs the tool and sends the result back; the loop ends when the model stops calling tools.

What is baby-onit's safety model? The model never touches the machine. It can only ask for a tool, and the harness, baby_onit.py, is the only thing that executes anything. File tools are confined to a jail directory, and bash refuses a blocklist of commands called NEVER_ASK_COMMANDS.

Where does baby-onit keep secrets? Never in config.yaml. Keys are resolved at runtime from an environment variable, then the OS keychain, then a 0600 file at ~/.baby-onit/secrets.yaml.

Which model servers does baby-onit support? Ollama, vLLM, SGLang, OpenRouter, Vercel, OpenAI, and Claude, through one chat call, because almost all of them speak the OpenAI-compatible protocol.

What commands does baby-onit have? setup, chat, run "task", and doctor.

Where is baby-onit? The code is in the repository github.com/sibyl-oracles/baby-onit.
