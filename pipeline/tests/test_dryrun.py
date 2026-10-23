from voice_pipeline.dryrun import leaked, score_turn


def test_score_exact() -> None:
    calls = [{"intent": "ALARM", "slot": "6 am"}, {"intent": "LIGHT_ON", "slot": None}]
    expect = [{"intent": "LIGHT_ON", "slot": None}, {"intent": "ALARM", "slot": "6 AM"}]
    assert score_turn(calls, expect) == {"exact": True, "intent": True, "false_call": False}


def test_score_intent_only() -> None:
    calls = [{"intent": "ALARM", "slot": "6am"}]
    expect = [{"intent": "ALARM", "slot": "6 AM"}]
    assert score_turn(calls, expect) == {"exact": False, "intent": True, "false_call": False}


def test_score_false_call() -> None:
    calls = [{"intent": "WEATHER", "slot": None}]
    assert score_turn(calls, []) == {"exact": False, "intent": False, "false_call": True}
    assert score_turn([], []) == {"exact": True, "intent": True, "false_call": False}


def test_leaked() -> None:
    assert leaked('<function=smart_home>{"intent": "LIGHT_ON"}</function>')
    assert leaked('{"name": "smart_home", "parameters": {}}')
    assert leaked('function=smart_home>{"intent": "STOP"}</function>')
    assert leaked("<LIGHT_OFF></LIGHT_OFF>")
    assert not leaked("Sure, the lights are on now.")
