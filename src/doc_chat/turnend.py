"""Does the transcript look like a finished request? (Turnsense, a small text model)

Used after the speech server ends a turn: if the words end like an unfinished sentence ("play
something by"), the agent asks "anything to add?" instead of answering half a request.

Model: latishab/turnsense (Apache-2.0, SmolLM2-135M, int8 ONNX, ~176 MB). Download it into
``models/turn-detector/`` with ``python scripts/fetch_turn_model.py``. See docs/SMART-TURN.md for
how well it works (it misses a quarter to a half of unfinished sentences; that is why the fixed
pause stays).
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

DEFAULT_MODEL_DIR = Path("models/turn-detector")
MODEL_FILE = "model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"

# said when the transcript looks unfinished; short, neutral, and no question about the content
ADD_PROMPTS = (
    "Got it. Anything to add?",
    "Okay. Do you want to add anything?",
    "Mm-hm. Is there more?",
)

_FINISHED_PHRASES = (
    "that's all",
    "thats all",
    "that is all",
    "that's it",
    "thats it",
    "that is it",
    "nothing",
    "no thanks",
    "no thank you",
    "never mind",
    "nevermind",
)


def plain_text(text: str) -> str:
    """Lower case without punctuation (apostrophes and hyphens kept).

    A full stop on the end would read as 'finished' to the model, and Parakeet puts one on
    truncated audio too."""
    keep = "".join(c.lower() if c.isalnum() or c in "'- " else " " for c in text)
    return " ".join(keep.split())


def declines_to_add(text: str) -> bool:
    """The reply to 'anything to add?' is a no ('no', 'that's all', 'nothing')."""
    from doc_chat.dialogue import parse_yes_no

    words = plain_text(text)
    return parse_yes_no(text) == "no" or any(words.startswith(p) for p in _FINISHED_PHRASES)


def pick_prompt(rng: random.Random | None = None) -> str:
    return (rng or random).choice(ADD_PROMPTS)


class TextTurnModel:
    """P(the speaker has finished) from the words alone."""

    def __init__(self, model_dir: Path | str = DEFAULT_MODEL_DIR, threads: int = 1) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        directory = Path(model_dir)
        self._tok: Any = Tokenizer.from_file(str(directory / TOKENIZER_FILE))
        self._tok.no_padding()  # the shipped file pads to 256 tokens, which costs ~180 ms
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self._sess = ort.InferenceSession(
            str(directory / MODEL_FILE), opts, providers=["CPUExecutionProvider"]
        )

    def prob(self, text: str) -> float:
        import numpy as np

        ids = np.array([self._tok.encode(f"<|user|> {plain_text(text)}").ids], dtype=np.int64)
        out = self._sess.run(None, {"input_ids": ids, "attention_mask": np.ones_like(ids)})[0]
        return float(out[0][1])

    def looks_unfinished(self, text: str, threshold: float) -> bool:
        return bool(text.strip()) and self.prob(text) < threshold
