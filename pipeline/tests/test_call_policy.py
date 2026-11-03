import pytest

from voice_pipeline.call_policy import decide, is_duration, is_time_of_day
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
    assert decide(calls("wiki_summary|Peru"), " ", ALL).planned[0].args == {"query": "Peru"}


def test_no_swap_without_web_answer():
    d = decide(calls("wiki_summary|Peru"), "peru", {"wiki_summary"})
    assert d.planned[0].tool == "wiki_summary"


def test_web_answer_asks_whole_question_when_alone():
    d = decide(calls("web_answer|senate president"), "who is the senate president", ALL)
    assert d.planned[0].args == {"query": "who is the senate president"}


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
    assert reason("TIMER|10 minutes") is None
    assert reason("TIMER|12 times 9") == "unsupported: TIMER value"
    assert reason("TIMER|now") == "unsupported: TIMER value"


def test_alarm_slot_checked():
    assert reason("ALARM|6:30 AM|tomorrow") is None
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
