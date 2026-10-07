"""새로 올라온 유튜브 영상을 티스토리 포스팅 자료로 만든다.

youtube_kakao.py가 남긴 .new_videos.json을 읽어서 영상마다
  YYYY-MM-DD/<제목>_붙여넣기용_소스.txt   (티스토리 HTML 모드에 그대로 붙여넣는 본문)
  YYYY-MM-DD/<제목>_발행메모.txt          (제목 후보·키워드·태그·검증 출처 — 본문에는 넣지 않는 것들)
을 생성한다. 작성 규칙은 .claude/skills/tisory-posting/SKILL.md 를 따른다.
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
MARK_META = "===META==="
EMBED_TOKEN = "{{VIDEO_EMBED}}"
BLOG_URL = "https://gnoygnaseel.tistory.com/"

# 티스토리 에디터는 줄 앞 공백을 &nbsp;로 바꾸므로 한 줄로, 들여쓰기 없이 둔다.
# 본문에는 외부 링크를 넣지 않는 규칙이라 영상 출처는 텍스트로만 적는다.
EMBED_HTML = (
    '<figure style="margin:0 0 28px 0;"><div style="position:relative; padding-bottom:56.25%; height:0; overflow:hidden;">'
    '<iframe src="https://www.youtube.com/embed/{video_id}" title="{title}" '
    'style="position:absolute; top:0; left:0; width:100%; height:100%; border:0;" '
    'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>'
    '<figcaption style="font-size:13px; color:#888; margin-top:8px;">영상: {channel} 「{title}」</figcaption></figure>'
)

H2_STYLE = "font-size:22px; font-weight:700; color:#1a1a1a; margin:40px 0 14px 0; padding-bottom:8px; border-bottom:2px solid #1a5490;"
H3_STYLE = "font-size:19px; font-weight:700; color:#1a1a1a; margin:28px 0 10px 0;"

# AI가 쓴 요약처럼 읽히는 표현 (스킬 '글의 톤')
AI_PHRASES = (
    "라고 할 수 있습니다", "주목할 만합니다", "시사하는 바가 큽니다", "귀추가 주목됩니다",
    "이처럼", "결론적으로", "종합하면", "다양한 측면에서", "의 중요성이 커지고 있습니다",
)

PROMPT = """당신은 "금융 IT 개발자의 경제 일상" 티스토리 블로그(gnoygnaseel.tistory.com)의 주인입니다.
금융권(저축은행 여신 시스템 운영)에서 일하는 IT 개발자로, 기술 변화가 시장·주식에 주는 영향에 관심이 많고
투자는 방어적으로 하는 사람입니다. 아래 유튜브 영상(미국 증시의 현황과 변동사항을 다루는 채널)을 소재로,
검색으로 사실을 검증해 티스토리에 바로 붙여넣을 수 있는 글을 만들어 주세요.
글은 AI 요약문이 아니라 이 사람이 뉴스를 읽고 정리한 글처럼 읽혀야 합니다.

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
2. 본문을 쓰기 전에 키워드를 정하세요.
   - 메인 키워드 1개: 그 주제를 모르는 사람이 검색창에 실제로 칠 법한 2~4단어 (예: "엔비디아 실적 발표일", "미국 CPI 발표 일정")
   - 보조 키워드 3~5개: 메인 키워드와 함께 검색되는 롱테일. 그중 2~3개는 질문형
   - 글의 각도: 영상 제목·뉴스 헤드라인을 그대로 쓰지 말고 "무엇인지 / 왜 / 어떻게 / 일정 / 조건 / 비교" 중 하나로 바꿔
     몇 달 뒤에도 검색되는 질문에 맞춥니다. 사건 자체는 본문에서 충분히 다룹니다.
3. 각 주제는 '무슨 일이 있었나 → 어디에 어떤 영향인가 → 무엇을 확인해야 하나' 흐름으로 씁니다.
   수치는 영향을 설명하는 데 필요한 만큼만, 검색으로 확인된 것만 씁니다. 확인 못 한 숫자·문장은 아예 빼세요.
   대괄호 자리표시([ ])를 남기지 마세요.
4. 영상 내용을 그대로 받아쓰지 말고 영상이 인용한 원자료를 찾아 확인하세요.
   영상의 주장은 "영상에서는 ~라고 봅니다"처럼 밝히고, 사실과 전망(의견)을 섞지 마세요.
5. 종목명·티커는 영상에서 언급됐거나 검색으로 확인된 것만 씁니다.
6. 오늘 날짜는 {today}입니다.

