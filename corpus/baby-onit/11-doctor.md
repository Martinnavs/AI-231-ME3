# baby-onit: doctor (S8.9)

The doctor command in baby-onit checks your endpoints. It answers one practical question: which of my endpoints actually work right now? It takes one minimal task and runs it against every known endpoint in parallel, each probe bounded at 20 seconds, and streams the results into a table as they land.

Each row shows the host, the key (masked to its last four characters), the model, a status (ok, no key, offline, or error), a short answer or error, the model latency, and the prompt-token count. The footer summarises how many of the total endpoints are ok and lists each failure with its error.

Teaching point: doctor reuses the exact same Provider and chat path the baby-onit loop uses; it is not a separate health-check implementation. If doctor works, chat works. That reuse is the whole value of the Provider abstraction.
