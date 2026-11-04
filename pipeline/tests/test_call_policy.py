import pytest

from voice_pipeline import call_policy
from voice_pipeline.call_policy import (
    decide,
    device_question,
    is_duration,
    is_time_of_day,
    plan,
)
from voice_pipeline.question import is_question
from voice_pipeline.router import spec

ALL = {"smart_home", "weather", "wiki_summary", "web_answer", "get_volume", "set_volume"}


def calls(text: str) -> list:
    return spec.parse_output(text)


def reason(text: str, user: str = "", available: set[str] = ALL):
    return decide(calls(text), user, available).reason


def test_usable_calls_have_no_reason():
    assert reason("LIGHT_ON", "turn on the light") is None
    assert reason("TIMER|20 minutes", "timer for twenty minutes") is None
    assert reason("weather|Cebu", "weather in cebu") is None
    assert reason("none|You're welcome.", "thanks") is None


def test_moved_rules_unchanged():
    assert decide([], "x", ALL).reason == "empty"
    assert reason("escalate|other", "x") == "escalate"
    assert reason("none|a\nLIGHT_ON", "x") == "unsupported: none"
    assert reason("none|Hi.", "one two three four five six") == "chat too long"
    assert reason("none|" + "word " * 11, "hello") == "chat reply too long"
    assert reason("none|Good morning to you.", "good morning to you") == "echo"
    assert reason("LIGHT_ON", "x", {"weather"}) == "unsupported: LIGHT_ON"
    assert reason("LIST_REMINDERS", "x") == "unsupported: LIST_REMINDERS"
    assert reason("get_current_time|Nowhere/City", "x") == "unsupported: time zone"
    assert reason("weather|Cebu", "x", {"smart_home"}) == "unsupported: weather"
    assert reason("weather|Cebu", "should I bring an umbrella") == "needs reasoning"


def test_web_answer_is_usable():
    assert (
        reason("web_answer|Who is the president of Peru?", "who is the president of peru") is None
    )
    assert reason("web_answer|x", "x", {"smart_home"}) == "unsupported: web_answer"
    assert reason("web_answer|x", "should I go") == "needs reasoning"


def test_swap_wiki_to_web_answer_with_whole_question():
    d = decide(calls("wiki_summary|Peru"), "tell me about Peru please", ALL)
    assert [(p.tool, p.args) for p in d.planned] == [
        ("web_answer", {"query": "tell me about Peru please"})
    ]


def test_swap_uses_topic_when_not_the_only_call_or_no_question():
    d = decide(calls("wiki_summary|Peru\nLIGHT_ON"), "peru and the light", ALL)
    assert d.planned[0].args == {"query": "Peru"}
    assert d.planned[1].tool == "smart_home" and d.planned[1].home
    assert plan(calls("wiki_summary|Peru"), " ", ALL)[0].args == {"query": "Peru"}


def test_no_swap_without_web_answer():
    d = decide(calls("wiki_summary|Peru"), "peru", {"wiki_summary"})
    assert d.planned[0].tool == "wiki_summary"


def test_web_answer_keeps_the_routers_own_question():
    d = decide(calls("web_answer|Who won the 2022 final?"), "and who won that one?", ALL)
    assert d.planned[0].args == {"query": "Who won the 2022 final?"}


def test_web_answer_falls_back_to_the_user_words_when_the_argument_is_empty():
    p = plan(calls("web_answer|"), " who runs the harbour ", ALL)
    assert p[0].args == {"query": "who runs the harbour"}
    p = plan(calls("web_answer|x\nLIGHT_ON"), "who runs the harbour", ALL)
    assert p[0].args == {"query": "x"}


@pytest.mark.parametrize(
    ("line", "user"),
    [
        ("web_answer|Harbour tides", "that is so interesting"),
        ("wiki_summary|Peru", "I visited it last year"),
    ],
)
def test_a_statement_is_not_looked_up(line, user):
    assert reason(line, user) == "not a question"


@pytest.mark.parametrize(
    ("line", "user"),
    [
        ("web_answer|Orchard Keeper first novel", "Cormac's first novel"),  # noun phrase
        ("web_answer|capital of Chad", "capital of chad"),
        ("web_answer|anything", "is it open?"),
        ("wiki_summary|Peru", "tell me about Peru"),
    ],
)
def test_noun_phrases_and_questions_are_looked_up(line, user):
    assert reason(line, user) is None


WITH_DOCS = ALL | {"search_docs"}


def test_none_reply_to_a_question_goes_to_gemini_when_documents_are_loaded():
    d = decide(calls("none|It is a book."), "what is the road?", WITH_DOCS)
    assert d.reason == "documents loaded: question"
    assert d.planned == []


def test_documents_loaded_leaves_other_turns():
    assert reason("none|You're welcome.", "thanks", WITH_DOCS) is None
    assert reason("none|Sure.", "I like mccarthy", WITH_DOCS) is None  # a statement
    assert reason("LIGHT_ON", "is the light on?", WITH_DOCS) is None
    assert reason("web_answer|x", "who is the senate president?", WITH_DOCS) is None