[출력 형식] 아래 세 구획을 순서대로, 마커를 정확히 그대로 써서 출력하세요.
다른 말(설명, 인사)은 절대 붙이지 마세요.

{mark_title}
(티스토리 제목 입력칸에 넣을 제목 한 줄. 메인 키워드를 맨 앞에, 25~40자, 궁금증 하나를 중심으로.
 "충격"·"역대급" 같은 낚시성 표현, "오늘"·"방금" 같은 속보성 표현 금지)

{mark_html}
(아래 규격을 지킨 HTML 본문. <div>로 시작해 </div>로 끝나야 합니다)

{mark_meta}
(아래 형식의 순수 텍스트. 각 줄 "항목: 내용")
메인 키워드: ...
보조 키워드: 키워드1, 키워드2, ...
제목 후보: 고르지 않은 다른 제목 후보 1~2개를 " / "로 구분
태그: 티스토리 태그 5~8개를 쉼표로 구분 (메인 키워드 포함, # 없이)
검증 출처: 사실 확인에 쓴 출처를 "매체·기관명 | URL" 형식으로 한 줄에 하나씩 (3개 이상)
자료 보정: 영상 내용이 사실과 달라 고친 부분 (없으면 "없음")

[HTML 규격] 티스토리 에디터에 붙여넣으므로 CSS는 전부 인라인 style로 넣습니다.
- **모든 줄은 `<`로 시작합니다. 들여쓰기 금지** (티스토리가 줄 앞 공백을 빈 칸으로 바꿉니다)
- 전체 감싸기:
<div style="max-width:800px; margin:0 auto; font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif; font-size:17px; line-height:1.8; color:#333; word-break:keep-all;">
- 글 제목은 본문에 넣지 않습니다(티스토리 제목 입력칸에 따로 넣습니다). **<h1>은 하나도 쓰지 않습니다.** 제목 아래 기준일 줄도 넣지 않습니다.
- 도입: 첫 문장 80자 안에 메인 키워드를 자연스럽게 넣습니다. 첫 두 문장만 읽어도 "무엇을, 핵심 수치, 기준 시점"이 보이게 씁니다.
  <p>[훅 한 문장]</p>
  <p> </p>
  <p>[반전·배경 2~3줄을 <br>로 이어서]<br>[오늘의 소식을 정리해봤습니다.]</p>
- 도입 바로 뒤 "핵심 요약" 박스(메인 키워드 질문에 대한 직답 3줄. 하단 "한 줄 정리"와 같은 문장을 반복하지 않습니다):
  <div style="background:#f7f9fb; border:1px solid #e1e8ed; border-radius:8px; padding:18px 22px; margin:24px 0 32px 0;"><p style="font-size:16px; font-weight:700; color:#1a5490; margin:0 0 10px 0;">핵심 요약</p><ul style="margin:0; padding-left:20px; font-size:16px;"><li style="margin-bottom:6px;">직답1</li><li style="margin-bottom:6px;">직답2</li><li>직답3</li></ul></div>
- 그 다음 줄에 {embed_token} 이라고만 쓰세요. (영상 임베드가 자동으로 들어갑니다)
- 본문 섹션 소제목은 <h2 style="{h2_style}">. 영상이 다룬 주제 하나당 h2 하나(1~3개).
  첫 번째 h2에는 메인 키워드, 나머지 h2에는 보조 키워드를 넣되 상황이 보이는 문장형으로 짓습니다.
  ("외국인이 반도체만 12조 가까이 던진 9월"처럼. "첫 번째 이슈" 같은 소제목 금지)
- 섹션 안 하위 소제목이 필요할 때만 <h3 style="{h3_style}">
- 핵심 수치가 여럿이면 표: <table style="width:100%; border-collapse:collapse; font-size:15px;"> (열은 4개 이하),
  표 바로 아래 <p style="font-size:13px; color:#888; margin:0 0 26px 0;">자료: 기관명 · 기준 시점</p>
- 어려운 용어는 설명 박스로: <div style="background:#f7f9fb; border-left:3px solid #1a5490; padding:14px 18px; margin:0 0 24px 0; font-size:15px; color:#444;"><b>용어</b>란? 설명</div>
- 마지막에 순서대로 넣으세요:
  (1) <div style="background:#f7f9fb; border:1px solid #e1e8ed; border-radius:8px; padding:22px 24px; margin:40px 0 30px 0;"><p style="font-size:18px; font-weight:700; color:#1a5490; margin:0 0 14px 0;">한 줄 정리</p><ol style="margin:0; padding-left:20px; font-size:16px;"><li>기억할 수치·일정1</li><li>2</li><li>3</li></ol></div>
  (2) <h2 style="{h2_style}">자주 묻는 질문</h2> 과 질문형 보조 키워드로 만든 질문 2~3개.
      형식 <p><b>Q. 질문</b><br>답 2~3문장</p>, 질문 사이 <p> </p>. 본문에서 검증한 사실만 쓰고, "지금 사도 될까요?" 같은 투자 판단 질문은 만들지 않습니다.
  (3) <p style="font-size:13px; color:#666; margin:0 0 8px 0;">금융권 IT 개발자가 공개 자료를 정리한 글입니다 · 수치는 {today_dot} 기준</p>
  (4) <p style="font-size:13px; color:#999; background:#fafafa; padding:14px 16px; border-radius:6px; margin:0 0 26px 0;">본 글은 정보 제공을 목적으로 작성되었으며 특정 투자를 권유하지 않습니다. 수치는 {today_ko} 기준이며 이후 변동될 수 있습니다. 투자 판단과 책임은 투자자 본인에게 있습니다.</p>
- 금지: 외부 사이트 링크(<a href>)와 참고 자료 목록(출처 URL은 META 구획에만), 이미지 태그, 본문 끝 #해시태그 줄,
  <h1>, <style> 블록, <script>, class 속성, 외부 CSS.

[줄바꿈 규칙] 본문은 "덩어리(2~3줄) + 빈 줄"의 반복입니다. 줄바꿈은 <br>, 빈 줄은 <p> </p>.
- 사실 문장 뒤에 그것을 받는 문장(반전 "하지만~", 부연 "그중~", 세부 수치)이 오면 <br>로 붙입니다. 한 덩어리는 3줄 이하.
- 이야기의 초점이 바뀔 때(수치 → 해석, 사건 → 배경)마다 빈 줄 1개.
- 모바일에서 두 줄을 넘길 만큼 긴 문장은 연결어미 뒤 쉼표("~로," "~면서," "~인데요,")에서 <br>로 끊습니다.
- 빈 줄 2개는 ① 빨간 볼드 질문 문장 앞 ② 개발자 코멘트 앞뒤 ③ 결론부에서 목록·일정으로 넘어가기 직전에만.
- <h2> 바로 앞에는 빈 줄 1개, 바로 뒤에는 빈 줄 없이 본문을 시작합니다.

[강조 규칙]
- 빨간 볼드 <b style="color:#ee2323;">문장 전체</b>: 글의 흐름을 뒤집는 질문 문장("그렇다면 정말 10월엔 오를까요?") 또는
  도입부에서 문제를 던지는 문장. **글 전체에서 1개, 많아야 2개.** 단어 하나에 쓰지 않습니다. 앞뒤 빈 줄, 독립된 한 줄.
- 검정 볼드 <b>…</b>: 섹션에서 처음 나오는 제품명·제도명, 전제가 되는 핵심 단어, 목록으로 넘어가는 안내 문장. 섹션당 1~3곳.
- 노란 형광펜 <span style="background-color:#fff3b0;">…</span>: 섹션의 결론을 만드는 숫자·구절만. 섹션당 최대 2곳.

[금융 IT 개발자 시선 코멘트] 글 전체에서 2~3곳, 한 섹션의 사실 설명이 끝나고 다음 <h2>나 "한 줄 정리"로 넘어가기 직전에만.
- 형식: <p> </p><p> </p> 다음 줄에 <p>* 1~2문장(최대 3줄)</p>, 다음 줄에 <p> </p><p> </p>
- 1인칭 의견임이 드러나게 끝맺습니다("~라고 생각합니다", "~게 느껴지더라구요", "현업에 있는 입장에서는 ~").
- 예: 금리·규제 → 여신 시스템의 금리·한도 테이블 변경, 재산정 배치 / AI·반도체 → 망분리·보안 규제로 바로 쓰기 어려운 점 /
  증시 제도 → 배치·마감·장애 대응 시간 / 환율·지표 → 방어적 투자자로서 먼저 확인하는 것(관찰 수준).
- 특정 회사의 내부 사정·수치를 지어내지 않고 회사명을 쓰지 않습니다. 출처 없는 숫자, 형광펜, 빨간 볼드를 넣지 않습니다.
- "매수 기회", "담아둘 만하다", "비중을 늘리겠다" 같은 투자 권유로 읽히는 말 금지. "실적 발표를 확인하고 판단할 것 같습니다"처럼 관찰·확인 위주로.

[글쓰기 규칙]
- 분량: 도입+본문 섹션의 순수 글자수(공백·표·핵심 요약·FAQ 제외) 800~1,500자. 읽는 데 3분을 넘지 않게.
- 대상 독자는 그 주제를 잘 모르는 일반인. 전문 용어는 처음 나올 때 괄호로 풀어 줍니다 (예: "관세(수입품에 매기는 세금)").
- 정보 제공형 존댓말. 어미는 "~했습니다", "~인데요,", "~정리해봤습니다", "~라는 분석입니다", "~로 풀이됩니다".
  단락 첫머리 연결어는 "그런데", "하지만", "그럼에도", "그래서", "상황이 이렇다 보니".
- 쓰지 말 것: {ai_phrases}. 같은 문장 틀("OO에 따르면 ~했습니다")을 세 번 이상 연달아 쓰지 않습니다.
  기관·매체명은 "한국은행은 ~라고 밝혔습니다"처럼 텍스트로, 한 섹션에 한 번이면 충분합니다.
- 기사 문장을 그대로 옮기지 않습니다. 사실만 가져오고 문장은 새로 씁니다.
- 시점에 따라 달라지는 정보에는 기준일을 함께 적습니다("9월 23일 코스피는~").

[출처 규칙] 아래 순서로 강한 출처를 우선합니다.
1. 정부·공공기관 원자료 (연준, 미 상무부, SEC, BLS, 한국은행, 통계청 등)
2. 기업 공식 발표 (IR 자료, 공시, 보도자료)
3. 주요 언론사 보도 (Reuters, Bloomberg, WSJ, 연합뉴스, 한국경제 등)
4. 증권사 리포트·전문가 코멘트 (의견임을 명시)
- 블로그·커뮤니티·유튜브 요약글은 근거로 쓰지 않습니다.
- 확정 발표와 추정치를 반드시 구분해 씁니다.
"""


def slugify(title):
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]", "", title).strip()
    name = re.sub(r"\s+", "_", name)
    return name[:80] or "무제"


def split_sections(text):
    if MARK_TITLE not in text or MARK_HTML not in text or MARK_META not in text:
        raise ValueError("응답에서 구획 마커를 찾지 못했습니다.")
    _, rest = text.split(MARK_TITLE, 1)
    title, rest = rest.split(MARK_HTML, 1)
    html, meta = rest.split(MARK_META, 1)
    return title.strip(), html.strip(), meta.strip()


def parse_meta(meta):
    """META 구획의 "항목: 내용" 줄을 dict로. 검증 출처처럼 여러 줄인 항목은 이어 붙인다."""
    fields = {}
    key = None
    for line in meta.splitlines():
        m = re.match(r"^\s*([가-힣 ]{2,10}):\s*(.*)$", line)
        if m:
            key = m.group(1).strip()
            fields[key] = m.group(2).strip()
        elif key and line.strip():
            fields[key] = (fields[key] + "\n" + line.strip()).strip()
    return fields


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
        mark_meta=MARK_META,
        h2_style=H2_STYLE,
        h3_style=H3_STYLE,
        ai_phrases=", ".join(f'"{p}"' for p in AI_PHRASES),
        today_dot=today.replace("-", "."),
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
    title, html, meta = split_sections(raw)
    html = flatten_lines(clean_html(html))
    meta = parse_meta(meta)

    embed = EMBED_HTML.format(
        video_id=entry["id"],
        title=html_mod.escape(entry["title"]),
        channel=CHANNEL_NAME,
    )
    if EMBED_TOKEN in html:
        html = re.sub(r"(?:<p>\s*)?" + re.escape(EMBED_TOKEN) + r"(?:\s*</p>)?", lambda _: embed, html)
    else:
        # 모델이 자리표시자를 빠뜨렸으면 첫 소제목 앞에 직접 넣는다.
        idx = html.find("<h2")
        insert_at = idx if idx != -1 else len(html) - len("</div>")
        html = html[:insert_at] + embed + "\n" + html[insert_at:]

    problems = validate(html, meta)
    for problem in problems:
        print(f"[check] {problem}")
    if problems:
        print("[warn] 위 항목은 발행 전에 확인이 필요합니다. 파일은 그대로 저장합니다.")

    return title.strip(), html, meta, problems


# --- 결과물 점검 (티스토리 스킬 5단계의 전달 전 확인 목록을 자동화) ---

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


def flatten_lines(html):
    """티스토리 에디터는 줄 앞 공백을 &nbsp;로 바꾼다. 들여쓰기와 빈 줄을 없앤다."""
    lines = [line.strip() for line in html.splitlines()]
    return "\n".join(line for line in lines if line)


def _text(html):
    return html_mod.unescape(re.sub(r"<[^>]+>", "", html))


def _table_problems(html):
    problems = []
    for table in re.findall(r"<table\b.*?</table>", html, re.S):
        rows = re.findall(r"<tr\b.*?</tr>", table, re.S)
        widest = max((len(re.findall(r"<t[hd]\b", row)) for row in rows), default=0)
        if widest > 4:
            problems.append(f"표의 열이 {widest}개입니다 (4개 이하여야 모바일에서 읽힙니다)")
    return problems


def validate(html, meta):
    """스킬의 전달 전 확인 목록. 문제 목록을 돌려준다."""
    problems = []

    # 형식
    if not html.startswith("<div") or not html.rstrip().endswith("</div>"):
        problems.append("붙여넣기용 본문이 <div>로 시작해 </div>로 끝나지 않습니다")
    if any(not line.startswith("<") for line in html.splitlines() if line.strip()):
        problems.append("'<'로 시작하지 않는 줄이 있습니다 (티스토리에서 앞 공백·글자가 깨집니다)")
    for banned, label in (("<style", "<style> 블록"), ("<script", "<script>"), ("class=", "class 속성")):
        if banned in html:
            problems.append(f"{label} 이(가) 들어 있습니다 (티스토리에서 제거되거나 스킨과 충돌합니다)")

    balance = _Balance()
    balance.feed(html)
    problems.extend(balance.problems)
    if balance.stack:
        problems.append(f"닫히지 않은 태그: {', '.join(balance.stack[:5])}")
    problems.extend(_table_problems(html))

    # 제목 태그
    if re.search(r"<h1\b", html):
        problems.append("<h1>이 들어 있습니다 (글 제목은 티스토리 제목 입력칸에, 소제목은 <h2>)")
    opens = len(re.findall(r"<h2\b", html))
    if opens != len(re.findall(r"</h2>", html)):
        problems.append("h2 여는 태그와 닫는 태그 개수가 다릅니다")
    sections = [h for h in re.findall(r"<h2\b[^>]*>(.*?)</h2>", html, re.S) if "자주 묻는 질문" not in h]
    if not sections:
        problems.append("본문 섹션 소제목(h2)이 하나도 없습니다")

    # 링크·이미지
    for href in re.findall(r"""href=["']([^"']*)""", html):
        if not href.startswith(BLOG_URL):
            problems.append(f"외부 링크가 있습니다: {href[:60]}")
    for src in re.findall(r"""<img\b[^>]*src=["']([^"']*)""", html):
        if not src.startswith("data:image/png;base64,"):
            problems.append(f"외부 이미지가 있습니다: {src[:60]}")

    # 내용·구조 (영상 임베드는 유튜브 제목을 그대로 담으므로 텍스트 점검에서 뺀다)
    text = _text(re.sub(r"<figure\b.*?</figure>", "", html, flags=re.S))
    leftovers = re.findall(r"\[[^\]\n]{1,40}\]", text)
    if leftovers:
        problems.append(f"채우지 않은 자리표시가 남아 있습니다: {leftovers[:3]}")
    for needed in ("핵심 요약", "한 줄 정리", "자주 묻는 질문", "금융권 IT 개발자가 공개 자료를 정리한 글입니다", "특정 투자를 권유하지 않습니다"):
        if needed not in html:
            problems.append(f"'{needed}' 부분이 없습니다")
    faq = len(re.findall(r"<b>\s*Q\.", html))
    if not 2 <= faq <= 3:
        problems.append(f"자주 묻는 질문이 {faq}개입니다 (2~3개)")

    red = len(re.findall(r"color:\s*#ee2323", html, re.I))
    if not 1 <= red <= 2:
        problems.append(f"빨간 볼드 문장이 {red}개입니다 (1~2개)")
    if "#e03131" in html.lower():
        problems.append("옛 빨간색 #e03131 이 남아 있습니다 (#ee2323 사용)")
    for i, part in enumerate(re.split(r"<h2\b", html)[1:], 1):
        if part.count("#fff3b0") > 2:
            problems.append(f"{i}번째 섹션의 형광펜이 2곳을 넘습니다")

    comments = re.findall(r"<p>\*\s(.*?)</p>", html, re.S)
    if not 2 <= len(comments) <= 3:
        problems.append(f"개발자 시선 코멘트(* 문단)가 {len(comments)}개입니다 (2~3개)")
    for c in comments:
        if "#fff3b0" in c or "#ee2323" in c:
            problems.append("개발자 시선 코멘트 안에 형광펜·빨간 볼드가 있습니다")

    used = [p for p in AI_PHRASES if p in text]
    if used:
        problems.append(f"AI 티 나는 표현: {', '.join(used)}")
    if re.search(r"(?:^|\s)#[^\s#<]+(?:\s+#[^\s#<]+)+\s*$", text.strip()):
        problems.append("본문 끝에 #태그 줄이 있습니다 (태그는 발행메모에)")

    keyword = meta.get("메인 키워드", "")
    if not keyword:
        problems.append("META에 메인 키워드가 없습니다")
    else:
        lead = re.sub(r"\s+", " ", text).strip()[:80]
        if keyword.replace(" ", "") not in lead.replace(" ", ""):
            problems.append(f"첫 80자 안에 메인 키워드 '{keyword}'가 없습니다")
    if not meta.get("태그"):
        problems.append("META에 태그가 없습니다")
    if not meta.get("검증 출처"):
        problems.append("META에 검증 출처가 없습니다")

    return problems


def build_memo(title, meta, problems, entry):
    lines = [
        f"티스토리 제목 입력칸: {title}",
        f"제목 후보: {meta.get('제목 후보', '-')}",
        f"메인 키워드: {meta.get('메인 키워드', '-')}",
        f"보조 키워드: {meta.get('보조 키워드', '-')}",
        f"태그: {meta.get('태그', '-')}",
        "",
        f"원본 영상: {CHANNEL_NAME} 「{entry['title']}」 {entry['url']}",
        "검증 출처:",
        meta.get("검증 출처", "-"),
        "",
        f"자료 보정: {meta.get('자료 보정', '없음')}",
        "",
        "남은 수작업:",
        "- 관련 내부 글(gnoygnaseel.tistory.com)이 있으면 본문 1~2곳과 '함께 보면 좋은 글' 박스에 직접 연결",
        "- 대표이미지 썸네일(1200x630)을 지정",
        "",
        "붙여넣기: _붙여넣기용_소스.txt 전체 복사 → 티스토리 글쓰기 → 제목 입력 → HTML 모드 → 붙여넣기 → 기본모드",
    ]
    if problems:
        lines += ["", "자동 점검에서 확인이 필요한 항목:"] + [f"- {p}" for p in problems]
    return "\n".join(lines) + "\n"


def write_files(out_dir, title, html, memo, video_id=""):
    os.makedirs(out_dir, exist_ok=True)
    slug = slugify(title)
    # 같은 날 제목이 겹치면 덮어쓰지 않도록 영상 ID를 덧붙인다.
    if video_id and os.path.exists(os.path.join(out_dir, f"{slug}_붙여넣기용_소스.txt")):
        slug = f"{slug}_{video_id}"
    paths = {
        "source": os.path.join(out_dir, f"{slug}_붙여넣기용_소스.txt"),
        "memo": os.path.join(out_dir, f"{slug}_발행메모.txt"),
    }
    with open(paths["source"], "w", encoding="utf-8") as f:
        f.write(html + "\n")
    with open(paths["memo"], "w", encoding="utf-8") as f:
        f.write(memo)
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
            title, html, meta, problems = generate(entry, today, today_ko)
        except gemini_client.QuotaError as exc:
            # 무료 한도 소진은 흔한 일이라 워크플로 자체를 실패시키지 않는다.
            print(f"[skip] 사용량 한도로 생성하지 못했습니다 ({entry['url']}): {exc}")
            quota_blocked = True
            continue
        except Exception as exc:  # noqa: BLE001 - 한 건 실패가 전체를 막지 않게
            print(f"[error] 생성 실패 ({entry['url']}): {exc}")
            failures += 1
            continue
        memo = build_memo(title, meta, problems, entry)
        paths = write_files(today, title, html, memo, entry["id"])
        for path in paths.values():
            print(f"  생성: {path}")

    if quota_blocked:
        print("[info] 한도가 풀린 뒤 force 옵션으로 다시 실행하면 원고를 만들 수 있습니다.")
    if failures:
        sys.exit(f"{failures}건의 티스토리 자료 생성에 실패했습니다.")


if __name__ == "__main__":
    main()
