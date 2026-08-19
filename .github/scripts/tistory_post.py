"""새로 올라온 유튜브 영상을 티스토리 포스팅 자료로 만든다.

youtube_kakao.py가 남긴 .new_videos.json을 읽어서 영상마다
  YYYY-MM-DD/<제목>.html                   (미리보기 + 티스토리 HTML 모드용)
  YYYY-MM-DD/<제목>_붙여넣기용_소스.txt      (에디터에 그대로 붙여넣는 본문)
  YYYY-MM-DD/<제목>_네이버용.txt             (네이버 블로그용 문체로 재작성)
을 생성한다. 기존 경제 이슈 자료와 같은 폴더 규칙·스타일을 따른다.
"""

import html as html_mod
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import gemini_client

NEW_VIDEOS_FILE = os.environ.get("NEW_VIDEOS_FILE", ".new_videos.json")
CHANNEL_NAME = os.environ.get("YOUTUBE_CHANNEL_NAME", "소수몽키")
# 영상을 직접 분석(재생)하면 입력 토큰을 훨씬 많이 써서 한도(429)에 먼저 걸린다.
# 무료 티어 한도가 넉넉한 계정만 명시적으로 켜도록 기본값은 꺼둔다.
USE_VIDEO = os.environ.get("TISTORY_USE_VIDEO", "0").lower() not in ("0", "false", "no")
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

