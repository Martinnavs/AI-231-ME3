# baby-onit: one config dict (S1)

Every knob in baby-onit lives in a single dictionary. The function load_config builds it by deep-merging three sources, in order of increasing priority: DEFAULTS, then the file ~/.baby-onit/config.yaml, then an explicit --config path.

DEFAULTS holds the sensible starting values for baby-onit:

- serving.provider defaults to auto: which server family to use, auto-detected from the host.
- serving.host defaults to http://localhost:11434: the model endpoint.
- serving.model defaults to an empty string: blank means auto-detect via models.list().
- serving.think defaults to True: route reasoning to its own field, not the answer.
- serving.max_tokens defaults to 32768: the per-response cap.
- serving.max_chat_iterations defaults to -1: the turn cap, where -1 means unlimited.
- serving.max_context_tokens defaults to 262144: the compaction trigger threshold.
- data_path defaults to ~/baby-sandbox: the agent's jail root.
- enforce_jail defaults to True: confine the file tools to data_path.
- block_dangerous defaults to True: refuse NEVER_ASK commands in bash.

The deep merge matters: a partial config.yaml that only sets serving.model keeps every other default. You never have to write a complete file.

Teaching point: onit uses pydantic models over defaults for the same job. In baby-onit a plain recursive merge is enough because the schema is small and stable.
