"""Qwen LLM backend for doc_chat (ticket 24).

Credentials never appear in output: the address and key are loaded into memory and passed to the
client, and every message printed here is a fixed string or an exception class name.

Sources, first match wins:
  key      ~/.secrets/onit/api_key.txt, then env ONIT_API_KEY
  address  ~/.secrets/onit/host_address.txt, then env ONIT_BASE_URL, then opencode's upd-ai baseURL
  model    env ONIT_MODEL, then the first model of opencode's upd-ai provider
Override the directory with env ONIT_SECRETS_DIR.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

_NOT_CONFIGURED = "qwen backend not configured"


def _read_secret_file(name: str) -> str | None:
    directory = Path(os.environ.get("ONIT_SECRETS_DIR") or Path.home() / ".secrets" / "onit")
    try:
        value = (directory / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def normalize_base_url(address: str) -> str:
    url = address.strip().rstrip("/")
    if "://" not in url:
        url = f"http://{url}"
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return url


def _opencode_provider() -> dict[str, object]:
    path = Path.home() / ".config" / "opencode" / "opencode.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        provider = data.get("provider", {}).get("upd-ai", {})
    except (OSError, ValueError, AttributeError):
        return {}
    return provider if isinstance(provider, dict) else {}


def _first_model_id(models: object) -> str | None:
    if isinstance(models, list) and models:
        first = models[0]
        if isinstance(first, str):
            return first
        model_id = first.get("id") if isinstance(first, dict) else None
        return str(model_id) if model_id else None
    if isinstance(models, dict) and models:
        return str(next(iter(models)))
    return None


def build_qwen_chat_model() -> BaseChatModel:
    api_key = _read_secret_file("api_key.txt") or os.environ.get("ONIT_API_KEY")
    provider = _opencode_provider()
    options = provider.get("options")
    config_url = options.get("baseURL") if isinstance(options, dict) else None
    address = _read_secret_file("host_address.txt") or os.environ.get("ONIT_BASE_URL") or config_url
    model_id = os.environ.get("ONIT_MODEL") or _first_model_id(provider.get("models"))

    if not api_key or not address or not model_id:
        print(_NOT_CONFIGURED, file=sys.stderr)
        sys.exit(2)
    return make_chat_model(normalize_base_url(str(address)), api_key, model_id)


def make_chat_model(base_url: str, api_key: str, model_id: str) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    return ChatOpenAI(
        base_url=base_url,
        api_key=SecretStr(api_key),
        model=model_id,
        temperature=0.2,
        max_completion_tokens=160,
        timeout=30,
        max_retries=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )


def describe_failure(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    if status in (401, 403):
        return f"{name}: the server rejected the API key (HTTP {status})"
    if status == 404:
        return f"{name}: path or model not found (HTTP 404)"
    if isinstance(status, int):
        return f"{name}: server answered HTTP {status}"
    if "Timeout" in name:
        return f"{name}: no answer within 30 s"
    if "Connection" in name:
        return f"{name}: could not connect to the server address"
    return name


def main() -> int:
    """`python -m doc_chat.qwen`: one tiny request; prints only OK/failure, never a value."""
    llm = build_qwen_chat_model()
    t0 = time.perf_counter()
    try:
        llm.invoke("Reply with the single word: ready")
    except Exception as exc:
        print(f"qwen check failed: {describe_failure(exc)}", file=sys.stderr)
        return 1
    print(f"qwen check ok ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
