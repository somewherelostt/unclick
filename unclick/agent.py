"""Unclick's agent brain: an OpenAI(-compatible) client plus the high-level decisions the pipeline asks for.

Env: OPENAI_API_KEY (required), LLM_MODEL (default gpt-4.1, needs vision + tool calling),
LLM_BASE_URL (optional, e.g. https://openrouter.ai/api/v1). Keys are never logged.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

from unclick import prompts

load_dotenv()

DEFAULT_MODEL = "gpt-4.1"


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str = ""


@dataclass
class ExploreState:
    """Conversation state for the explore loop (kept by the agent, passed back by the pipeline)."""
    messages: list[dict[str, Any]] = field(default_factory=list)


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def _parse_json(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


class LLM:
    """Thin wrapper over the openai SDK. Works with OpenAI or any OpenAI-compatible base URL."""

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None):
        from openai import OpenAI

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set (put it in .env)")
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        self.client = OpenAI(api_key=key, base_url=(base_url or os.environ.get("LLM_BASE_URL") or None),
                             timeout=60, max_retries=2)

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None,
             tool_choice: str | None = None, json_mode: bool = False, temperature: float = 0.1,
             max_tokens: int = 4000):
        kwargs: dict[str, Any] = dict(model=self.model, messages=messages, temperature=temperature,
                                      max_tokens=max_tokens)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "required"
            kwargs["parallel_tool_calls"] = False
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return self.client.chat.completions.create(**kwargs).choices[0].message

    def json(self, system: str, user: str) -> Any:
        msg = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], json_mode=True)
        return _parse_json(msg.content or "{}")

    def text(self, messages: list[dict[str, Any]], max_tokens: int = 6000) -> str:
        return self.chat(messages, max_tokens=max_tokens).content or ""


LINK_LABELS = {"visit", "repo", "link", "links", "view", "open", "more", "details", "stack", "website", "site", "url"}


def _clean_expected(item: dict[str, Any]) -> dict[str, Any]:
    """Keep only unambiguous ground truth: drop empty values, link labels and every URL-ish field."""
    out = {}
    for k, v in item.items():
        if v is None or v == "" or isinstance(v, (dict, list)):
            continue
        if isinstance(v, str):
            if v.strip().lower() in LINK_LABELS:
                continue
        if re.search(r"url|link|href|website|domain", k, re.I):
            continue  # real URLs sit behind links; what looks like one on screen is often another column
        out[k] = v
    return out


def _png_size(png: bytes) -> tuple[int, int]:
    if png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) >= 24:
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return 0, 0


class Agent:
    """High-level decisions. `fakes.ScriptedAgent` implements the same interface for offline tests."""

    def __init__(self, llm: LLM | None = None):
        self.llm = llm or LLM()

    @property
    def model(self) -> str:
        return self.llm.model

    # -- explore -------------------------------------------------------------
    def explore_start(self, url: str, task: str) -> ExploreState:
        return ExploreState(messages=[{"role": "system", "content": prompts.EXPLORE_SYSTEM}])

    def explore_next(self, state: ExploreState, *, url: str, task: str, step: int, max_steps: int,
                     screenshot: bytes | None, page_text: str, captures: list[str], controls: str = "") -> ToolCall:
        w, h = _png_size(screenshot or b"")
        text = prompts.EXPLORE_TURN.format(
            task=task, url=url, step=step, max_steps=max_steps, width=w or "?", height=h or "?",
            page_text=(page_text or "")[:4000], n_captures=len(captures), controls=controls or "(not available)",
            captures="\n".join(captures[:15]) or "(none yet)")
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if screenshot:
            b64 = base64.b64encode(screenshot).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}})
        # Keep context small: drop images from earlier turns.
        for m in state.messages:
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                m["content"] = [c for c in m["content"] if c.get("type") == "text"]
        state.messages.append({"role": "user", "content": content})

        msg = self.llm.chat(state.messages, tools=prompts.EXPLORE_TOOLS, max_tokens=500)
        calls = msg.tool_calls or []
        if not calls:
            state.messages.append({"role": "assistant", "content": msg.content or ""})
            return ToolCall("done", {"summary": msg.content or "no action"})
        tc = calls[0]
        state.messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}]})
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        return ToolCall(tc.function.name, args, tc.id)

    def explore_result(self, state: ExploreState, call: ToolCall, result: str) -> None:
        if call.id:
            state.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    # -- discover ------------------------------------------------------------
    def pick_endpoint(self, task: str, page_text: str, candidates: list[str]) -> tuple[int, str]:
        out = self.llm.json(prompts.PICK_ENDPOINT_SYSTEM, prompts.PICK_ENDPOINT_USER.format(
            task=task, page_text=page_text[:3000], candidates="\n\n".join(candidates)))
        idx = int(out.get("index", 0))
        return (idx if 0 <= idx < len(candidates) else 0), str(out.get("why", ""))

    # -- verify ground truth -------------------------------------------------
    def extract_expected(self, task: str, page_text: str) -> list[dict[str, Any]]:
        out = self.llm.json(prompts.EXPECTED_SYSTEM, prompts.EXPECTED_USER.format(task=task, page_text=page_text[:8000]))
        items = out.get("items") if isinstance(out, dict) else out
        return [c for c in (_clean_expected(i) for i in (items or []) if isinstance(i, dict)) if c][:5]

    # -- code ----------------------------------------------------------------
    def write_client(self, ctx: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        """First attempt. Returns (code, conversation) so fixes keep the full context."""
        conv = [{"role": "system", "content": prompts.CODE_SYSTEM},
                {"role": "user", "content": prompts.CODE_USER.format(**ctx)}]
        reply = self.llm.text(conv)
        conv.append({"role": "assistant", "content": reply})
        return extract_code(reply), conv

    def fix_client(self, conv: list[dict[str, Any]], feedback: dict[str, Any]) -> str:
        conv.append({"role": "user", "content": prompts.FIX_USER.format(**feedback)})
        reply = self.llm.text(conv)
        conv.append({"role": "assistant", "content": reply})
        return extract_code(reply)
