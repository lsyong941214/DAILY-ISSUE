#!/usr/bin/env python3
"""
티스토리 포스팅용 한글 차트 생성기.

한글 폰트 탐색/등록, 마이너스 기호 깨짐, 출처 표기, 블로그에 어울리는 색상까지
미리 처리해 둔 스크립트다. matplotlib 코드를 매번 새로 짜면 폰트 깨짐을 매번
다시 디버깅하게 되므로 이 스크립트를 쓴다.

사용법
------
    python3 make_chart.py --spec chart_spec.json --out chart.png

스펙(JSON) 형식
--------------
{
  "type":   "line" | "bar" | "hbar" | "pie",   (필수)
  "title":  "서울 아파트 매매가격지수 추이",      (필수)
  "labels": ["2024.01", "2024.04", ...],        (필수) x축 항목 / 파이 조각 이름
  "series": [                                   (필수) pie는 1개만
    {"name": "서울", "values": [100.0, 98.4, ...]},
    {"name": "전국", "values": [100.0, 99.1, ...]}
  ],
  "ylabel": "지수 (2024.01=100)",               (선택)
  "source": "한국부동산원 · 2026.07 기준",       (선택, 강력 권장) 차트 하단 출처
  "unit":   "%",                                (선택) 값 라벨 뒤에 붙는 단위
  "annotate": true,                             (선택) 막대/파이에 값 표시. 기본 true
  "ylim":   [90, 110],                          (선택) y축 범위 고정
  "highlight": 0                                (선택) 강조할 series 인덱스
}

원칙
----
- source 를 비우지 말 것. 출처 없는 차트는 블로그 글의 신뢰를 떨어뜨린다.
- 축 라벨과 단위를 명시할 것. "100"이 만원인지 지수인지 독자는 모른다.
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

# 블로그 본문 배경(흰색)에서 잘 보이고 서로 구분되는 색. 색맹 사용자도 명도 차이로 구분 가능.
PALETTE = ["#1a5490", "#e8743b", "#3d9970", "#9b59b6", "#c0392b", "#7f8c8d"]
GRID = "#e1e8ed"
TEXT = "#333333"
MUTED = "#888888"


def setup_korean_font() -> str:
    """사용 가능한 한글 폰트를 찾아 matplotlib 기본값으로 등록하고 이름을 돌려준다.

    환경마다 설치된 폰트가 달라서 후보를 순서대로 훑는다. 하나도 못 찾으면
    조용히 넘어가지 않고 에러를 낸다 — 한글이 두부(□□□)로 깨진 차트를
    블로그에 올리는 것보다 실패가 낫다.
    """
    preferred = [
        "Noto Sans CJK KR",
        "NanumGothic",
        "Nanum Gothic",
        "Malgun Gothic",
        "AppleGothic",
        "Apple SD Gothic Neo",
        "Noto Sans KR",
        "UnDotum",
    ]

    # .ttc 컬렉션 안에 든 CJK 폰트는 자동 스캔에서 빠질 수 있으므로 직접 등록한다.
    for path in Path("/usr/share/fonts").rglob("*CJK*"):
        if path.suffix.lower() in (".ttc", ".otf", ".ttf"):
            try:
                fm.fontManager.addfont(str(path))
            except Exception:
                pass

    available = {f.name for f in fm.fontManager.ttflist}
    for name in preferred:
        if name in available:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False  # 한글 폰트에서 음수 기호가 깨지는 것 방지
            return name

    korean_like = sorted(n for n in available if any(k in n for k in ("CJK", "Nanum", "Gothic", "Batang")))
    if korean_like:
        plt.rcParams["font.family"] = korean_like[0]
        plt.rcParams["axes.unicode_minus"] = False
        return korean_like[0]

    raise RuntimeError(
        "한글 폰트를 찾지 못했습니다. 차트의 한글이 깨지므로 중단합니다.\n"
        "  Debian/Ubuntu: apt-get install -y fonts-noto-cjk\n"
        "  또는 fonts-nanum 설치 후 다시 실행하세요."
    )


def _decimals(spec: dict) -> int:
    """데이터가 요구하는 소수점 자릿수를 정한다 (0~2).

    고정 자릿수를 쓰면 0.49%와 0.44%가 나란히 '0.5%', '0.4%'로 반올림돼
    막대 길이는 다른데 라벨은 같아 보이는 사고가 난다. 원본 값이 가진
    자릿수를 그대로 살려야 독자가 숫자를 신뢰할 수 있다.
    """
    need = 0
    for s in spec["series"]:
        for v in s["values"]:
            for d in (0, 1, 2):  # 0부터 시작해야 정수 데이터가 "29,172.0"으로 찍히지 않는다
                if round(v, d) == v:
                    need = max(need, d)
                    break
            else:
                need = 2
    return need


def _fmt(v: float, unit: str, decimals: int = 1) -> str:
    # 천 단위 구분 기호는 1,000 이상이면 항상 넣는다. 같은 차트 안에서
    # "17,687"과 "8337"이 섞이면 눈에 거슬리고 자릿수를 잘못 읽게 된다.
    return f"{v:,.{decimals}f}" + unit


def _fmt_axis(v: float, unit: str, decimals: int = 1) -> str:
    """축 눈금용. 데이터 라벨과 달리 의미 없는 뒤쪽 0은 떼서 눈금을 가볍게 유지한다."""
    s = f"{v:,.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s + unit


def _finish(fig, ax, spec: dict, out: Path):
    """제목·출처·여백 등 모든 차트에 공통으로 들어가는 마무리."""
    fig.suptitle(spec["title"], fontsize=16, fontweight="bold", color="#1a1a1a", y=0.97)

    source = spec.get("source", "").strip()
    if source:
        fig.text(0.99, 0.015, f"자료: {source}", ha="right", va="bottom", fontsize=9, color=MUTED)
    else:
        print("경고: source 가 비어 있습니다. 출처 없는 차트는 신뢰를 떨어뜨립니다.", file=sys.stderr)

    fig.tight_layout(rect=[0, 0.04, 1, 0.94])
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _style_axes(ax, spec, value_axis="y"):
    """공통 축 스타일. value_axis 는 수치가 놓이는 축 — 가로 막대는 'x' 다.

    단위 포맷터와 축 라벨을 엉뚱한 축에 걸면 항목 이름이 숫자로 덮여 버리므로
    반드시 방향을 구분해야 한다.
    """
    ax.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT, labelsize=11)

    label = spec.get("ylabel")  # 스펙에서는 방향과 무관하게 '값 축 이름'을 뜻한다
    lim = spec.get("ylim")
    unit = spec.get("unit", "")
    dec = _decimals(spec)

    if value_axis == "x":
        if label:
            ax.set_xlabel(label, fontsize=11, color=MUTED)
        if lim:
            ax.set_xlim(lim)
        if unit:
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: _fmt_axis(v, unit, dec)))
    else:
        if label:
            ax.set_ylabel(label, fontsize=11, color=MUTED)
        if lim:
            ax.set_ylim(lim)
        if unit:
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _fmt_axis(v, unit, dec)))


def draw_line(spec, out):
    fig, ax = plt.subplots(figsize=(9, 5))
    hl = spec.get("highlight")
    for i, s in enumerate(spec["series"]):
        emphasized = hl is None or i == hl
        ax.plot(
            spec["labels"], s["values"],
            marker="o", markersize=5,
            linewidth=2.6 if emphasized else 1.6,
            color=PALETTE[i % len(PALETTE)],
            alpha=1.0 if emphasized else 0.45,
            label=s.get("name", ""),
        )
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    _style_axes(ax, spec)
    if len(spec["series"]) > 1:
        ax.legend(frameon=False, fontsize=11, loc="best")
    if len(spec["labels"]) > 8:
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    _finish(fig, ax, spec, out)


def draw_bar(spec, out, horizontal=False):
    import numpy as np

    labels = spec["labels"]
    series = spec["series"]
    n = len(series)
    idx = np.arange(len(labels))
    width = 0.8 / n
    annotate = spec.get("annotate", True)
    unit = spec.get("unit", "")
    dec = _decimals(spec)

    fig, ax = plt.subplots(figsize=(9, 5 if not horizontal else max(4, 0.6 * len(labels) + 2)))

    for i, s in enumerate(series):
        offset = (i - (n - 1) / 2) * width
        color = PALETTE[i % len(PALETTE)]
        if horizontal:
            bars = ax.barh(idx + offset, s["values"], height=width, color=color, label=s.get("name", ""))
        else:
            bars = ax.bar(idx + offset, s["values"], width=width, color=color, label=s.get("name", ""))
        if annotate:
            # 음수 막대는 라벨을 반대쪽에 붙여야 한다. 그냥 끝점에 두면
            # 막대 안쪽으로 들어가 글자가 배경에 묻힌다.
            for b, v in zip(bars, s["values"]):
                if horizontal:
                    ax.text(b.get_width(), b.get_y() + b.get_height() / 2,
                            (" " if v >= 0 else "") + _fmt(v, unit, dec) + ("" if v >= 0 else " "),
                            va="center", ha="left" if v >= 0 else "right",
                            fontsize=10, color=TEXT)
                else:
                    ax.text(b.get_x() + b.get_width() / 2, b.get_height(), _fmt(v, unit, dec),
                            va="bottom" if v >= 0 else "top", ha="center",
                            fontsize=10, color=TEXT)

    if horizontal:
        ax.set_yticks(idx, labels)
        ax.invert_yaxis()
        ax.grid(axis="x", color=GRID, linewidth=0.8)
    else:
        ax.set_xticks(idx, labels)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        if max(len(str(x)) for x in labels) > 6 or len(labels) > 8:
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    ax.set_axisbelow(True)
    # 값 라벨이 그림 밖으로 잘리지 않도록 값 축에 여유를 준다
    if annotate and not spec.get("ylim"):
        ax.margins(**({"x": 0.14} if horizontal else {"y": 0.12}))
    _style_axes(ax, spec, value_axis="x" if horizontal else "y")
    if n > 1:
        ax.legend(frameon=False, fontsize=11)
    _finish(fig, ax, spec, out)


def draw_pie(spec, out):
    values = spec["series"][0]["values"]
    labels = spec["labels"]
    fig, ax = plt.subplots(figsize=(7.5, 6))
    wedges, _, autotexts = ax.pie(
        values, labels=labels, autopct="%1.1f%%", startangle=90, counterclock=False,
        colors=[PALETTE[i % len(PALETTE)] for i in range(len(values))],
        textprops={"fontsize": 12, "color": TEXT},
        wedgeprops={"edgecolor": "white", "linewidth": 2},
    )
    for t in autotexts:
        t.set_color("white")
        t.set_fontweight("bold")
    ax.axis("equal")
    _finish(fig, ax, spec, out)


DRAWERS = {
    "line": draw_line,
    "bar": lambda s, o: draw_bar(s, o, horizontal=False),
    "hbar": lambda s, o: draw_bar(s, o, horizontal=True),
    "pie": draw_pie,
}


def main():
    p = argparse.ArgumentParser(
        description="티스토리 포스팅용 한글 차트 생성기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--spec", required=True, help="차트 스펙 JSON 파일 경로")
    p.add_argument("--out", required=True, help="출력 PNG 경로")
    args = p.parse_args()

    font = setup_korean_font()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    for key in ("type", "title", "labels", "series"):
        if key not in spec:
            sys.exit(f"스펙에 필수 항목 '{key}' 가 없습니다.")

    ctype = spec["type"]
    if ctype not in DRAWERS:
        sys.exit(f"지원하지 않는 type '{ctype}'. 사용 가능: {', '.join(DRAWERS)}")

    for s in spec["series"]:
        if len(s["values"]) != len(spec["labels"]):
            sys.exit(
                f"series '{s.get('name','')}' 의 값 개수({len(s['values'])})가 "
                f"labels 개수({len(spec['labels'])})와 다릅니다."
            )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    DRAWERS[ctype](spec, out)
    print(f"생성 완료: {out}  (폰트: {font})")


if __name__ == "__main__":
    main()
