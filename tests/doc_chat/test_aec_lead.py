"""The canceller feeds no reference ahead by default (.scratch/echo-next/PLAN.md, E1)."""

from doc_chat.aec import FRAME_BYTES, EchoCanceller


def test_no_lead_by_default() -> None:
    assert EchoCanceller()._lead == 0


def test_a_reply_start_renders_one_frame_per_microphone_frame() -> None:
    canceller = EchoCanceller()
    rendered: list[bytes] = []
    canceller._render = rendered.append  # type: ignore[method-assign]
    canceller.reference(bytes(FRAME_BYTES * 10))
    canceller.process(bytes(FRAME_BYTES * 3))
    assert len(rendered) == 3 and len(canceller._frames) == 7


def test_an_explicit_lead_is_still_fed_ahead() -> None:
    canceller = EchoCanceller(lead_ms=50)
    rendered: list[bytes] = []
    canceller._render = rendered.append  # type: ignore[method-assign]
    canceller.reference(bytes(FRAME_BYTES * 10))
    canceller.process(bytes(FRAME_BYTES))
    assert len(rendered) == 6  # 5 frames of lead, then the frame for this microphone frame
