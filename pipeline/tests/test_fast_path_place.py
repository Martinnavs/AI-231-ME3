"""Fast path matching for place and barangay questions."""

from __future__ import annotations

import pytest

from voice_pipeline import fast_path


@pytest.mark.parametrize(
    "utterance",
    [
        "Can you tell me where I am right now?",
        "Can you check the location of where I am right now?",
        "What's my current location?",
        "Can you then tell me where I am right now?",
        "Where am I?",
        "tell me where I am",
        "what is my location",
        "where am I located",
        "what's our current position",
        "could you check my location please",
        "What city am I in",
    ],
)
def test_place_phrases_match(utterance: str) -> None:
    """Place questions should match fast path with what='place'."""
    found = fast_path.match(utterance)
    assert found is not None, f"'{utterance}' should match"
    assert found.tool == "clock"
    assert found.args["what"] == "place"


@pytest.mark.parametrize(
    "utterance",
    [
        "which barangay am I in right now",
        "What barangay are we in",
    ],
)
def test_barangay_phrases_match(utterance: str) -> None:
    """Barangay questions should match fast path with what='barangay'."""
    found = fast_path.match(utterance)
    assert found is not None, f"'{utterance}' should match"
    assert found.tool == "clock"
    assert found.args["what"] == "barangay"


@pytest.mark.parametrize(
    "utterance",
    [
        "where are we in the build",
        "where am I in the timeline",
        "where is the nearest jollibee",
        "what is the location of the nearest pharmacy",
        "where are we on the task",
        "where is my package",
        "where do i work",
    ],
)
def test_non_place_location_questions_do_not_match_place_or_barangay(utterance: str) -> None:
    """Location-like questions that are not about current place should not match place/barangay."""
    found = fast_path.match(utterance)
    # Either returns None or returns something other than place/barangay
    if found is not None:
        what = found.args.get("what")
        assert what not in ("place", "barangay"), (
            f"'{utterance}' should not match as place/barangay, got what='{what}'"
        )
