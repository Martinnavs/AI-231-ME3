"""The kind of question asked: can a looked-up result be spoken as it is?

A lookup tool (weather, web_answer) returns a sentence that is spoken without asking the model
again (R5). That is right for "what is the weather in Davao" and wrong for "should I bring an
umbrella": the user wants a decision made from the result, and repeating the forecast is not one.
The form of the question tells them apart, whatever its topic.
"""

from __future__ import annotations

import re

ASKS_FOR_A_DECISION = re.compile(
    r"\b(?:should|shall) (?:i|we)\b"
    r"|\b(?:do|will|would|does) (?:i|we) (?:need|have to|want)\b"
    r"|\b(?:is|would|will) it (?:be )?(?:ok|okay|safe|worth|fine|wise|a good idea|better)\b"
    r"|\bworth (?:it|going|bringing|taking)\b"
    r"|\b(?:better|best) (?:to|if)\b"
    r"|\bwhat (?:should|shall) (?:i|we)\b",
    re.IGNORECASE,
)

# the question of the request being served: set by the agent server, one request at a time
current = ""


def needs_reasoning(question: str) -> bool:
    """True when the question asks for a decision or advice drawn from a result, not the result."""
    return ASKS_FOR_A_DECISION.search(question.replace("’", "'")) is not None
