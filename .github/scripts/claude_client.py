"""Anthropic Messages API 최소 클라이언트 (표준 라이브러리만 사용).

의존성 설치 없이 GitHub Actions에서 바로 돌리기 위해 urllib으로 직접 호출한다.
긴 응답과 웹 검색 때문에 응답 시간이 길어질 수 있어 스트리밍(SSE)으로 받는다.
"""

import json
import os
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")


class RefusalError(RuntimeError):
    """안전 분류기가 요청을 거절한 경우."""


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _request(payload, timeout):
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
    )
    return urllib.request.urlopen(req, timeout=timeout)


def complete(prompt, max_tokens=4000, effort="medium", tools=None, timeout=180):
    """비스트리밍 호출. 짧은 응답용."""
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": prompt}],
    }
    if tools:
        payload["tools"] = tools
    with _request(payload, timeout) as resp:
        data = json.load(resp)
    if data.get("stop_reason") == "refusal":
        raise RefusalError(str(data.get("stop_details")))
    return "".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    ).strip()


def complete_streaming(prompt, max_tokens=32000, effort="medium", tools=None, timeout=180):
    """스트리밍 호출. 긴 글 생성이나 웹 검색을 쓸 때 사용한다.

    timeout은 '이벤트 사이 최대 대기 시간'으로 동작하므로, 전체 생성이
    수 분 걸려도 중간에 끊기지 않는다.
    """
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "output_config": {"effort": effort},
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
    }
    if tools:
        payload["tools"] = tools

    chunks = []
    stop_reason = None
    searches = 0
    with _request(payload, timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    chunks.append(delta.get("text", ""))
            elif etype == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "server_tool_use":
                    searches += 1
            elif etype == "message_delta":
                stop_reason = event.get("delta", {}).get("stop_reason", stop_reason)
            elif etype == "error":
                raise RuntimeError(f"Anthropic API error: {event.get('error')}")

    if stop_reason == "refusal":
        raise RefusalError("모델이 응답을 거부했습니다.")
    if searches:
        print(f"[info] 웹 검색 {searches}회 사용")
    if stop_reason == "max_tokens":
        print("[warn] max_tokens에 도달해 응답이 잘렸을 수 있습니다.")
    return "".join(chunks).strip()


WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}
