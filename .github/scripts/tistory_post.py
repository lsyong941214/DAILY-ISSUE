"""새로 올라온 유튜브 영상을 티스토리 포스팅 자료로 만든다.

youtube_kakao.py가 남긴 .new_videos.json을 읽어서 영상마다
  YYYY-MM-DD/<제목>.html                   (미리보기 + 티스토리 HTML 모드용)
  YYYY-MM-DD/<제목>_붙여넣기용_소스.txt      (에디터에 그대로 붙여넣는 본문)
  YYYY-MM-DD/<제목>_네이버용.txt             (네이버 블로그용 문체로 재작성)
을 생성한다. 기존 경제 이슈 자료와 같은 폴더 규칙·스타일을 따른다.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import gemini_client

NEW_VIDEOS_FILE = os.environ.get("NEW_VIDEOS_FILE", ".new_videos.json")
CHANNEL_NAME = os.environ.get("YOUTUBE_CHANNEL_NAME", "소수몽키")
# 영상 분석은 입력 토큰을 많이 써서 무료 한도를 빨리 소진한다. 0으로 두면 제목·설명만 쓴다.
USE_VIDEO = os.environ.get("TISTORY_USE_VIDEO", "1").lower() not in ("0", "false", "no")
KST = timezone(timedelta(hours=9))

MARK_TITLE = "===TITLE==="
MARK_HTML = "===HTML==="
MARK_NAVER = "===NAVER==="
EMBED_TOKEN = "{{VIDEO_EMBED}}"

HTML_SHELL = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0; padding:32px 16px; background:#fff;">

<!-- ===== 여기부터 복사 : 티스토리 HTML 모드에 붙여넣기 ===== -->

{body}

<!-- ===== 여기까지 복사 ===== -->

</body>
</html>
"""

EMBED_HTML = """  <figure style="margin:0 0 28px 0;">
    <div style="position:relative; padding-bottom:56.25%; height:0; overflow:hidden;">
      <iframe src="https://www.youtube.com/embed/{video_id}" title="{title}"
        style="position:absolute; top:0; left:0; width:100%; height:100%; border:0;"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowfullscreen></iframe>
    </div>
    <figcaption style="font-size:14px; color:#888; margin-top:10px;">
      영상 출처 : <a href="{url}" target="_blank" style="color:#1a5490;">{channel} 「{title}」</a>
    </figcaption>
  </figure>"""

