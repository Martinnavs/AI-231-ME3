# baby-onit: tools (S5)

A tool in baby-onit is a plain Python function with a JSON-schema business card. The model sees only the card (name, description, parameter schema); it never sees the code.

The document says the baby-onit harness ships eight tools, and the table that follows it lists nine entries:

- web_search: DuckDuckGo or Tavily web or news search. No guard.
- fetch_content: pull a URL and extract text or media. No guard.
- bash: run a shell command in the jail. Guarded by the NEVER_ASK_COMMANDS blocklist.
- write_file: create or append a file. Guarded by the jail path check.
- read_file: read a file (text, tables, images). Guarded by the jail path check.
- edit_file: exact string replace in a file. Guarded by the jail path check.
- local_search: BM25 search over local docs. No guard.
- search_document: search within one document. Guarded by the jail path check.
- grep: regex search across files. Guarded by the jail path check.

Three design details are worth studying. The first is the bash jail. _resolve(path) pins every path to DATA_PATH and refuses anything that escapes it, when enforce_jail is on. Separately, bash refuses a frozen set of commands, the NEVER_ASK_COMMANDS blocklist (the never-ask list), the "become someone else or leave the machine" executables such as sudo, docker, ssh, systemctl and shutdown. This is the same set onit refuses as CRITICAL without asking. The comment in the source makes the distinction explicit: reaching for one of these is a different kind of event from reaching for a linter nobody listed.

The second is local_search, the only nontrivial tool left in baby-onit. It is a complete retrieval pipeline in about 80 lines: chunk, index, rank. _chunks splits text on blank lines into paragraph-aware chunks with overlap, the same defaults onit uses. _tokens lowercases, extracts alphanumerics, and drops a small stop-word set. The index is a plain in-memory BM25 table with no embeddings, so it works offline on any corpus. tool_local_search ranks the query tokens against every chunk and returns the top-k sections.

Teaching point: a tool's interface is the schema; its implementation is an implementation detail the model never sees. You can rewrite local_search with embeddings and the loop does not change at all.
