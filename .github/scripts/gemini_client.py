"""Google Gemini API 최소 클라이언트 (표준 라이브러리만 사용).

무료 티어로 쓸 수 있고, 유튜브 URL을 그대로 넘기면 영상 자체를 분석해 준다.
결제수단 등록 없이 GEMINI_API_KEY 발급만으로 동작한다.

모델 이름은 수시로 바뀌므로, 404가 나면
  1) API가 오류 메시지로 알려 주는 대체 모델을 쓰고
  2) 그래도 안 되면 계정에서 쓸 수 있는 모델 중 최신 flash 계열을 골라
다시 시도한다.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

_resolved_model = None


class GeminiError(RuntimeError):
    pass


class QuotaError(GeminiError):
    """429 RESOURCE_EXHAUSTED. 잠시 뒤 또는 더 가벼운 요청으로 재시도할 수 있다."""


# 429를 만났을 때 기다렸다 다시 시도할 간격(초)
RETRY_WAITS = [20, 45, 90]


def available():
    return bool(os.environ.get("GEMINI_API_KEY"))


def _key():
    return os.environ["GEMINI_API_KEY"]


def _post(path, payload, timeout):
    req = urllib.request.Request(
        f"{BASE}/{path}",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"content-type": "application/json", "x-goog-api-key": _key()},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:600]
        raise GeminiError(f"HTTP {exc.code}: {body}") from exc


def _suggested_model(message):
    """'Please update your code to use models/X' 같은 안내에서 모델 이름을 뽑는다."""
    match = re.search(r"use\s+models/([A-Za-z0-9._-]+)", message)
    return match.group(1) if match else None


def _version_key(name):
    numbers = re.findall(r"(\d+(?:\.\d+)?)", name)
    return float(numbers[0]) if numbers else 0.0


def _list_models():
    req = urllib.request.Request(f"{BASE}/models", headers={"x-goog-api-key": _key()})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return [
        m["name"].split("/", 1)[-1]
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


def _pick_model(exclude=()):
    """쓸 수 있는 모델 중 하나를 고른다. 이미 실패한 모델은 제외한다."""
    try:
        names = [n for n in _list_models() if n not in exclude]
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 모델 목록 조회 실패: {exc}")
        return None
    if not names:
        return None
    # flash 계열 우선, 그중 버전이 높은 것 우선
    names.sort(key=lambda n: ("flash" in n, _version_key(n)), reverse=True)
    return names[0]


def _extract_text(data):
    candidates = data.get("candidates") or []
    if not candidates:
        raise GeminiError(f"응답이 비어 있습니다. promptFeedback={data.get('promptFeedback', {})}")
    candidate = candidates[0]
    text = "".join(
        part.get("text", "") for part in candidate.get("content", {}).get("parts", [])
    ).strip()
    if not text:
        reason = candidate.get("finishReason", "UNKNOWN")
        if reason == "MAX_TOKENS":
            raise GeminiError(
                "출력 토큰 한도에 걸려 본문이 비었습니다. max_output_tokens를 늘려 보세요."
            )
        raise GeminiError(f"본문이 비었습니다. finishReason={reason}")
    return text


def generate(prompt, video_url=None, max_output_tokens=8192, use_search=False, timeout=300):
    """텍스트 생성. video_url을 주면 그 유튜브 영상을 함께 분석한다."""
    global _resolved_model

    parts = [{"text": prompt}]
    if video_url:
        parts.append({"fileData": {"fileUri": video_url}})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"maxOutputTokens": max_output_tokens},
    }
    if use_search:
        payload["tools"] = [{"google_search": {}}]

    model = _resolved_model or DEFAULT_MODEL
    tried = set()

    quota_waits = list(RETRY_WAITS)

    for _ in range(8):
        tried.add(model)
        try:
            data = _post(f"models/{model}:generateContent", payload, timeout)
        except GeminiError as exc:
            message = str(exc)
            # 분당/일일 한도에 걸린 경우 잠시 기다렸다 다시 시도한다.
            if "HTTP 429" in message:
                if quota_waits:
                    wait = quota_waits.pop(0)
                    print(f"[warn] 사용량 한도(429)에 걸려 {wait}초 뒤 다시 시도합니다.")
                    time.sleep(wait)
                    continue
                raise QuotaError(message) from exc
            # 검색 도구를 함께 못 쓰는 경우 도구 없이 한 번 더 시도한다.
            if "HTTP 400" in message and "tools" in payload:
                print("[warn] 검색 도구를 쓸 수 없어 도구 없이 다시 시도합니다.")
                payload.pop("tools")
                continue
            # 모델 이름이 맞지 않으면 다른 모델로 바꿔 다시 시도한다.
            if "HTTP 404" in message:
                nxt = _suggested_model(message)
                if not nxt or nxt in tried:
                    nxt = _pick_model(exclude=tried)
                if nxt and nxt not in tried:
                    print(f"[info] '{model}' 모델을 쓸 수 없어 '{nxt}' 로 대체합니다.")
                    model = nxt
                    continue
            raise
        _resolved_model = model
        return _extract_text(data)

    raise GeminiError("쓸 수 있는 모델을 찾지 못했습니다. GEMINI_MODEL 값을 확인하세요.")
