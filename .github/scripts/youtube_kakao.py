"""매일 아침, 유튜브 채널의 최신 영상 1개만 확인해 카카오톡으로 보낸다.

- 확인 대상: 피드의 가장 최근 영상 하나뿐 (지난 영상은 소급해서 보내지 않는다)
- 이미 보낸 영상이면 아무 메시지도 보내지 않고 조용히 끝난다
- 요약: GEMINI_API_KEY가 있으면 Gemini로 한 문장 요약, 없으면 설명 앞부분 사용
- 전송: 카카오톡 '나에게 보내기' (kakao_client.py)
- 중복 방지: data/youtube_seen.json 에 이미 보낸 영상 ID 저장
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import gemini_client
import kakao_client

CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "UCC3yfxS5qC6PCwDzetUuEWg")
CHANNEL_HANDLE = os.environ.get("YOUTUBE_HANDLE", "sosumonkey")
CHANNEL_NAME = os.environ.get("YOUTUBE_CHANNEL_NAME", "소수몽키")
STATE_FILE = os.environ.get("YOUTUBE_STATE_FILE", "data/youtube_seen.json")
KEEP_IDS = 200
# 카카오톡 요약 길이 상한 (짧게 유지)
SUMMARY_LIMIT = int(os.environ.get("YOUTUBE_SUMMARY_LIMIT", "85"))
# 이미 보낸 영상이어도 다시 처리 (수동 재실행용)
FORCE = os.environ.get("YOUTUBE_FORCE", "").lower() in ("1", "true", "yes")
# 카카오톡 전송 없이 최신 영상만 골라 다음 단계로 넘긴다 (원고만 다시 만들 때)
SKIP_KAKAO = os.environ.get("YOUTUBE_SKIP_KAKAO", "").lower() in ("1", "true", "yes")
# 이번 실행에서 새로 알린 영상 목록 (티스토리 자료 생성 단계가 읽어간다)
NEW_VIDEOS_FILE = os.environ.get("NEW_VIDEOS_FILE", ".new_videos.json")

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
# 봇으로 보이는 UA는 유튜브가 차단하는 경우가 있어 일반 브라우저 UA를 쓴다.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
# 유튜브가 일시적으로 404/429를 주는 경우가 있어 몇 번 다시 시도한다.
FEED_RETRY_WAITS = [5, 15, 30]

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

KST = timezone(timedelta(hours=9))


def http_get(url, timeout=30):
    waits = list(FEED_RETRY_WAITS)
    while True:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if not waits:
                raise
            wait = waits.pop(0)
            print(f"[warn] 조회 실패({exc}). {wait}초 뒤 다시 시도합니다.")
            time.sleep(wait)


def resolve_channel_id(handle):
    """핸들(@sosumonkey) 페이지에서 채널 ID를 찾아낸다. 실패하면 None."""
    try:
        html = http_get(f"https://www.youtube.com/@{handle}").decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 폴백 경로라 원인 구분이 필요 없다
        print(f"[warn] 핸들로 채널 ID 조회 실패: {exc}")
        return None
    match = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', html)
    return match.group(1) if match else None


def fetch_entries(channel_id):
    root = ET.fromstring(http_get(FEED_URL.format(channel_id)))
    entries = []
    for entry in root.findall("atom:entry", NS):
        video_id = entry.findtext("yt:videoId", default="", namespaces=NS)
        if not video_id:
            continue
        group = entry.find("media:group", NS)
        description = ""
        if group is not None:
            description = group.findtext("media:description", default="", namespaces=NS) or ""
        entries.append(
            {
                "id": video_id,
                "title": (entry.findtext("atom:title", default="", namespaces=NS) or "").strip(),
                "published": entry.findtext("atom:published", default="", namespaces=NS),
                "description": description.strip(),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "short_url": f"https://youtu.be/{video_id}",
            }
        )
    return entries


def fetch_latest_from_page(handle):
    """RSS가 막혔을 때 채널 페이지 HTML에서 최신 영상 하나를 뽑는다."""
    try:
        html = http_get(f"https://www.youtube.com/@{handle}/videos").decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 마지막 폴백이라 원인 구분이 필요 없다
        print(f"[warn] 채널 페이지 조회 실패: {exc}")
        return None

    match = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
    if not match:
        print("[warn] 채널 페이지에서 영상을 찾지 못했습니다.")
        return None

    video_id = match.group(1)
    window = html[match.end() : match.end() + 3000]
    title_match = re.search(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', window)
    if title_match:
        try:
            title = json.loads(f'"{title_match.group(1)}"')
        except json.JSONDecodeError:
            title = video_id
    else:
        title = video_id

    print(f"[info] 채널 페이지에서 최신 영상을 찾았습니다: {title}")
    return {
        "id": video_id,
        "title": title,
        "published": "",
        "description": "",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "short_url": f"https://youtu.be/{video_id}",
    }


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return None
    if not isinstance(state.get("seen"), list):
        return None
    return state


def save_state(channel_id, seen_ids):
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"channel_id": channel_id, "seen": seen_ids[:KEEP_IDS]},
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")


def format_published(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ""
    return dt.astimezone(KST).strftime("%m/%d %H:%M")


def summarize_with_llm(title, description, max_chars):
    """Gemini로 영상 핵심을 한 문장 요약. 키가 없거나 실패하면 None."""
    if not gemini_client.available():
        return None

    prompt = (
        "다음 유튜브 영상이 무엇을 말하는 영상인지 한국어 한 문장으로 요약하세요.\n"
        f"- 공백 포함 {max_chars}자 이내, 반드시 한 문장\n"
        "- 인사말·머리말·따옴표 없이 요약문만 출력\n"
        "- '이 영상은' 같은 군더더기 없이 핵심 내용부터 바로 쓰기\n"
        "- 설명란이 부실하면 제목만 보고 핵심 주제를 적기\n\n"
        f"[제목] {title}\n[설명] {description[:2000]}"
    )
    try:
        return gemini_client.generate(prompt, max_output_tokens=2048, timeout=120) or None
    except Exception as exc:  # noqa: BLE001 - 요약 실패는 치명적이지 않다
        print(f"[warn] Gemini 요약 실패, 설명으로 대체합니다: {exc}")
    return None


def fallback_summary(description):
    text = re.sub(r"https?://\S+", "", description)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clip(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def build_message(entry):
    published = format_published(entry["published"])
    header = f"🎥 {CHANNEL_NAME}" + (f" · {published}" if published else "")
    title = clip(entry["title"], 60)

    fixed = f"{header}\n{title}\n\n\n\n{entry['short_url']}"
    budget = min(SUMMARY_LIMIT, kakao_client.TEXT_LIMIT - len(fixed))

    summary = ""
    if budget > 20:
        summary = summarize_with_llm(entry["title"], entry["description"], budget) or ""
        if not summary:
            summary = fallback_summary(entry["description"])
        summary = clip(summary, budget)

    parts = [header, title]
    if summary:
        parts += ["", summary]
    parts += ["", entry["short_url"]]
    return "\n".join(parts)


def main():
    channel_id = CHANNEL_ID
    try:
        entries = fetch_entries(channel_id)
    except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError) as exc:
        print(f"[warn] 피드 조회 실패({exc}). 핸들로 채널 ID를 다시 찾습니다.")
        resolved = resolve_channel_id(CHANNEL_HANDLE)
        if resolved and resolved != channel_id:
            channel_id = resolved
            print(f"[info] 채널 ID를 {channel_id} 로 갱신했습니다.")
        try:
            entries = fetch_entries(channel_id)
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError) as exc2:
            print(f"[warn] 피드가 계속 막혀 채널 페이지에서 최신 영상을 찾습니다: {exc2}")
            fallback = fetch_latest_from_page(CHANNEL_HANDLE)
            if not fallback:
                sys.exit(f"최신 영상을 확인하지 못했습니다: {exc}")
            entries = [fallback]

    if not entries:
        print("피드에 영상이 없습니다.")
        return

    # 피드는 최신순이다. 가장 최근 영상 하나만 본다.
    latest = entries[0]
    state = load_state()
    seen = state["seen"] if state else []

    if SKIP_KAKAO:
        # 카카오톡은 건너뛰고 원고 생성 단계로만 넘긴다. 상태도 건드리지 않는다.
        print(f"[info] 카카오톡 전송 없이 원고만 다시 만듭니다: {latest['title']}")
        with open(NEW_VIDEOS_FILE, "w", encoding="utf-8") as f:
            json.dump([latest], f, ensure_ascii=False, indent=2)
        return

    if latest["id"] in seen and not FORCE:
        print(f"새 영상이 없습니다. (최신: {latest['title']})")
        return
    if latest["id"] in seen:
        print("[info] 강제 실행: 이미 보낸 영상을 다시 처리합니다.")

    access_token = kakao_client.get_access_token()
    kakao_client.send_text(access_token, build_message(latest), latest["url"])
    print(f"전송 완료: {latest['title']} ({latest['url']})")

    # 최신 영상 외 나머지는 '이미 본 것'으로만 기록해 소급 전송을 막는다.
    merged = [latest["id"]] + [e["id"] for e in entries[1:]] + seen
    deduped = list(dict.fromkeys(merged))
    save_state(channel_id, deduped)

    # 티스토리 자료 생성 단계로 넘길 목록을 남긴다.
    with open(NEW_VIDEOS_FILE, "w", encoding="utf-8") as f:
        json.dump([latest], f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
