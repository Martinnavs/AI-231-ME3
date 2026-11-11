# baby-onit: secrets (S2)

The single most important rule in the baby-onit harness is that secrets never live in config.yaml. config.yaml may hold the host and the model, but it may not hold a key.

Keys are resolved at runtime by get_secret(name), which walks three tiers and returns the first one that yields a value:

1. An environment variable: BABY_ONIT_API_KEY for endpoint keys, or GITHUB_TOKEN, HF_TOKEN, OLLAMA_API_KEY, TAVILY_API_KEY for the named services.
2. The OS keychain: keyring.get_password("baby-onit", name). This is the preferred at-rest store, encrypted by the OS and never a file on disk.
3. A 0600 file, ~/.baby-onit/secrets.yaml. This is a fallback for machines without a keychain; mode 0600 means only the owner can read it.

Because the resolver is a single expression, env or keyring(name) or file_secrets().get(name), a secret can be supplied in exactly one place and the rest of the baby-onit code never knows which one it came from. The setup wizard (S9) writes keys into the keychain or the file, so after a one-time baby-onit setup, baby-onit chat needs zero flags.

Teaching point: this is the same "secrets are a runtime concern, not a config concern" split that onit makes, just with a smaller tier list.
