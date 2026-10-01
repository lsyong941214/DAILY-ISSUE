import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from nacl import encoding, public


def post(url, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} from {url}: {e.read().decode(errors='replace')}", file=sys.stderr)
        raise


def request_json(url, method="GET", data=None, headers=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def rotate_refresh_token_secret(new_refresh_token):
    """카카오가 재발급한 refresh_token을 GitHub Actions secret에 반영한다.

    카카오 OAuth는 리프레시 토큰의 남은 유효기간이 얼마 안 남으면 재발급 시
    새 refresh_token을 함께 내려주고 기존 토큰을 폐기한다. 이를 저장하지
    않으면 다음 실행부터 저장된 토큰이 무효화되어 전송이 영구적으로
    실패하므로, 여기서 Secret을 즉시 갱신해 둔다.
    """
    gh_token = os.environ.get("GH_PAT")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not gh_token or not repo:
        print("GH_PAT/GITHUB_REPOSITORY가 없어 refresh token 자동 갱신을 건너뜁니다.")
        return

    api_base = f"https://api.github.com/repos/{repo}/actions/secrets"
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    key_info = request_json(f"{api_base}/public-key", headers=headers)
    public_key = public.PublicKey(key_info["key"].encode(), encoding.Base64Encoder())
    encrypted_value = base64.b64encode(
        public.SealedBox(public_key).encrypt(new_refresh_token.encode())
    ).decode()

    request_json(
        f"{api_base}/KAKAO_REFRESH_TOKEN",
        method="PUT",
        data={"encrypted_value": encrypted_value, "key_id": key_info["key_id"]},
        headers={**headers, "Content-Type": "application/json"},
    )
    print("KAKAO_REFRESH_TOKEN secret을 새로 발급된 토큰으로 갱신했습니다.")


def main():
    current_refresh_token = os.environ["KAKAO_REFRESH_TOKEN"]
    token_resp = post(
        "https://kauth.kakao.com/oauth/token",
        {
            "grant_type": "refresh_token",
            "client_id": os.environ["KAKAO_CLIENT_ID"],
            "client_secret": os.environ["KAKAO_CLIENT_SECRET"],
            "refresh_token": current_refresh_token,
        },
    )
    access_token = token_resp["access_token"]

    new_refresh_token = token_resp.get("refresh_token")
    if new_refresh_token and new_refresh_token != current_refresh_token:
        rotate_refresh_token_secret(new_refresh_token)

    with open("briefing.txt", encoding="utf-8") as f:
        text = f.read().strip()

    template = json.dumps(
        {
            "object_type": "text",
            "text": text,
            "link": {
                "web_url": "https://developers.kakao.com",
                "mobile_web_url": "https://developers.kakao.com",
            },
        }
    )

    result = post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        {"template_object": template},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    print(result)
    if result.get("result_code") != 0:
        sys.exit(f"Kakao send failed: {result}")


if __name__ == "__main__":
    main()
