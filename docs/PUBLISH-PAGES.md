# Publishing an app to GitHub Pages (ticket 04)

"Publish the calculator" puts a finished app (a folder under `~/agent_sandbox/workspace/apps/`) on a public GitHub Pages site, after a spoken yes. Built 2026-11-11; **tested against a local bare git repository and the real apps folder, not yet against GitHub** (needs your repository and token, below).

## Turn it on (owner steps, about 5 minutes)

1. **A dedicated demo repository**, public (free Pages needs it), for example `<you>/voice-agent-demos`. Not your `<you>.github.io`: the agent only writes inside `<app>/` folders, but a separate repository keeps it away from your real site.
2. **A fine-grained personal access token** for that one repository only: Contents read/write and Pages read/write. Save it to `~/.secrets/github/pat` (one line, `chmod 600`). An assistant must not read it; only the publish server does, at publish time.
3. **Set `repo`** in `config/publish.toml` (`https://github.com/<you>/voice-agent-demos`). Until `repo` is set nothing is offered and nothing is added to the prompt.
4. Restart the agent server (`make restart-agent`). Say "publish the calculator".

## What happens

| Step | By | What |
|---|---|---|
| 1 | model | calls `publish_pages(app)` for an app `open_app` lists |
| 2 | code (`agent_tools.py`) | the first call is **always a dry run**; the model's own `dry_run` value is overridden |
| 3 | pages server | copies the folder with no-follow reads into a private staging directory, checks the staged copy, and says "Ready to publish calculator: 1 files, 5 KB, public at https://you.github.io/repo/calculator/." |
| 4 | agent | speaks that plus "Should I go ahead and publish it?" |
| 5 | code | a call for the **same app**, within 90 s, while the user's own words are a yes, publishes; anything else stays a dry run. One yes publishes once. |
| 6 | pages server | fetches the branch, replaces `<app>/`, adds `.nojekyll`, commits as "Voice Agent", pushes **without force**, and switches Pages on through the API if it is off |

## Checks before anything leaves the machine

- The folder must be inside the apps folder; a symlink anywhere in it (a delegate can plant one) is refused and never read.
- `index.html` at the top; only html, css, js, json, txt, md, svg, png, jpg, gif, webp, ico and woff files; dotfiles left out; at most 100 files, 1 MB each, 5 MB in all.
- No secrets: known key shapes (GitHub, Google, `sk-`, Tavily, AWS, Slack, private keys, `api_key = "..."`) and the **exact contents of your `~/.secrets` files**, whatever shape they have.
- The repository must be a `https://github.com/<owner>/<repo>` address; the branch name is validated; the token reaches git through an askpass script and the environment, never a command line, and is redacted from any message.

## Not covered

- The delegate cannot publish: it has no token, no `gh`, no ssh, and the tool is only offered to the front agent.
- A "yes" is checked by pattern on the user's words within 90 s of the question. A different yes in that window, followed by a model call for the same app, would publish; the page is still the one just described to the user.
- A published page is public and stays in git history; unpublishing means deleting the folder in the repository.
- Not built: publishing an arbitrary folder, a custom domain, deleting a published site by voice, the chain phase `awaiting_publish_confirm` (the confirmation lives in the tool gate instead).

## Tests

`tests/mcp_servers/test_pages.py` (37; a real local bare repository: push, identity, no empty commits, existing files kept, stale files removed, symlinks, type and size limits, secret shapes and exact values, bad names and branches, token never on a command line, no `--force`, Pages API) and `pipeline/tests/test_publish_gate.py` (9; dry run first, one yes publishes once, wrong app, expiry, refusals).
