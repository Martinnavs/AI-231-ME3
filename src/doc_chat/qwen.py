"""Qwen LLM backend scaffold for doc_chat (ticket 24)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


def build_qwen_chat_model() -> BaseChatModel:
    """Build ChatOpenAI configured from opencode.json and ONIT_API_KEY."""
    api_key = os.environ.get("ONIT_API_KEY")
    if not api_key:
        print("qwen backend not configured", file=sys.stderr)
        sys.exit(2)

    config_path = Path.home() / ".config" / "opencode" / "opencode.json"
    if not config_path.is_file():
        print("qwen backend not configured", file=sys.stderr)
        sys.exit(2)

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        print("qwen backend not configured", file=sys.stderr)
        sys.exit(2)

    providers = data.get("provider", {})
    upd = providers.get("upd-ai", {})
    options = upd.get("options", {})
    base_url = options.get("baseURL")
    models = upd.get("models")

    if not base_url or not models:
        print("qwen backend not configured", file=sys.stderr)
        sys.exit(2)

    model_id: str | None = None
    if isinstance(models, list) and models:
        first = models[0]
        model_id = first if isinstance(first, str) else first.get("id")
    elif isinstance(models, dict) and models:
        model_id = next(iter(models.keys()))

    if not model_id:
        print("qwen backend not configured", file=sys.stderr)
        sys.exit(2)

    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    return ChatOpenAI(
        base_url=base_url,
        api_key=SecretStr(api_key),
        model=model_id,
        temperature=0.0,
    )
