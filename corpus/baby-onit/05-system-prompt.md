# baby-onit: the system prompt is the constitution (S4)

In baby-onit, SYSTEM_PROMPT is a single string with two format holes, {data_path} and {today}, filled by build_system_prompt. It is the one place where the harness tells the model how to behave, and it does four jobs:

1. Identity: "You are baby-onit, a helpful AI agent that completes tasks by calling tools."
2. Working directory: every file path the model writes starts at data_path. This is the jail, stated in words.
3. Tool routing: a short decision table. Files in the working directory go to read_file, grep, or search_document; public facts and news go to web search; anything that can change (prices, versions, dates) needs a tool result, never memory; prefer the primary source.
4. Credentials, pre-wired: the harness authenticates git via GIT_ASKPASS and puts HF_TOKEN in the bash environment, so the model is told never to hunt for a token or put one in a URL.

That last point is a deliberate safety posture. The model is told the credentials exist and are handled, so it has no reason to try to read or print them, and the harness is the only component that actually holds them.

Teaching point: in a real agent, the system prompt is policy. Changing one line in baby-onit changes what the agent is allowed to do. onit keeps this policy in a separate module; baby-onit keeps it as a constant because there is exactly one policy.
