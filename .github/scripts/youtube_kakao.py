"""유튜브 채널에 새 영상이 올라오면 요약해서 카카오톡으로 보낸다.

- 새 영상 확인: 유튜브 RSS 피드 (API 키 불필요)
- 요약: ANTHROPIC_API_KEY가 있으면 Claude로 요약, 없으면 설명 앞부분 사용
- 전송: 카카오톡 '나에게 보내기' (kakao_client.py)
- 중복 방지: data/youtube_seen.json 에 이미 보낸 영상 ID 저장
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import claude_client
import kakao_client

CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "UCC3yfxS5qC6PCwDzetUuEWg")
CHANNEL_HANDLE = os.environ.get("YOUTUBE_HANDLE", "sosumonkey")
STATE_FILE = os.environ.get("YOUTUBE_STATE_FILE", "data/youtube_seen.json")
# 한 번 실행에 보낼 수 있는 최대 개수 (몰아서 올라올 때 도배 방지)
MAX_NOTIFY = int(os.environ.get("YOUTUBE_MAX_NOTIFY", "5"))
KEEP_IDS = 200
# 이번 실행에서 새로 알린 영상 목록 (티스토리 자료 생성 단계가 읽어간다)
NEW_VIDEOS_FILE = os.environ.get("NEW_VIDEOS_FILE", ".new_videos.json")

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
USER_AGENT = "Mozilla/5.0 (compatible; daily-issue-bot/1.0)"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

KST = timezone(timedelta(hours=9))


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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


def summarize_with_claude(title, description, max_chars):
    """Claude로 영상 내용을 한두 문장 요약. 키가 없거나 실패하면 None."""
    if not claude_client.available():
        return None

    prompt = (
        "다음은 유튜브 영상의 제목과 설명입니다. 어떤 내용의 영상인지 한국어로 "
        f"{max_chars}자 이내, 1~2문장으로 요약해 주세요. 인사말이나 머리말 없이 "
        "요약문만 답하세요. 설명이 부실하면 제목만 보고 핵심 주제를 적으세요.\n\n"
        f"[제목] {title}\n[설명] {description[:2000]}"
    )
    try:
        return claude_client.complete(prompt, max_tokens=1000, effort="low") or None
    except claude_client.RefusalError:
        print("[warn] Claude가 요약을 거부했습니다. 설명으로 대체합니다.")
    except Exception as exc:  # noqa: BLE001 - 요약 실패는 치명적이지 않다
        print(f"[warn] Claude 요약 실패, 설명으로 대체합니다: {exc}")
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
    header = "🎥 소수몽키 새 영상"
    if published:
        header += f" ({published})"
    title = clip(entry["title"], 60)

    fixed = f"{header}\n{title}\n\n\n\n{entry['short_url']}"
    budget = kakao_client.TEXT_LIMIT - len(fixed)

    summary = ""
    if budget > 20:
        summary = summarize_with_claude(entry["title"], entry["description"], budget) or ""
        if not summary:
            summary = fallback_summary(entry["description"])
        summary = clip(summary, budget)

    parts = [header, title]
    if summary:
        parts.append("")
        parts.append(summary)
    parts.append("")
    parts.append(entry["short_url"])
    return "\n".join(parts)


def main():
    channel_id = CHANNEL_ID
    try:
        entries = fetch_entries(channel_id)
    except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError) as exc:
        print(f"[warn] 피드 조회 실패({exc}). 핸들로 채널 ID를 다시 찾습니다.")
        resolved = resolve_channel_id(CHANNEL_HANDLE)
        if not resolved or resolved == channel_id:
            sys.exit(f"피드를 가져오지 못했습니다: {exc}")
        channel_id = resolved
        print(f"[info] 채널 ID를 {channel_id} 로 갱신했습니다.")
        entries = fetch_entries(channel_id)

    if not entries:
        print("피드에 영상이 없습니다.")
        return

    state = load_state()
    if state is None:
        # 첫 실행: 지금 올라와 있는 영상은 '이미 본 것'으로 기록만 하고 보내지 않는다.
        save_state(channel_id, [e["id"] for e in entries])
        print(f"첫 실행: 기존 영상 {len(entries)}개를 기록만 했습니다. 다음 실행부터 알림을 보냅니다.")
        return

    seen = state["seen"]
    seen_set = set(seen)
    # 피드는 최신순이므로 뒤집어서 오래된 새 영상부터 보낸다.
    new_entries = [e for e in reversed(entries) if e["id"] not in seen_set]

    if not new_entries:
        print("새 영상이 없습니다.")
        return

    skipped = new_entries[:-MAX_NOTIFY] if len(new_entries) > MAX_NOTIFY else []
    to_send = new_entries[-MAX_NOTIFY:]
    if skipped:
        print(f"새 영상 {len(new_entries)}개 중 최신 {MAX_NOTIFY}개만 전송합니다.")

    access_token = kakao_client.get_access_token()
    sent_ids = []
    for entry in to_send:
        message = build_message(entry)
        kakao_client.send_text(access_token, message, entry["url"])
        sent_ids.append(entry["id"])
        print(f"전송 완료: {entry['title']} ({entry['url']})")

    # 전송하지 않고 건너뛴 영상도 다시 알리지 않도록 함께 기록한다.
    processed = [e["id"] for e in skipped] + sent_ids
    save_state(channel_id, list(reversed(processed)) + seen)

    # 티스토리 자료 생성 단계로 넘길 목록을 남긴다.
    with open(NEW_VIDEOS_FILE, "w", encoding="utf-8") as f:
        json.dump(to_send, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