PROMPT = """당신은 경제·투자 이슈를 정리하는 한국어 블로그 작성자입니다.
아래 유튜브 영상을 소재로, 티스토리에 바로 올릴 수 있는 포스팅 자료를 만들어 주세요.

[영상 정보]
- 채널: {channel}
- 제목: {title}
- 업로드: {published}
- 링크: {url}
- 설명란:
{description}

[작업 지침]
1. 첨부된 영상을 직접 보고, 어떤 주장을 어떤 근거로 펴는지 파악하세요.
2. 영상에서 언급된 수치·기업·지표는 검색으로 사실관계를 확인하세요.
   확인되지 않은 숫자는 아예 쓰지 마세요. 출처는 3개 이상 확보하세요.
3. 영상 내용을 그대로 받아쓰지 말고, 영상이 던진 주제를 출발점으로 삼아
   독자가 스스로 판단할 수 있도록 사실과 배경을 정리하는 글을 쓰세요.
   영상의 주장은 "영상에서는 ~라고 봅니다"처럼 출처를 밝혀 인용하세요.
4. 오늘 날짜는 {today}입니다.

[출력 형식] 아래 세 구획을 순서대로, 마커를 정확히 그대로 써서 출력하세요.
다른 말(설명, 인사)은 절대 붙이지 마세요.

{mark_title}
(포스팅 제목 한 줄. 30자 내외, 낚시성 표현 금지, 다루는 소재가 드러나게)

{mark_html}
(아래 규격을 지킨 HTML 본문. <div>로 시작해 </div>로 끝나야 합니다)

{mark_naver}
(같은 내용을 네이버 블로그용 문체로 다시 쓴 순수 텍스트. HTML 태그 금지)

[HTML 규격] 티스토리 에디터에 붙여넣으므로 CSS는 전부 인라인 style로 넣습니다.
- 전체 감싸기:
  <div style="max-width:800px; margin:0 auto; font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif; font-size:17px; line-height:1.8; color:#333; word-break:keep-all;">
- 제목: <h1 style="font-size:28px; font-weight:700; line-height:1.4; color:#1a1a1a; margin:0 0 8px 0;">
- 제목 아래 날짜 줄: <p style="font-size:14px; color:#888; margin:0 0 28px 0;">{today_ko} 기준 · {channel} 영상 정리</p>
- 리드 문단 1개(3~4문장): <p style="margin:0 0 28px 0;">
- 그 다음 줄에 {embed_token} 이라고만 쓰세요. (영상 임베드가 자동으로 들어갑니다)
- 소제목(3~5개): <h1 style="font-size:22px; font-weight:700; color:#1a1a1a; margin:40px 0 14px 0; padding-bottom:8px; border-bottom:2px solid #1a5490;">
- 본문 문단: <p style="margin:0 0 18px 0;">
- 본문 속 출처 링크: <a href="주소" target="_blank" style="color:#1a5490; text-decoration:underline;">매체명 보도</a>
- 어려운 용어는 최소 2회 설명 박스로:
  <div style="background:#f7f9fb; border-left:3px solid #1a5490; padding:14px 18px; margin:0 0 24px 0; font-size:15px; color:#444;"><strong style="color:#1a5490;">용어</strong>란? 설명</div>
- 마지막에 순서대로 넣으세요:
  (1) <h1 ...22px...>참고 자료</h1> 와 <ol style="margin:0 0 26px 0; padding-left:20px; font-size:15px; color:#555; line-height:1.9;"> 안에 검색한 출처 3개 이상
      각 항목 형식: <li>매체명, <a href="주소" target="_blank" style="color:#1a5490;">「기사 제목」</a>, 날짜</li>
  (2) <p style="font-size:13px; color:#999; background:#fafafa; padding:14px 16px; border-radius:6px; margin:0 0 26px 0;">본 글은 정보 제공을 목적으로 작성되었으며 특정 투자를 권유하지 않습니다. 수치는 작성 시점 보도를 기준으로 하며 이후 변동될 수 있습니다. 투자 판단과 책임은 투자자 본인에게 있습니다.</p>
  (3) <p style="font-size:14px; color:#1a5490; margin:0;">#해시태그 6~8개</p>
- 표, 이미지, 스크립트는 넣지 마세요.

[네이버용 문체]
- "안녕하세요..!" 로 시작해 독자에게 말 거는 구어체.
- 한 문장이 끝나면 줄바꿈을 자주 넣어 호흡을 짧게. 문단은 2~4줄.
- 강조는 "..!" 정도만. 이모지와 HTML 태그는 쓰지 않습니다.
- 숫자와 출처는 "(매체명, 날짜)" 형태로 문장 안에 자연스럽게 넣습니다.
- 마지막은 담백한 마무리 인사 한 줄.
"""


def slugify(title):
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]", "", title).strip()
    name = re.sub(r"\s+", "_", name)
    return name[:80] or "무제"


def split_sections(text):
    if MARK_TITLE not in text or MARK_HTML not in text or MARK_NAVER not in text:
        raise ValueError("응답에서 구획 마커를 찾지 못했습니다.")
    _, rest = text.split(MARK_TITLE, 1)
    title, rest = rest.split(MARK_HTML, 1)
    html, naver = rest.split(MARK_NAVER, 1)
    return title.strip(), html.strip(), naver.strip()


def clean_html(html):
    # 모델이 코드펜스를 붙였을 경우 제거
    html = re.sub(r"^```(?:html)?\s*", "", html)
    html = re.sub(r"\s*```$", "", html)
    start = html.find("<div")
    end = html.rfind("</div>")
    if start == -1 or end == -1:
        raise ValueError("HTML 본문에서 <div> 블록을 찾지 못했습니다.")
    return html[start : end + len("</div>")]


