import pytest

from voice_pipeline.question import needs_reasoning


@pytest.mark.parametrize(
    "said",
    [
        "Should I bring an umbrella?",
        "What's the weather in Davao tomorrow, and should I bring an umbrella?",
        "Given this information, should I bring an umbrella?",
        "Do I need a jacket today?",
        "Will I need to wear a coat?",
        "Is it safe to go out?",
        "Is it worth going to Baguio this weekend?",
        "Would it be better to leave early?",
        "What should we wear tomorrow?",
        "Should we cancel the trip",
    ],
)
def test_questions_that_want_a_decision(said: str) -> None:
    assert needs_reasoning(said)


@pytest.mark.parametrize(
    "said",
    [
        "What's the weather in Baguio?",
        "Who won the 2022 World Cup?",
        "What is the chance of rain tomorrow?",
        "Will it rain tomorrow?",
        "How tall is Mount Everest?",
        "Tell me about the history of the Philippines.",
        "I should have called her.",
    ],
)
def test_plain_lookups_are_spoken_as_they_are(said: str) -> None:
    assert not needs_reasoning(said)
