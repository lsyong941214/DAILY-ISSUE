"""매일 아침, 유튜브 채널의 최신 영상 1개만 확인해 카카오톡으로 보낸다.

- 확인 대상: 피드의 가장 최근 영상 하나뿐 (지난 영상은 소급해서 보내지 않는다)
- 이미 보낸 영상이면 아무 메시지도 보내지 않고 조용히 끝난다
- 정리: 자막을 근거로 '이슈 → 영향 → 관점' 브리핑 작성 (미국 주식 관점)
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
import http_util
import kakao_client
import youtube_transcript

CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "UCC3yfxS5qC6PCwDzetUuEWg")
CHANNEL_HANDLE = os.environ.get("YOUTUBE_HANDLE", "sosumonkey")
CHANNEL_NAME = os.environ.get("YOUTUBE_CHANNEL_NAME", "소수몽키")
STATE_FILE = os.environ.get("YOUTUBE_STATE_FILE", "data/youtube_seen.json")
KEEP_IDS = 200
# 카카오톡 브리핑 본문 길이 상한
BRIEF_LIMIT = int(os.environ.get("YOUTUBE_BRIEF_LIMIT", "420"))
# 이미 보낸 영상이어도 다시 처리 (수동 재실행용)
FORCE = os.environ.get("YOUTUBE_FORCE", "").lower() in ("1", "true", "yes")
# 카카오톡 전송 없이 최신 영상만 골라 다음 단계로 넘긴다 (원고만 다시 만들 때)
SKIP_KAKAO = os.environ.get("YOUTUBE_SKIP_KAKAO", "").lower() in ("1", "true", "yes")
# 이번 실행에서 새로 알린 영상 목록 (티스토리 자료 생성 단계가 읽어간다)
NEW_VIDEOS_FILE = os.environ.get("NEW_VIDEOS_FILE", ".new_videos.json")

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
# 유튜브가 일시적으로 404/429를 주는 경우가 있어 몇 번 다시 시도한다.
FEED_RETRY_WAITS = [5, 15, 30]

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

KST = timezone(timedelta(hours=9))


def http_get(url, timeout=30):
    return http_util.get(url, timeout=timeout, retry_waits=FEED_RETRY_WAITS)


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


BRIEF_PROMPT = """당신은 미국 주식에 투자하는 사람에게 아침 브리핑을 써 주는 애널리스트입니다.
아래 유튜브 영상(미국 증시 현황·변동사항을 다루는 채널)의 내용을 정리하세요.

[정리 관점]
- 정확한 수치 나열은 중요하지 않습니다. **누가 무엇을 했고, 그게 어디에 어떤 영향을 주는지**가 핵심입니다.
- 어느 기업·국가·기관이 어떤 발표나 결정을 했는지 사실을 먼저 짚으세요.
- 그 사건이 어떤 섹터나 종목의 주가에 어떻게 작용하는지(수혜/타격, 방향과 이유) 쓰세요.
- 투자자가 지금 무엇을 봐야 하는지 관점을 한 덩어리로 정리하세요.
- 영상이 여러 주제를 다루면 가장 중요한 1~2개만 고르세요.
- 영상에 없는 내용을 지어내지 마세요. 종목명·티커는 영상에서 언급된 것만 씁니다.

[출력 형식] 아래 형식을 그대로 지키세요. 다른 말은 붙이지 마세요.

📌 이슈
(무엇이 일어났는지 1~2줄, 각 줄 45자 이내)

📈 영향
· (영향받는 섹터/종목과 방향, 45자 이내)
· (다른 하나, 45자 이내. 없으면 이 줄 생략)

🧭 관점
(투자자가 봐야 할 포인트 1~2줄, 각 줄 45자 이내)

[영상 제목] {title}
{source}
"""


def build_brief(entry):
    """자막을 근거로 '이슈 → 영향 → 관점' 브리핑을 만든다. 실패하면 None."""
    if not gemini_client.available():
        return None

    transcript = (entry.get("transcript") or "").strip()
    if transcript:
        source = f"[영상 자막]\n{transcript[:15000]}"
    else:
        description = (entry.get("description") or "").strip()
        if not description:
            return None
        source = f"[영상 설명란]\n{description[:2000]}\n(자막을 받지 못해 설명란만 있습니다. 확실한 것만 쓰세요.)"

    prompt = BRIEF_PROMPT.format(title=entry["title"], source=source)
    try:
        text = gemini_client.generate(prompt, max_output_tokens=3000, timeout=180)
    except Exception as exc:  # noqa: BLE001 - 브리핑 실패가 알림 자체를 막으면 안 된다
        print(f"[warn] 브리핑 작성 실패, 설명으로 대체합니다: {exc}")
        return None

    return tidy_brief(text)


def tidy_brief(text):
    """코드펜스 제거, 빈 줄 정리, 길이 제한."""
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", (text or "").strip())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= BRIEF_LIMIT:
        return text or None

    # 길면 줄 단위로 잘라 낸다 (문장이 중간에 끊기지 않게)
    kept = []
    used = 0
    for line in text.split("\n"):
        if used + len(line) + 1 > BRIEF_LIMIT:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept).strip() or text[:BRIEF_LIMIT]


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
    header = f"🇺🇸 {CHANNEL_NAME}" + (f" · {published}" if published else "")
    title = clip(entry["title"], 60)

    body = build_brief(entry) or clip(fallback_summary(entry.get("description", "")), 150)

    parts = [header, title]
    if body:
        parts += ["", body]
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

    # 자막을 먼저 확보한다. 요약과 원고 작성 모두 이걸 근거로 쓴다.
    latest["transcript"] = youtube_transcript.fetch(latest["id"]) or ""

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
    kakao_client.send_brief(access_token, build_message(latest), latest["url"])
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
