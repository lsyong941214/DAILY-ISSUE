"""Google Gemini API 최소 클라이언트 (표준 라이브러리만 사용).

무료 티어로 쓸 수 있고, 유튜브 URL을 그대로 넘기면 영상 자체를 분석해 준다.
결제수단 등록 없이 GEMINI_API_KEY 발급만으로 동작한다.
"""

import json
import os
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
# 모델 이름이 바뀌었을 때 자동으로 시도해 볼 후보들
MODEL_FALLBACKS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]

_resolved_model = None


class GeminiError(RuntimeError):
    pass


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


def _list_models():
    req = urllib.request.Request(
        f"{BASE}/models", headers={"x-goog-api-key": _key()}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return [
        m["name"].split("/", 1)[-1]
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


def _pick_model():
    """설정된 모델이 없을 때 쓸 수 있는 모델을 찾아 준다."""
    try:
        names = _list_models()
    except Exception as exc:  # noqa: BLE001
        raise GeminiError(f"사용 가능한 모델 목록을 가져오지 못했습니다: {exc}") from exc
    for candidate in MODEL_FALLBACKS:
        if candidate in names:
            return candidate
    for name in names:
        if "flash" in name:
            return name
    if names:
        return names[0]
    raise GeminiError("이 API 키로 쓸 수 있는 모델이 없습니다.")


def _extract_text(data):
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback", {})
        raise GeminiError(f"응답이 비어 있습니다. promptFeedback={feedback}")
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
    try:
        data = _post(f"models/{model}:generateContent", payload, timeout)
    except GeminiError as exc:
        message = str(exc)
        # 검색 도구를 함께 못 쓰는 경우 도구 없이 한 번 더 시도한다.
        if use_search and "HTTP 400" in message:
            print("[warn] 검색 도구를 쓸 수 없어 도구 없이 다시 시도합니다.")
            payload.pop("tools", None)
            data = _post(f"models/{model}:generateContent", payload, timeout)
        # 모델 이름이 맞지 않으면 쓸 수 있는 모델을 찾아 다시 시도한다.
        elif "HTTP 404" in message and _resolved_model is None:
            picked = _pick_model()
            print(f"[info] '{model}' 모델을 쓸 수 없어 '{picked}' 로 대체합니다.")
            _resolved_model = picked
            data = _post(f"models/{picked}:generateContent", payload, timeout)
        else:
            raise
    else:
        _resolved_model = model

    return _extract_text(data)
