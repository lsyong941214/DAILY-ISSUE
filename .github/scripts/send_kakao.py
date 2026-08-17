import json
import os
import sys
import urllib.parse
import urllib.request


def post(url, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def main():
    token_resp = post(
        "https://kauth.kakao.com/oauth/token",
        {
            "grant_type": "refresh_token",
            "client_id": os.environ["KAKAO_CLIENT_ID"],
            "client_secret": os.environ["KAKAO_CLIENT_SECRET"],
            "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"],
        },
    )
    access_token = token_resp["access_token"]

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
