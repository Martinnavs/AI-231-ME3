# baby-onit: setup (S9)

The setup command in baby-onit is the on-ramp: a wizard that writes config.yaml and stores secrets so that chat needs zero flags afterwards. It does four things:

1. It lets you pick an endpoint (a preset, the current one, or a custom URL) and validates that it is an http:// or https:// URL.
2. It asks for the API key via getpass, so it is never echoed, and stores it with set_secret("endpoint_key:<host>", key), into the keychain or the 0600 file.
3. It auto-detects a model via models.list(), or lets you type one, and remembers it per endpoint in models.yaml.
4. It offers to delete an endpoint's stored config (forget_endpoint) when you switch.

The baby-onit command line itself is a small argparse tree: setup, chat, run "task", doctor, plus flags like --host, --model, --data-path, --no-think, --max-iterations, --max-context-tokens, and --config.

Teaching point: setup is the only place that writes secrets. Everything else only reads them through get_secret. That single-writer rule is what keeps the three-tier resolver honest.
