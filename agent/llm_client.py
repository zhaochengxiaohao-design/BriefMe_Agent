"""OpenAI 兼容的 Chat Completions 客户端（DeepSeek / 其他兼容网关）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


@dataclass
class FunctionCall:
    name: str
    arguments: str


@dataclass
class ToolCall:
    id: str
    type: str
    function: FunctionCall


@dataclass
class ChatMessage:
    role: str
    content: Optional[str]
    tool_calls: Optional[List[ToolCall]] = None


@dataclass
class Choice:
    message: ChatMessage
    finish_reason: Optional[str] = None


@dataclass
class ChatCompletion:
    choices: List[Choice]


def parse_chat_completion(payload: Dict[str, Any]) -> ChatCompletion:
    """把 OpenAI 兼容 JSON 转成 core.py 使用的对象形状。"""
    choices: List[Choice] = []
    for raw in payload.get("choices") or []:
        msg = raw.get("message") or {}
        tool_calls = None
        raw_tools = msg.get("tool_calls") or []
        if raw_tools:
            tool_calls = [
                ToolCall(
                    id=str(tc.get("id") or ""),
                    type=str(tc.get("type") or "function"),
                    function=FunctionCall(
                        name=str((tc.get("function") or {}).get("name") or ""),
                        arguments=str((tc.get("function") or {}).get("arguments") or "{}"),
                    ),
                )
                for tc in raw_tools
            ]
        choices.append(
            Choice(
                message=ChatMessage(
                    role=str(msg.get("role") or "assistant"),
                    content=msg.get("content"),
                    tool_calls=tool_calls,
                ),
                finish_reason=raw.get("finish_reason"),
            )
        )
    if not choices:
        raise ValueError(f"LLM 返回缺少 choices: {payload}")
    return ChatCompletion(choices=choices)


class _Completions:
    def __init__(self, client: "OpenAICompatClient") -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        timeout: Optional[float] = None,
    ) -> ChatCompletion:
        body: Dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            body["tools"] = tools
        url = f"{self._client.base_url.rstrip('/')}/chat/completions"
        with httpx.Client(timeout=timeout or self._client.timeout) as http:
            resp = http.post(url, headers=self._client.headers, json=body)
            try:
                payload = resp.json()
            except Exception as exc:
                raise RuntimeError(
                    f"LLM 响应不是 JSON (HTTP {resp.status_code}): {resp.text[:300]}"
                ) from exc
            if resp.status_code >= 400:
                err = payload.get("error") or payload
                raise RuntimeError(f"LLM HTTP {resp.status_code}: {err}")
        return parse_chat_completion(payload)


class _Chat:
    def __init__(self, client: "OpenAICompatClient") -> None:
        self.completions = _Completions(client)


class OpenAICompatClient:
    """接口对齐 zhipuai.ZhipuAI：client.chat.completions.create(...)"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key 不能为空")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.chat = _Chat(self)