def generate(entry, today, today_ko):
    prompt = PROMPT.format(
        channel=CHANNEL_NAME,
        title=entry["title"],
        published=entry.get("published", ""),
        url=entry["url"],
        description=(entry.get("description") or "(설명 없음)")[:3000],
        today=today,
        today_ko=today_ko,
        mark_title=MARK_TITLE,
        mark_html=MARK_HTML,
        mark_naver=MARK_NAVER,
        embed_token=EMBED_TOKEN,
    )
    if not USE_VIDEO:
        return _finish(
            gemini_client.generate(prompt, max_output_tokens=12000, use_search=True, timeout=600),
            entry,
        )

    try:
        raw = gemini_client.generate(
            prompt,
            video_url=entry["url"],
            max_output_tokens=12000,
            use_search=True,
            timeout=600,
        )
    except gemini_client.QuotaError:
        # 영상 분석은 요청이 무거워 한도에 먼저 걸린다. 제목·설명만으로 다시 시도한다.
        print("[warn] 한도 때문에 영상 분석을 건너뛰고 제목·설명만으로 작성합니다.")
        raw = gemini_client.generate(
            prompt,
            max_output_tokens=12000,
            use_search=True,
            timeout=600,
        )
    return _finish(raw, entry)


def _finish(raw, entry):
    title, html, naver = split_sections(raw)
    html = clean_html(html)

    embed = EMBED_HTML.format(
        video_id=entry["id"],
        title=entry["title"].replace('"', "&quot;"),
        url=entry["url"],
        channel=CHANNEL_NAME,
    )
    if EMBED_TOKEN in html:
        html = html.replace(EMBED_TOKEN, embed)
    else:
        # 모델이 자리표시자를 빠뜨렸으면 첫 문단 뒤에 직접 넣는다.
        marker = "</p>"
        idx = html.find(marker, html.find(marker) + 1)
        insert_at = idx + len(marker) if idx != -1 else len(html) - len("</div>")
        html = html[:insert_at] + "\n\n" + embed + "\n" + html[insert_at:]

    return title.strip(), html, naver


def write_files(out_dir, title, html, naver, video_id=""):
    os.makedirs(out_dir, exist_ok=True)
    slug = slugify(title)
    # 같은 날 제목이 겹치면 덮어쓰지 않도록 영상 ID를 덧붙인다.
    if video_id and os.path.exists(os.path.join(out_dir, f"{slug}.html")):
        slug = f"{slug}_{video_id}"
    paths = {
        "html": os.path.join(out_dir, f"{slug}.html"),
        "source": os.path.join(out_dir, f"{slug}_붙여넣기용_소스.txt"),
        "naver": os.path.join(out_dir, f"{slug}_네이버용.txt"),
    }
    with open(paths["html"], "w", encoding="utf-8") as f:
        f.write(HTML_SHELL.format(title=title, body=html))
    with open(paths["source"], "w", encoding="utf-8") as f:
        f.write(html + "\n")
    with open(paths["naver"], "w", encoding="utf-8") as f:
        f.write(naver + "\n")
    return paths


def main():
    if not gemini_client.available():
        print("[skip] GEMINI_API_KEY가 없어 티스토리 자료 생성을 건너뜁니다.")
        return

    try:
        with open(NEW_VIDEOS_FILE, encoding="utf-8") as f:
            entries = json.load(f)
    except FileNotFoundError:
        print("새로 전송된 영상이 없어 생성할 자료가 없습니다.")
        return

    if not entries:
        print("새로 전송된 영상이 없어 생성할 자료가 없습니다.")
        return

    # 앞 단계(요약)와 연달아 호출하면 분당 한도에 걸리기 쉬워 잠깐 쉬어 간다.
    time.sleep(int(os.environ.get("TISTORY_START_DELAY", "20")))

    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    today_ko = now.strftime("%Y년 %-m월 %-d일")
    failures = 0
    quota_blocked = False

    for entry in entries:
        print(f"\n=== 티스토리 자료 생성: {entry['title']}")
        try:
            title, html, naver = generate(entry, today, today_ko)
        except gemini_client.QuotaError as exc:
            # 무료 한도 소진은 흔한 일이라 워크플로 자체를 실패시키지 않는다.
            print(f"[skip] 사용량 한도로 생성하지 못했습니다 ({entry['url']}): {exc}")
            quota_blocked = True
            continue
        except Exception as exc:  # noqa: BLE001 - 한 건 실패가 전체를 막지 않게
            print(f"[error] 생성 실패 ({entry['url']}): {exc}")
            failures += 1
            continue
        paths = write_files(today, title, html, naver, entry["id"])
        for path in paths.values():
            print(f"  생성: {path}")

    if quota_blocked:
        print("[info] 한도가 풀린 뒤 force 옵션으로 다시 실행하면 원고를 만들 수 있습니다.")
    if failures:
        sys.exit(f"{failures}건의 티스토리 자료 생성에 실패했습니다.")


if __name__ == "__main__":
    main()
