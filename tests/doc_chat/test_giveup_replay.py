"""Replay of the 11 logged give-ups (stack-1105-0829, spoken-v3-1104-1133) through the new rules.

Each row is the agent-side timeline read from out/sessions/<session>/turns.jsonl (filler_log) and
pipeline/out/agent_server/*.jsonl (calls, first_call_s, first_text_s, end_s), taking the agent's
clock as the client's (the router path starts the agent late, so these are approximate). The
timeline is fed to `TurnFillers` with the real config/fillers.json.
"""

from doc_chat.fillers import Fillers, TurnFillers

# (session, turn, tool, tool_s, answer_text_s, finished_s)
#   tool_s: first sign of progress (tool event); answer_text_s: a non-`say` text event;
#   finished_s: when the agent's answer was complete (None: the client dropped it first).
LOGGED = [
    ("stack-1105-0829", 31, "web_answer", 1.38, None, None),
    ("stack-1105-0829", 57, "delegate", 2.01, None, None),
    ("stack-1105-0829", 59, "delegate", 2.67, 3.27, 8.10),
    ("stack-1105-0829", 60, "delegate", 12.25, None, None),
    ("stack-1105-0829", 61, "delegate", 4.48, None, None),
    ("stack-1105-0829", 62, "delegate_cancel", 2.04, 2.64, 3.86),
    ("stack-1105-0829", 64, "device_status", 1.85, None, 17.80),
    ("stack-1105-0829", 69, "device_status", 0.74, 1.34, 1.88),
    ("spoken-v3-1104-1133", 28, "web_answer", 1.23, None, None),
    ("spoken-v3-1104-1133", 29, "web_answer", 8.71, None, None),
    ("spoken-v3-1104-1133", 39, "delegate", 2.01, 2.61, 7.49),
]
OUTCOME = {  # who is still given up with the new rules
    ("stack-1105-0829", 60): 8.0,  # the model's first tool call came at 12.25 s: nothing before 8 s
    ("spoken-v3-1104-1133", 29): 8.0,  # the first tool call came at 8.71 s
}


def gave_up_at(
    tool: str, tool_s: float, text_s: float | None, horizon: float = 40.0
) -> float | None:
    turn = TurnFillers(Fillers.load())
    t = 0.0
    told_tool = told_text = False
    while t < horizon:
        if not told_tool and t >= tool_s:
            told_tool = True
            turn.tool(tool, said=True)
        if text_s is not None and not told_text and t >= text_s:
            told_text = True
            turn.text_arrived()
        if turn.due(t) == "giveup":
            return t
        t = round(t + 0.05, 2)
    return None


def test_the_replay_over_the_logged_give_ups() -> None:
    dropped = {}
    for session, number, tool, tool_s, text_s, finished_s in LOGGED:
        at = gave_up_at(tool, tool_s, text_s)
        if at is not None and at <= 8.05:  # the old cap: nothing came back
            dropped[(session, number)] = at
        elif at is not None and finished_s is not None:
            assert at > finished_s, number  # a later cap never cuts off a known finish
    assert dropped.keys() == OUTCOME.keys()
    assert all(abs(dropped[k] - OUTCOME[k]) < 0.1 for k in OUTCOME)
    # 9 of 11 are not dropped at 8 s any more; 5 of them are known to have finished in time:
    # 59, 62, 64 (late, 17.8 s), 69, 39. 31, 57, 61, 28 were cut off while still running.
    assert len(LOGGED) - len(dropped) == 9


def test_every_logged_turn_with_a_tool_event_before_8_s_survives_8_s() -> None:
    for _session, number, tool, tool_s, text_s, _finished in LOGGED:
        at = gave_up_at(tool, tool_s, text_s)
        if tool_s < 8.0:
            assert at is None or at >= 12.0, number  # 12 s is the delegate cap, 20 s the rest