def test_none_reply_to_a_question_is_unchanged_without_documents():
    assert reason("none|It is a book.", "what is the road?") is None


@pytest.mark.parametrize(
    "text",
    [
        "20 minutes",
        "an hour",
        "half an hour",
        "1 hour 30 minutes",
        "ten seconds",
        "45 seconds",
        "1.5 hours",
        "an hour and a half",
        "two minutes",
        "90 seconds",
        "5min",
    ],
)
def test_valid_durations(text):
    assert is_duration(text)


@pytest.mark.parametrize("text", ["12 times 9", "now", "", "minutes", "20", "twenty", "5 apples"])
def test_invalid_durations(text):
    assert not is_duration(text)


def test_timer_slot_checked():
    assert reason("TIMER|10 minutes", "ten minutes") is None
    assert reason("TIMER|12 times 9") == "unsupported: TIMER value"
    assert reason("TIMER|now") == "unsupported: TIMER value"


def test_alarm_slot_checked():
    assert reason("ALARM|6:30 AM|tomorrow", "wake me at six thirty") is None
    assert reason("ALARM|banana") == "unsupported: ALARM value"
    assert is_time_of_day("seven thirty pm")
    assert is_time_of_day("noon")
    assert is_time_of_day("half past six")
    assert not is_time_of_day("25:00")
    assert not is_time_of_day("13 pm")


def test_is_question():
    assert is_question("tell me more?")
    assert is_question("who wrote it")
    assert is_question("Did he write it")
    assert not is_question("I like the road")
    assert not is_question("turn on the light")


DEVICE = ALL | {"device_status"}


def test_device_status_is_refused_while_the_flag_is_off():
    assert call_policy.ROUTER_DEVICE_STATUS is False
    assert reason("device_status", "what's the pi temperature", DEVICE) == (
        "unsupported: device_status"
    )


def test_device_status_when_enabled_needs_a_status_question(monkeypatch):
    monkeypatch.setattr(call_policy, "ROUTER_DEVICE_STATUS", True)
    assert reason("device_status", "what's the pi temperature", DEVICE) is None
    assert reason("device_status", "is the speaker connected", DEVICE) is None
    assert reason("device_status", "what's the pi temperature") == "unsupported: device_status"
    for user in ("ping the router and give the latency", "set the speaker to half", "how are you"):
        assert reason("device_status", user, DEVICE) == "unsupported: device_status"


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        ("how hot is the raspberry", True),
        ("how much memory does the device have", True),
        ("turn the speaker up", False),
        ("what is the temperature outside", False),
        ("is the pi network ok", False),
        ("speaker volume", False),
    ],
)
def test_device_question(user, expected):
    assert device_question(user) is expected


@pytest.mark.parametrize(
    ("line", "user"),
    [
        ("TIMER|10 minutes", "ten minutes"),
        ("TIMER|an hour", "timer for an hour"),
        ("TIMER|half an hour", "set a half hour timer"),
        ("TIMER|1 hour 30 minutes", "one hour thirty minutes"),
        ("ALARM|6:30 AM", "wake me at six thirty"),
        ("ALARM|6:30 AM", "alarm for 6:30am"),
        ("TEMPERATURE|28 degrees", "make it twenty eight degrees"),
        ("TEMPERATURE|28 degrees", "set it to 28"),
        ("BRIGHTNESS|40 percent", "dim to forty percent"),
        ("set_volume|35", "volume to thirty five"),
        ("set_volume|35", "volume to 35 percent"),
    ],
)
def test_slot_numbers_in_the_users_words_pass(line, user):
    assert reason(line, user) is None


@pytest.mark.parametrize(
    ("line", "user", "intent"),
    [
        ("TIMER|6 hours", "set a timer", "TIMER"),
        ("TIMER|an hour", "set a timer", "TIMER"),
        ("TEMPERATURE|32 degrees", "call Marta", "TEMPERATURE"),
        ("BRIGHTNESS|40 percent", "make it brighter", "BRIGHTNESS"),
        ("ALARM|7 AM", "wake me at six", "ALARM"),
        ("set_volume|60", "turn it up", "set_volume"),
    ],
)
def test_slot_numbers_from_the_history_are_refused(line, user, intent):
    assert reason(line, user) == f"slot not in the user's words: {intent}"


@pytest.mark.parametrize(
    ("user", "reply", "parrot"),
    [
        ("Wind storm.", "Wind storm.", True),  # a stop word the recogniser garbled
        ("Oh", "Oh!", True),
        ("Good morning.", "Good morning!", False),  # a pleasantry answered with itself
        ("Hello.", "Hello!", False),
        ("Thank you.", "Thank you!", False),
        ("Hello, good morning.", "Good morning!", False),
        ("Bucket on Mu.", "Bucket on Mu.", True),
        ("thanks", "You're welcome.", False),
    ],
)
def test_parroted_chat_reply(user, reply, parrot):
    got = reason(f"none|{reply}", user)
    assert (got == "echo") is parrot