PROMPT = """당신은 미국 주식 이슈를 정리하는 한국어 블로그 작성자입니다.
아래 유튜브 영상(미국 증시의 현황과 변동사항을 다루는 채널)을 소재로,
티스토리에 바로 올릴 수 있는 포스팅 자료를 만들어 주세요.

[영상 정보]
- 채널: {channel}
- 제목: {title}
- 업로드: {published}
- 링크: {url}
- 설명란:
{description}
{transcript_block}

[작업 지침]
1. {source_instruction}
2. 글은 **'무슨 일이 있었나 → 어디에 어떤 영향인가 → 투자자는 무엇을 봐야 하나'**
   흐름으로 구성하세요. 이 세 가지가 독자가 알고 싶어 하는 전부입니다.
   - 어느 기업·국가·기관이 어떤 발표나 결정을 했는지 사실을 먼저 짚습니다.
   - 그 사건이 어떤 섹터·종목의 주가에 어떤 방향으로 작용하는지, 왜 그런지 설명합니다.
   - 마지막에 투자자가 지금 지켜봐야 할 지표나 일정, 판단 기준을 정리합니다.
3. 숫자를 나열하는 글이 되지 않게 하세요. 수치는 영향을 설명하는 데 필요한 만큼만 쓰고,
   쓸 때는 검색으로 확인된 것만 씁니다. 확인되지 않은 숫자는 아예 쓰지 마세요.
   출처는 3개 이상 확보하세요.
4. 영상 내용을 그대로 받아쓰지 말고, 독자가 스스로 판단할 수 있도록 배경을 채우세요.
   영상의 주장은 "영상에서는 ~라고 봅니다"처럼 출처를 밝혀 인용하고,
   사실과 전망(의견)을 섞지 마세요.
5. 종목명·티커는 영상에서 언급됐거나 검색으로 확인된 것만 씁니다.
6. 오늘 날짜는 {today}입니다.

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
- 글 대제목(맨 위 1개): <h1 style="font-size:28px; font-weight:700; line-height:1.4; color:#1a1a1a; margin:0 0 8px 0;">
- 제목 아래 기준일 줄: <p style="font-size:14px; color:#888; margin:0 0 28px 0;">{today_ko} 기준 · {channel} 영상 정리</p>
- 도입 2~3문장: <p style="margin:0 0 28px 0;">  (왜 지금 이 주제인지)
- 그 다음 줄에 {embed_token} 이라고만 쓰세요. (영상 임베드가 자동으로 들어갑니다)
- 본문 섹션 소제목(3~5개, 위 2번의 흐름이 드러나게):
  <h1 style="font-size:22px; font-weight:700; color:#1a1a1a; margin:40px 0 14px 0; padding-bottom:8px; border-bottom:2px solid #1a5490;">
- 본문 문단: <p style="margin:0 0 18px 0;">
- 본문 속 출처 링크: <a href="주소" target="_blank" style="color:#1a5490; text-decoration:underline;">매체명 보도</a>
- 어려운 용어는 최소 2회 설명 박스로:
  <div style="background:#f7f9fb; border-left:3px solid #1a5490; padding:14px 18px; margin:0 0 24px 0; font-size:15px; color:#444;"><strong style="color:#1a5490;">용어</strong>란? 설명</div>
- 핵심 수치가 여럿이면 표를 써도 됩니다. 단 <table style="width:100%; border-collapse:collapse;">이고 **열은 4개 이하**입니다.
- 마지막에 순서대로 넣으세요:
  (1) "한 줄 정리" 박스 — 기억할 핵심 3가지:
      <div style="background:#f7f9fb; border-left:3px solid #1a5490; padding:16px 18px; margin:0 0 26px 0;"><strong style="color:#1a5490;">한 줄 정리</strong><br>· 핵심1<br>· 핵심2<br>· 핵심3</div>
  (2) 부록 소제목 <h1 style="font-size:20px; font-weight:700; color:#1a1a1a; margin:38px 0 14px 0; padding-bottom:8px; border-bottom:1px solid #e1e8ed;">참고 자료</h1>
      와 <ol style="margin:0 0 26px 0; padding-left:20px; font-size:15px; color:#555; line-height:1.9;"> 안에 검색한 출처 3개 이상
      각 항목 형식: <li>매체명, <a href="주소" target="_blank" style="color:#1a5490;">「기사 제목」</a>, 날짜</li>
  (3) <p style="font-size:13px; color:#999; background:#fafafa; padding:14px 16px; border-radius:6px; margin:0 0 26px 0;">본 글은 정보 제공을 목적으로 작성되었으며 특정 투자를 권유하지 않습니다. 수치는 작성 시점 보도를 기준으로 하며 이후 변동될 수 있습니다. 투자 판단과 책임은 투자자 본인에게 있습니다.</p>
  (4) <p style="font-size:14px; color:#1a5490; margin:0;">#해시태그 5~8개</p>
- 금지: <h2> 등 다른 제목 태그, <style> 블록, <script>, class 속성, 외부 CSS, 이미지 태그.
  소제목은 전부 <h1>이고 위계는 인라인 font-size로만 줍니다.

[글쓰기 규칙]
- 분량은 본문 1,500~2,500자.
- 한 문단은 3~4줄 이내. 독자 대부분이 모바일로 봅니다.
- 소제목은 사람이 실제로 검색창에 칠 법한 키워드를 앞쪽에 넣어 짓습니다.
  "첫 번째 이슈" 같은 무의미한 소제목은 쓰지 않습니다. 키워드를 부자연스럽게 반복하지도 않습니다.
- 대상 독자는 그 주제를 잘 모르는 일반인입니다. 전문 용어는 처음 나올 때 괄호로 풀어 줍니다
  (예: "관세(수입품에 매기는 세금)").
- 정보 제공형 존댓말. 낚시성 제목, 과장, "충격"·"역대급" 같은 표현은 쓰지 않습니다.
- 기사 문장을 그대로 옮기지 않습니다. 사실만 가져오고 문장은 새로 씁니다.
- 시점에 따라 달라지는 정보에는 기준일을 함께 적습니다.

[출처 규칙] 아래 순서로 강한 출처를 우선합니다.
1. 정부·공공기관 원자료 (연준, 미 상무부, SEC, BLS, 한국은행, 통계청 등)
2. 기업 공식 발표 (IR 자료, 공시, 보도자료)
3. 주요 언론사 보도 (Reuters, Bloomberg, WSJ, 연합뉴스, 한국경제 등)
4. 증권사 리포트·전문가 코멘트 (의견임을 명시)
- 블로그·커뮤니티·유튜브 요약글은 출처로 쓰지 않습니다.
- **이 글의 출발점이 유튜브 영상이더라도, 글에는 그 영상이 인용한 원자료를 찾아서 답니다.**
- 출처를 못 찾은 문장은 글에서 뺍니다. 확정 발표와 추정치를 반드시 구분해 씁니다.

[네이버용 문체] 순수 텍스트입니다. HTML 태그·style·색상 강조를 쓰지 않습니다.
- "안녕하세요..!" 로 시작해 독자에게 말 거는 구어체. 개인적인 상황·감정을 먼저 꺼낸 뒤 주제로 이어갑니다.
- 한두 문장마다 줄을 바꿉니다. 티스토리처럼 문단으로 묶지 말고 훨씬 자주 개행합니다.
- 솔직하고 다소 자조적인 어투("저는 ~했습니다", "~더라구요"). 강조는 독립된 한 줄이나 "..!!" 로만 줍니다.
- 노래 가사나 남의 글을 인용하지 않습니다.
- 사실관계·수치·출처는 티스토리 버전과 똑같이 유지합니다. 문체와 구성만 바꿉니다.
- 출처는 "(매체명, 날짜, https://...)" 처럼 URL을 텍스트 그대로 남깁니다.
- HTML 엔티티(&middot; &lsquo; 등)를 쓰지 말고 실제 문자(·, ')로 씁니다.
- 영상 링크는 텍스트로 남깁니다. 이미지 자리표시는 넣지 않습니다.
- 분량 1,200~2,000자. 하단에 참고 자료 출처와 투자 권유가 아니라는 문구를 부드러운 말투로 남깁니다.
- 글 말미에 독자에게 말을 걸며 마무리합니다.
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
    transcript = (entry.get("transcript") or "").strip()
    if transcript:
        transcript_block = f"- 영상 자막(발화 내용):\n{transcript[:15000]}"
        source_instruction = (
            "위 자막이 영상의 실제 발화 내용입니다. 자막을 근거로 어떤 주장을 "
            "어떤 이유로 펴는지 파악하세요. 자막에 없는 내용을 영상이 말했다고 쓰지 마세요."
        )
    elif USE_VIDEO:
        transcript_block = ""
        source_instruction = "첨부된 영상을 직접 보고, 어떤 주장을 어떤 근거로 펴는지 파악하세요."
    else:
        transcript_block = ""
        source_instruction = (
            "자막도 영상도 없이 제목과 설명란만 있습니다. 설명란에 없는 발언·수치를 "
            "영상이 말했다고 지어내지 말고, 제목과 설명란이 가리키는 주제에 대해 "
            "검색으로 확인되는 사실관계만으로 정리하세요."
        )

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
        transcript_block=transcript_block,
        source_instruction=source_instruction,
    )
    if transcript:
        # 자막이 있으면 영상을 첨부할 필요가 없다. 토큰을 훨씬 적게 쓴다.
        return _finish(
            gemini_client.generate(prompt, max_output_tokens=12000, use_search=True, timeout=600),
            entry,
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
    html = enforce_h1(clean_html(html))
    naver = clean_naver(naver)

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

    problems = validate(html, naver)
    for problem in problems:
        print(f"[check] {problem}")
    if problems:
        print("[warn] 위 항목은 발행 전에 확인이 필요합니다. 파일은 그대로 저장합니다.")

    return title.strip(), html, naver


# --- 결과물 점검 (티스토리 스킬의 확인 목록을 그대로 자동화) ---

SELF_CLOSING = {"br", "img", "hr", "meta", "input", "source"}


class _Balance(HTMLParser):
    """열고 닫는 태그가 맞는지 확인한다."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.problems = []

    def handle_starttag(self, tag, attrs):
        if tag not in SELF_CLOSING:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in SELF_CLOSING:
            return
        if not self.stack:
            self.problems.append(f"닫는 </{tag}> 가 여는 태그보다 많습니다")
        elif self.stack[-1] != tag:
            self.problems.append(f"<{self.stack[-1]}> 가 닫히기 전에 </{tag}> 가 나왔습니다")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def enforce_h1(html):
    """소제목은 전부 <h1>이어야 한다. 눈으로 고치면 반드시 하나를 놓친다."""
    fixed = re.sub(r"<h[2-6]\b", "<h1", html)
    fixed = re.sub(r"</h[2-6]>", "</h1>", fixed)
    if fixed != html:
        print("[info] h2~h6 소제목을 h1으로 바꿨습니다.")
    return fixed


