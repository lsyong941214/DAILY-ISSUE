"""유튜브 요청용 공용 HTTP 헬퍼.

유튜브는 데이터센터 IP나 봇 티가 나는 요청에 404/429를 주는 경우가 있어
일반 브라우저처럼 요청하고, 실패하면 몇 번 다시 시도한다.
"""

import time
import urllib.error
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
RETRY_WAITS = [5, 15, 30]

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def get(url, timeout=30, retry_waits=None):
    waits = list(RETRY_WAITS if retry_waits is None else retry_waits)
    while True:
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if not waits:
                raise
            wait = waits.pop(0)
            print(f"[warn] 조회 실패({exc}). {wait}초 뒤 다시 시도합니다.")
            time.sleep(wait)
