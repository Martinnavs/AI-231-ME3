"""Gemini LLM backend for doc_chat, through Google's OpenAI-compatible endpoint.

Same shape as the Qwen backend: the key is loaded into memory and handed to the client, and every
message printed here is a fixed string or an exception class name.

  key    ~/.secrets/google-ai-studio/api_key.txt or api-key.txt (whitespace ignored), then env
         GEMINI_API_KEY; override the directory with env GOOGLE_AI_SECRETS_DIR
  model  env GEMINI_MODEL, default gemini-3.1-flash-lite
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from doc_chat.qwen import describe_failure

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
_NOT_CONFIGURED = "gemini backend not configured"


def _read_key() -> str | None:
    directory = Path(
        os.environ.get("GOOGLE_AI_SECRETS_DIR") or Path.home() / ".secrets" / "google-ai-studio"
    )
    for name in ("api_key.txt", "api-key.txt"):
        try:
            value = "".join((directory / name).read_text(encoding="utf-8").split())
        except OSError:
            continue
        if value:
            return value
    return None


def build_gemini_chat_model(
    model: str | None = None,
    timeout: float = 30.0,
    max_tokens: int = 160,
    temperature: float = 0.2,
) -> BaseChatModel:
    api_key = _read_key() or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(_NOT_CONFIGURED, file=sys.stderr)
        sys.exit(2)
    return make_chat_model(
        api_key,
        model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def make_chat_model(
    api_key: str,
    model_id: str,
    *,
    base_url: str = BASE_URL,
    timeout: float = 30.0,
    max_tokens: int = 160,
    temperature: float = 0.2,
) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    return ChatOpenAI(
        base_url=base_url,
        api_key=SecretStr(api_key),
        model=model_id,
        temperature=temperature,
        max_completion_tokens=max_tokens,
        timeout=timeout,
        max_retries=0,
    )


def main() -> int:
    """`python -m doc_chat.gemini`: one tiny request; prints only OK/failure, never a value."""
    llm = build_gemini_chat_model()
    t0 = time.perf_counter()
    try:
        llm.invoke("Reply with the single word: ready")
    except Exception as exc:
        print(f"gemini check failed: {describe_failure(exc)}", file=sys.stderr)
        return 1
    print(f"gemini check ok ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