def clean_naver(text):
    """네이버용은 순수 텍스트. 엔티티와 남은 태그를 정리한다."""
    cleaned = html_mod.unescape(text or "")
    if re.search(r"</?[a-zA-Z][^>]*>", cleaned):
        cleaned = re.sub(r"</?[a-zA-Z][^>]*>", "", cleaned)
        print("[info] 네이버용에서 HTML 태그를 제거했습니다.")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _table_problems(html):
    problems = []
    for table in re.findall(r"<table\b.*?</table>", html, re.S):
        rows = re.findall(r"<tr\b.*?</tr>", table, re.S)
        widest = max((len(re.findall(r"<t[hd]\b", row)) for row in rows), default=0)
        if widest > 4:
            problems.append(f"표의 열이 {widest}개입니다 (4개 이하여야 모바일에서 읽힙니다)")
    return problems


def validate(html, naver):
    """스킬의 전달 전 확인 목록. 문제 목록을 돌려준다."""
    problems = []

    if not html.startswith("<div") or not html.rstrip().endswith("</div>"):
        problems.append("붙여넣기용 본문이 <div>로 시작해 </div>로 끝나지 않습니다")
    if re.search(r"</?h[2-6]\b", html):
        problems.append("h2~h6 소제목이 남아 있습니다")

    opens = len(re.findall(r"<h1\b", html))
    closes = len(re.findall(r"</h1>", html))
    if opens != closes:
        problems.append(f"h1 여는 태그 {opens}개, 닫는 태그 {closes}개로 개수가 다릅니다")
    if opens == 0:
        problems.append("소제목(h1)이 하나도 없습니다")

    for banned, label in (("<style", "<style> 블록"), ("<script", "<script>"), ("class=", "class 속성")):
        if banned in html:
            problems.append(f"{label} 이(가) 들어 있습니다 (티스토리에서 제거되거나 스킨과 충돌합니다)")

    balance = _Balance()
    balance.feed(html)
    problems.extend(balance.problems)
    if balance.stack:
        problems.append(f"닫히지 않은 태그: {', '.join(balance.stack[:5])}")

    problems.extend(_table_problems(html))

    leftovers = [m for m in re.findall(r"\[[^\]\n]{2,30}\]", html) if "이미지" not in m]
    if leftovers:
        problems.append(f"채우지 않은 자리표시가 남아 있습니다: {leftovers[:3]}")

    if re.search(r"</?[a-zA-Z][^>]*>", naver):
        problems.append("네이버용에 HTML 태그가 남아 있습니다")
    if "style=" in naver:
        problems.append("네이버용에 style 속성이 남아 있습니다")
    if re.search(r"&[a-zA-Z]+;|&#\d+;", naver):
        problems.append("네이버용에 HTML 엔티티가 남아 있습니다")

    return problems


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
