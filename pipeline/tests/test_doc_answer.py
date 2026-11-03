import pytest

from voice_pipeline import doc_answer as da

PASSAGES = [
    "The kid walked south with the man. They reached Nashville in 1980 and Billy Ray sold the "
    "truck for 450 dollars.",
    "Suttree lived on a houseboat on the river.",
]
JOINED = "\n\n".join(PASSAGES)


def test_parse_answer_and_quote():
    reply = 'ANSWER: Billy Ray sold the truck.\nQUOTE: "sold the truck for 450 dollars"'
    assert da.parse(reply) == ("Billy Ray sold the truck.", "sold the truck for 450 dollars")


@pytest.mark.parametrize("reply", ["NONE", "none", "", "I do not know.", "ANSWER: only an answer"])
def test_parse_rejects_none_and_junk(reply):
    assert da.parse(reply) is None


@pytest.mark.parametrize(
    "quote",
    [
        "sold the truck for 450 dollars",
        "SOLD THE TRUCK FOR 450 DOLLARS",
        "**sold the truck** for 450 dollars",
        "“sold the truck for 450 dollars”",
        "Billy Ray sold the truck.",
        "lived on a houseboat",
    ],
)
def test_quote_passes(quote):
    assert da.quote_ok(quote, JOINED)


@pytest.mark.parametrize(
    "quote",
    [
        "sold the car for 450 dollars",  # a changed word
        "sold the truck for 540 dollars",  # a changed digit
        "walked south with the houseboat",  # stitched from two places
        "Nashville in 1980 ... 450 dollars",
        "Nashville in 1980 … 450 dollars",
        "reached Nash",  # part of a word
        "",
    ],
)
def test_quote_fails(quote):
    assert not da.quote_ok(quote, JOINED)


def test_curly_apostrophe_matches():
    assert da.quote_ok("the kid’s hat", "He took the kid's hat and left.")


def test_names_and_numbers_pass():
    assert da.names_numbers_ok("Billy Ray sold the truck for 450 dollars.", JOINED)
    assert da.names_numbers_ok("They reached Nashville in 1980.", JOINED)
    assert da.names_numbers_ok("Suttree's houseboat was on the river.", JOINED)


@pytest.mark.parametrize(
    "answer",
    [
        "Billy Ray sold the truck for 500 dollars.",  # a number not in the passages
        "They reached Memphis in 1980.",  # a name not in the passages
        "He met Cornelius Suttree.",
    ],
)
def test_names_and_numbers_fail(answer):
    assert not da.names_numbers_ok(answer, JOINED)


def test_first_word_and_common_words_are_ignored():
    assert da.names_numbers_ok("Yesterday the man walked south.", JOINED)  # first word
    assert da.names_numbers_ok("The kid walked. He walked. They walked. It ended.", JOINED)


def test_passages_of_list_and_mcp_text():
    items = [{"source": "a.txt", "text": "one"}, {"source": "b.txt", "text": "two"}]
    assert da.passages_of(items) == ["one", "two"]
    text = '{"source": "a.txt", "text": "one"}{"source": "b.txt", "text": "two"}'
    assert da.passages_of(text) == ["one", "two"]
    assert da.passages_of('[{"source": "a", "text": "one"}]') == ["one"]
    assert da.passages_of("Sorry, could not call the mcp tool") == []
    assert da.passages_of(None) == []


def test_user_content_layout():
    assert da.user_content(["a", "b"], "q?") == "Passages:\n[1] a\n\n[2] b\n\nQuestion: q?"


def fake(reply=None, boom=False):
    seen = []

    async def request(system, content):
        seen.append((system, content))
        if boom:
            raise RuntimeError("down")
        return reply

    return request, seen


async def test_answer_accepted():
    request, seen = fake("ANSWER: Billy Ray sold it.\nQUOTE: Billy Ray sold the truck")
    got = await da.answer(request, PASSAGES, "who sold the truck?")
    assert (got.status, got.text) == ("accepted", "Billy Ray sold it.")
    assert seen[0][0] == da.SYSTEM and seen[0][1].endswith("Question: who sold the truck?")


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        ("NONE", "none"),
        ("ANSWER: It was Tom who sold it.\nQUOTE: Billy Ray sold the truck", "check_failed"),
        ("ANSWER: Billy Ray sold it.\nQUOTE: Billy Ray ... the truck", "check_failed"),
        ("blah", "error"),
    ],
)
async def test_answer_statuses(reply, status):
    request, _ = fake(reply)
    got = await da.answer(request, PASSAGES, "q?")
    assert (got.status, got.text) == (status, None)


async def test_request_error_and_no_passages():
    request, seen = fake(boom=True)
    assert (await da.answer(request, PASSAGES, "q?")).status == "error"
    assert (await da.answer(request, [], "q?")).status == "none"
    assert len(seen) == 1  # no passages, no request


async def test_check_failed_keeps_the_rejected_answer():
    request, _ = fake("ANSWER: It was Tom who sold it.\nQUOTE: Billy Ray sold the truck")
    got = await da.answer(request, PASSAGES, "q?")
    assert got.rejected == {
        "answer": "It was Tom who sold it.",
        "quote": "Billy Ray sold the truck",
        "failed": "names_numbers",
    }
