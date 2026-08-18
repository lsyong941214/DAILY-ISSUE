"""카카오톡 '나에게 보내기' 공용 클라이언트.

KAKAO_CLIENT_ID / KAKAO_CLIENT_SECRET / KAKAO_REFRESH_TOKEN 환경변수를 사용한다.
"""

import json
import os
import urllib.parse
import urllib.request

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

# 카카오가 긴 텍스트를 거부할 경우 이 길이로 잘라 나눠 보낸다.
CHUNK_LIMIT = 190


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


def _split(text, limit):
    """빈 줄 → 줄 단위로 끊어 limit 이하 조각들로 나눈다."""
    chunks = []
    current = ""
    for block in text.split("\n\n"):
        for piece in ([block] if len(block) <= limit else block.split("\n")):
            piece = piece.rstrip()
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = piece[:limit]
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


def send_brief(access_token, text, link_url):
    """브리핑을 한 통으로 보내되, 길이 때문에 거부되면 나눠 보낸다."""
    try:
        return send_text(access_token, text, link_url)
    except Exception as exc:  # noqa: BLE001 - 길이 제한 등으로 거부될 수 있다
        print(f"[warn] 한 통으로 보내지 못해 나눠 보냅니다: {exc}")

    chunks = _split(text, CHUNK_LIMIT)
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        suffix = f"\n({i}/{total})" if total > 1 else ""
        send_text(access_token, chunk + suffix, link_url)
    return {"result_code": 0, "split": total}
