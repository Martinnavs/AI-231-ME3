# baby-onit: one chat call, many servers (S3)

The Provider class is the abstraction that lets the same baby-onit loop talk to Ollama, vLLM, SGLang, OpenRouter, Vercel, OpenAI, or Claude. The trick is that almost all of them speak the OpenAI-compatible protocol, so one chat() method covers them. Ollama is the one odd one out with a native API, and the gpt-6 family is the other odd one out, needing /v1/responses instead of /v1/chat/completions.

A few small helpers keep the surface area honest in baby-onit:

- is_ollama_host(host) sniffs the URL to pick the native path.
- normalize_host(host, ollama) strips a trailing /v1 or /chat/completions so either spelling in config.yaml works.
- _is_openai_responses_model(model) routes the gpt-6 family to the responses API, because /v1/chat/completions refuses its tool calls.

chat(messages, tools, stream_cb) is the only method the loop ever calls. It returns (assistant_message_dict, usage_dict), and an optional stream_cb lets the UI paint tokens as they arrive. The provider also remembers the chosen model per endpoint in ~/.baby-onit/models.yaml, so switching endpoints in the REPL prefills the right model automatically.

Teaching point: the baby-onit loop is written against Provider, never against openai or ollama. That is what makes "any endpoint" a one-line config change rather than a code change.
