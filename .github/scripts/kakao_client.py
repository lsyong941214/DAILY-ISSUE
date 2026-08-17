"""카카오톡 '나에게 보내기' 공용 클라이언트.

KAKAO_CLIENT_ID / KAKAO_CLIENT_SECRET / KAKAO_REFRESH_TOKEN 환경변수를 사용한다.
"""

import json
import os
import urllib.parse
import urllib.request

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

# 카카오 text 템플릿의 text 필드 제한(200자)에 여유를 둔 값
TEXT_LIMIT = 195


def _post(url, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def get_access_token():
    resp = _post(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": os.environ["KAKAO_CLIENT_ID"],
            "client_secret": os.environ["KAKAO_CLIENT_SECRET"],
            "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"],
        },
    )
    return resp["access_token"]


def send_text(access_token, text, link_url):
    """텍스트 메시지 한 건을 나에게 보내기로 전송한다."""
    text = text.strip()
    if len(text) > TEXT_LIMIT:
        text = text[: TEXT_LIMIT - 1].rstrip() + "…"

    template = json.dumps(
        {
            "object_type": "text",
            "text": text,
            "link": {"web_url": link_url, "mobile_web_url": link_url},
            "button_title": "영상 보기",
        },
        ensure_ascii=False,
    )

    result = _post(
        MEMO_URL,
        {"template_object": template},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if result.get("result_code") != 0:
        raise RuntimeError(f"Kakao send failed: {result}")
    return result
