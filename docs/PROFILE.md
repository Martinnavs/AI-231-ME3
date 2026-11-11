# The owner profile ("make me a page about me")

A file you write once, so the agent knows who you are without asking or searching the web. **Optional**: with no profile, "make me a page about me" still works the old way (the agent asks your name and a detail, searches public sources, reads back three facts and waits for a yes). Built and live-checked 2026-11-11.

## Set it up

1. Copy the template: `cp config/profile.example.md config/profile.md`. `config/profile.md` is **git-ignored** (it is yours; never `git add` it). The template has headings and empty fields; add or delete fields freely.
2. Write only what you are happy to see on a public page. The delegate model (Haiku, through OpenRouter) reads it, and a built page may show any of it. No secrets, passwords, home address or private phone number.
3. Restart the agent server (`make restart-agent`, or `make up`). The server copies the file when it starts; editing it later changes nothing until the next restart.
4. Check: `make doctor-tools` (below).

It counts as filled in once **at least one field has a value** (headings, comments and empty `- Field:` lines do not count). An unfilled template installs nothing and adds nothing to the prompt.

## What happens

| Step | What |
|---|---|
| server start | a filled `config/profile.md` is copied to `<delegate workspace>/scratch/PROFILE.md` (`/sandbox/scratch/PROFILE.md` inside the delegate); an emptied or removed one deletes that copy |
| prompt | one rule is appended to the base prompt (only then): for "me", "about me", "my ..." builds skip the name question, the web search and the read-back, say one sentence, and delegate with a task that names `/sandbox/scratch/PROFILE.md` and says to use only what it says |
| delegate | builds the page from that file alone: no `SOURCES.json` is required and the profile text counts as the source for the "unsourced figure" lint; if it also searched the web, the usual source checks apply |

Code: `pipeline/src/voice_pipeline/scratchpad.py` (`profile_is_filled`, `install_profile`, `PROFILE_RULE`), `agent_server.py` (the install at start), `src/voice_agent/agent/subagent.py` (`from_profile`).

## The doctor (optional check)

`make doctor-tools` (or `.venv/bin/python scripts/delegate_doctor.py --offline`) reports two **optional** lines; neither can make the doctor fail, and the profile's contents are never printed:

| Line | OK means | MISSING means |
|---|---|---|
| `owner profile` | `config/profile.md` exists and is filled in (size shown) | no file, or only the template (it says which) |
| `profile in sandbox` | the delegate itself can read `/sandbox/scratch/PROFILE.md` and it is the same size as your file | not installed yet, or out of date: restart the agent server |

When both are OK the last line adds "build a page about you from your profile" to what the voice agent can do. The check runs inside the real sandbox, so it is what the delegate's shell sees.

## On the Raspberry Pi

With `HARNESS=pi` the agent server and the delegate run on the Pi, whose checkout has no `config/profile.md` (git-ignored files are not in git). Copy it once from the laptop to the Pi's checkout at `config/profile.md` (the Pi is the owner's call; nothing here touches it), restart the Pi's agent server, and run `make doctor-tools` there: the same two lines say whether it took. In the default mode (agent on the laptop) the Pi needs nothing.

## Live check (2026-11-11, one trial, $0.0057)

Isolated copy of the stack, workspace inside the repository's `.scratch/`, profile with 16 filled fields. "Make me a personal page about me." The agent skipped the name question and the search, said "I will build your personal page using the details from your profile." (3.7 s), and the delegate finished in about 35 s (Haiku, $0.0057, task names `PROFILE.md`). Checked on the page: all 16 profile values present; all 6 links come from the profile; no external scripts, styles or fonts; no number the profile lacks; the sub-agent lint found nothing; none of the 5 text lines is weakly supported by the profile; the GitHub Pages validator (`docs/PUBLISH-PAGES.md`) found no problem. One trial, so it shows the path works, not how often.

## Known limits

- Restart needed after every edit.
- The page's text is written by a model from your fields; it can paraphrase but, in the one trial, added nothing.
- The delegate's spoken summary for any page ends with "say open it to see it" (an app-oriented sentence).
- Only "me/about me/my" requests use it; the profile is not answered from by voice ("what's my school?" is not wired).
