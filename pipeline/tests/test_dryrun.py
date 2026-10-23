from voice_pipeline.dryrun import leaked, score_turn

DOCS = {"tool": "search_docs", "args": {"query": "q"}}


def test_score_exact_ignores_order_and_slot_case() -> None:
    calls = [{"intent": "ALARM", "slot": "6:30 am"}, {"intent": "LIGHT_ON", "slot": None}]
    expect = [{"intent": "LIGHT_ON", "slot": None}, {"intent": "ALARM", "slot": "6:30 AM"}]
    assert score_turn(calls, expect) == {"exact": True, "intent": True, "false_call": False}


def test_score_counts_repeated_searches_once() -> None:
    calls = [{"intent": "LIGHT_OFF", "slot": None}, DOCS, DOCS]
    expect = [{"intent": "LIGHT_OFF", "slot": None}, {"tool": "search_docs"}]
    assert score_turn(calls, expect)["exact"] is True


def test_score_free_text_slot_matches_by_containment() -> None:
    calls = [{"intent": "CREATE_REMINDER", "slot": "pick up the laundry at five"}]
    expect = [{"intent": "CREATE_REMINDER", "slot": "pick up the laundry"}]
    assert score_turn(calls, expect)["exact"] is True
    wrong = [{"intent": "TIMER", "slot": "45 minutes and more"}]
    assert score_turn(wrong, [{"intent": "TIMER", "slot": "45 minutes"}])["exact"] is False


def test_score_false_call() -> None:
    assert score_turn([DOCS], []) == {"exact": False, "intent": False, "false_call": True}
    assert score_turn([], []) == {"exact": True, "intent": True, "false_call": False}


def test_leaked() -> None:
    assert leaked('function=smart_home>{"intent": "STOP"}</function>')
    assert leaked("<LIGHT_OFF></LIGHT_OFF>")
    assert not leaked("Sure, the lights are on now.")
