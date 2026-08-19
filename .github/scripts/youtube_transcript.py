"""유튜브 자막(자동 생성 포함)을 받아온다.

영상 파일을 그대로 모델에 넘기면 입력 토큰을 엄청나게 쓰지만,
자막 텍스트는 같은 내용을 훨씬 적은 토큰으로 담는다.
자막을 못 받아오는 경우도 흔해서(비공개/미생성/차단) 실패하면 None을 준다.
"""

import json
import re
import urllib.parse

import http_util

# 선호 언어 순서. 앞쪽을 먼저 쓴다.
PREFERRED = ("ko", "ko-KR", "en", "en-US")
# 모델에 넘길 자막 최대 길이 (토큰 낭비 방지)
MAX_CHARS = 20000


def _caption_tracks_from_watch(video_id):
    """watch 페이지에 박혀 있는 플레이어 응답 JSON에서 자막 목록을 찾는다."""
    html = http_util.get(f"https://www.youtube.com/watch?v={video_id}&hl=en").decode(
        "utf-8", "replace"
    )
    match = re.search(r'"captionTracks":(\[.*?\])', html)
    if not match:
        print(f"[info] captionTracks를 찾지 못했습니다 (페이지 {len(html)}자).")
        return []
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return []


def _caption_tracks_from_timedtext(video_id):
    """watch 페이지 스크래핑이 막혔을 때를 대비한 목록 API 폴백."""
    try:
        xml = http_util.get(
            f"https://www.youtube.com/api/timedtext?type=list&v={video_id}"
        ).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 마지막 폴백이라 원인 구분이 필요 없다
        print(f"[warn] timedtext 목록 조회 실패: {exc}")
        return []

    tracks = []
    for m in re.finditer(r"<track\b([^>]*)/?>", xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        lang = attrs.get("lang_code", "")
        if not lang:
            continue
        kind = "asr" if attrs.get("kind") == "asr" else ""
        params = {"lang": lang, "v": video_id}
        if kind:
            params["kind"] = kind
        if attrs.get("name"):
            params["name"] = attrs["name"]
        tracks.append(
            {
                "languageCode": lang,
                "kind": kind,
                "baseUrl": "https://www.youtube.com/api/timedtext?" + urllib.parse.urlencode(params),
            }
        )
    return tracks


def _caption_tracks(video_id):
    try:
        tracks = _caption_tracks_from_watch(video_id)
    except Exception as exc:  # noqa: BLE001 - 없으면 없는 대로 다음 방법을 쓴다
        print(f"[warn] watch 페이지에서 자막 목록 조회 실패: {exc}")
        tracks = []
    return tracks or _caption_tracks_from_timedtext(video_id)


def _pick(tracks):
    """선호 언어 → 사람이 만든 자막 우선으로 하나 고른다."""
    def rank(track):
        code = (track.get("languageCode") or "").lower()
        try:
            lang_rank = next(i for i, p in enumerate(PREFERRED) if code.startswith(p.lower()))
        except StopIteration:
            lang_rank = len(PREFERRED)
        # kind == "asr" 이면 자동 생성 자막
        auto = 1 if track.get("kind") == "asr" else 0
        return (lang_rank, auto)

    return min(tracks, key=rank) if tracks else None


def _text_from_json3(raw):
    data = json.loads(raw)
    parts = []
    for event in data.get("events", []):
        for seg in event.get("segs", []) or []:
            parts.append(seg.get("utf8", ""))
    text = "".join(parts)
    text = re.sub(r"\[[^\]]{1,20}\]", " ", text)  # [음악], [박수] 같은 표기 제거
    return re.sub(r"\s+", " ", text).strip()


def fetch(video_id):
    """자막 텍스트를 반환. 못 받으면 None."""
    try:
        tracks = _caption_tracks(video_id)
    except Exception as exc:  # noqa: BLE001 - 없으면 없는 대로 진행한다
        print(f"[warn] 자막 목록 조회 실패: {exc}")
        return None

    track = _pick(tracks)
    if not track or not track.get("baseUrl"):
        print("[info] 사용할 수 있는 자막이 없습니다.")
        return None

    url = track["baseUrl"]
    if "fmt=" not in url:
        url += ("&" if "?" in url else "?") + "fmt=json3"
    try:
        text = _text_from_json3(http_util.get(url))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 자막 내려받기 실패: {exc}")
        return None

    if not text:
        print("[info] 자막이 비어 있습니다.")
        return None

    kind = "자동 생성" if track.get("kind") == "asr" else "제작"
    print(f"[info] 자막 확보: {track.get('languageCode')} ({kind}), {len(text)}자")
    return text[:MAX_CHARS]
