# The baby-onit corpus (document search)

`corpus/baby-onit/` (16 files, about 3,000 words) is what the agent's document search (`search_docs`, the `docs` MCP server, prompt Case C) answers from. It is the teaching document **"baby-onit, Explained"** (`baby_onit_features.pdf`, 12 pages), a walk through `baby_onit.py`, the single-file agent harness distilled from onit; the code is at https://github.com/sibyl-oracles/baby-onit. It is the document the professor knows. It **replaced** the Cormac McCarthy corpus (33 files) and the Harry Potter corpus (9 files), removed 2026-11-11 (they remain in git history and on the `archive/` tag of the dev branch).

## What is in it

One file per section of the PDF, in its order and wording: `01-the-big-picture`, `02-one-config-dict` (the config keys and their defaults), `03-secrets` (the three tiers), `04-providers-one-chat-call`, `05-system-prompt`, `06-tools`, `07-registry-and-dispatch`, `08-the-agent-loop`, `09-session-memory`, `10-the-text-ui`, `11-doctor`, `12-setup`, `13-from-toy-to-real` (the baby-onit to onit table), plus `00-why-a-baby-version`, `00-overview-and-key-facts` (question-and-answer phrasing for "what is baby-onit?" and similar) and `14-about-this-document`. Nothing was added that the PDF does not say. Tables became plain sentences so an answer can quote them, and each concept is named in ordinary words next to its identifier (the loop is "the agent loop, the function `agent_loop`") because the retriever tokenises `agent_loop` as one word.

**One discrepancy kept on purpose:** the PDF says the harness "ships eight" tools but its table lists nine (`web_search`, `fetch_content`, `bash`, `write_file`, `read_file`, `edit_file`, `local_search`, `search_document`, `grep`). The corpus states both, so "how many tools" is answered with the document's own "eight" and the list can still be read out.

## How it is searched

`voice_agent.mcp_servers.docs`: BM25 over chunks, `CHUNK_SIZE = 700`, top 3. On a 21-question set, 1,000-character chunks put the answering section in the top 3 for 20, 700 for 21 and 500 for 19 (the subject name is in almost every chunk, so a section that only repeats it outranked the one that answers; smaller chunks fix that). The question set was written with the corpus, so it guards against regressions but is not an independent benchmark. The router's document race and Gemini's `search_docs` call both use this server; the race accepts a document answer only when its quote is copied verbatim from the passages.

## Where else it is wired

| Place | What changed (2026-11-11) |
|---|---|
| `pipeline/config/eval/system_agent.txt`, `system_agent_search.txt` | Case C is "anything about baby-onit or onit"; Case B says "not baby-onit or onit" |
| `src/voice_agent/mcp_servers/tavily_search.py` | the `web_answer` description says "Not for baby-onit or onit (use search_docs)" |
| `pipeline/src/voice_pipeline/call_policy.py` (`about_document_topic`) | the router's web lookup is **rejected** when the question names baby-onit or onit and `search_docs` is available, so the turn goes to Gemini's Case C. Without it the router (trained before this corpus) sent "How many tools does baby-onit ship?" to the web and Gemini spoke a confident wrong answer about a company that ships physical baby tools. |
| `pipeline/config/eval/agent_prompts.jsonl` | the docs-kind regression prompts (`n03`, `d01`-`d08`) ask baby-onit questions |
| `config/entities-baby-onit.json` | speech names: the 1,444 general names plus 35 document names (baby onit, onit, Ollama, vLLM, SGLang, OpenRouter, Vercel, DuckDuckGo, REPL, MCP, A2A and file and variable names in spoken form), category `doc`, curated by hand because `scripts/make_entities.py` only finds capitalised names. It is the `ENTITIES` default of `make up`; `ENTITIES=config/entities.json make up` restores the general list. `place_check` still reads `config/entities.json`. |
| `tests/mcp_servers/test_docs_server.py` | the 21 questions above, the default corpus, the tool description, the "eight tools" wording, the top-k clamp |

## Checked live (2026-11-11, text turns, real router and Gemini)

"What is baby-onit?", "Where do baby-onit's API keys live?" (environment variable, then keychain, then `~/.baby-onit/secrets.yaml`), "What does the agent loop do?", "How is baby-onit different from onit?", "What does baby-onit's doctor command check?" were answered from `search_docs`; "How many tools does baby-onit ship?" (with and without the hyphen) is answered from the documents as "said to ship eight tools, but the table lists nine" (after the guard above; before it, one run went to the web and answered wrongly). "Who was Cormac McCarthy?" now goes to Gemini (no corpus); weather and time are unchanged.

## Not checked, and limits

- **By voice:** whether speech recognition hears "baby-onit" and the other names (the names file should help; it was not measured).
- **Read aloud:** Gemini sometimes says identifiers such as `BABY_ONIT_API_KEY` as written.
- The 350M router was trained before this corpus; document questions reach `search_docs` through Gemini's Case C or through the race, not through a router intent.
- Only this PDF is indexed, not the repository's code.
- A turn is sent to the documents only if the words baby-onit, baby onit or onit are recognised; a misheard name ("baby unit") falls to the router and the web. The names file is meant to prevent that; it was not measured by voice.
- **Network:** on this laptop about 1 in 12 fresh TCP connects to Google timed out (2 s) in a test; the agent's online probe goes offline after two misses in a row and then answers "I can't reach the internet right now" until the next success. It flipped once during the 2026-11-11 smoke test. Not changed here (`OnlineProbe`, `agent_server.py`); worth a 3-miss threshold if the venue link is poor.

## Replacing the corpus again

Put `.md` or `.txt` files in a folder (every file in it is indexed, so no source lists), then: `--corpus` / `DEFAULT_CORPUS` and `DEFAULT_TOPIC` in `docs.py`; the Case C and Case B lines in both system prompts and the `web_answer` description; the docs-kind rows of `agent_prompts.jsonl`; the names file (`scripts/make_entities.py <folder> --merge config/entities.json`, then add the lowercase names by hand); and the questions in `test_docs_server.py`.
