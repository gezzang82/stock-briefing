"""
HTML 리포트 생성
- Plotly CDN으로 인터랙티브 원그래프 (도넛)
- 모바일 친화적 반응형 레이아웃
- GitHub Pages로 자동 배포되어 URL로 접근 가능
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from accuracy_tracker import TIER_COLORS, TIER_LABELS

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# https://<owner>.github.io/<repo>/
DASHBOARD_URL = "https://gezzang82.github.io/stock-briefing/"


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=2">
<title>주식 AI 백테스트 리포트</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  :root {{
    --bg: #f7f9fc;
    --card: #ffffff;
    --text: #2c3e50;
    --muted: #7f8c8d;
    --border: #ecf0f1;
    --up: #27ae60;
    --down: #e74c3c;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo',
                 'Noto Sans KR', 'Helvetica Neue', sans-serif;
    max-width: 900px; margin: 0 auto; padding: 1rem;
    background: var(--bg); color: var(--text);
    line-height: 1.5;
  }}
  h1 {{ margin: 0 0 0.3rem; font-size: 1.5rem; }}
  h3 {{ margin: 1.5rem 0 0.5rem; font-size: 1.1rem; }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1.5rem; }}
  .month-card {{
    background: var(--card); border-radius: 14px;
    padding: 1.2rem; margin-bottom: 1.2rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }}
  .month-title {{ font-size: 1.15rem; margin: 0 0 0.6rem; font-weight: 600; }}
  .stats-row {{
    display: flex; flex-wrap: wrap; gap: 0.5rem;
    margin-bottom: 1rem; font-size: 0.85rem;
  }}
  .stat {{
    background: var(--border); padding: 0.4rem 0.7rem; border-radius: 8px;
    color: var(--muted);
  }}
  .stat strong {{ color: var(--text); }}
  .chart {{ width: 100%; height: 380px; min-height: 320px; }}
  table {{
    width: 100%; border-collapse: collapse;
    margin-top: 0.5rem; font-size: 0.85rem;
    background: var(--card); border-radius: 10px; overflow: hidden;
  }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; }}
  th {{ background: #f8f9fa; font-weight: 600; font-size: 0.8rem; color: var(--muted); }}
  td:last-child, th:last-child {{ text-align: right; }}
  td a {{ color: #2980b9; text-decoration: none; }}
  td a:not(.stock-link) {{ border-bottom: 1px dashed #b9d6e8; }}
  td a:hover {{ color: #1c5980; }}
  td.mfe-mae {{ font-size: 0.78rem; white-space: nowrap; line-height: 1.35; }}
  td.mfe-mae .row {{ display: block; }}
  td.stock-col {{ padding: 10px; }}
  td.amount-col {{ vertical-align: middle; font-weight: 600; }}
  /* Stock cell: logo + (sector tag / stock name) */
  .stock-link {{ display: block; text-decoration: none; color: inherit; }}
  .stock-row {{ display: flex; align-items: center; gap: 10px; }}
  .stock-info {{ display: flex; flex-direction: column; gap: 3px; min-width: 0; }}
  .stock-info .stock-name {{
    color: var(--text); font-weight: 500;
    border-bottom: 1px dashed transparent;
  }}
  .stock-link:hover .stock-name {{
    color: #2980b9; border-bottom-color: #2980b9;
  }}
  .sector-tag {{
    display: inline-block; font-size: 0.6rem;
    padding: 1px 7px; border-radius: 8px;
    line-height: 1.4; font-weight: 700;
    letter-spacing: -0.01em;
    /* color and background set inline per sector */
  }}
  .stock-logo, .stock-logo-fb {{
    display: inline-block; width: 36px; height: 36px;
    vertical-align: middle;
    border-radius: 50%; overflow: hidden;
    flex-shrink: 0;
  }}
  .stock-logo {{ background: white; border: 1px solid var(--border); }}
  .stock-logo img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}
  .stock-logo-fb {{
    color: white; font-size: 14px; font-weight: 700;
    text-align: center; line-height: 36px;
  }}
  .show-more-wrap {{ text-align: center; margin-top: 0.8rem; }}
  .show-more-btn {{
    background: white; border: 1px solid var(--border); color: var(--text);
    padding: 0.5rem 1.4rem; border-radius: 8px; cursor: pointer;
    font-size: 0.85rem; font-family: inherit;
  }}
  .show-more-btn:hover {{ background: #f0f3f5; border-color: #d0d7de; }}
  .empty {{
    color: var(--muted); padding: 2rem; text-align: center;
    font-size: 0.9rem;
  }}
  .up {{ color: var(--up); font-weight: 600; }}
  .down {{ color: var(--down); font-weight: 600; }}
  .table-wrap {{ overflow-x: auto; }}
  @media (max-width: 480px) {{
    body {{ padding: 0.7rem; }}
    h1 {{ font-size: 1.3rem; }}
    .month-card {{ padding: 1rem; }}
    .chart {{ height: 340px; }}
  }}
</style>
</head>
<body>
  <h1>📊 주식 AI 백테스트 리포트</h1>
  <p class="meta">
    최종 업데이트: {updated_at} KST<br>
    평가 기준: 추천일 +14일 시점 수익률 (8개 티어)<br>
    <a href="./workflow.html" style="color:#3498db;text-decoration:none;border-bottom:1px dashed #3498db">
    🔧 시스템 워크플로우 보기 →</a>
  </p>

{body}

<script>
function logoFail(img) {{
  var span = document.createElement('span');
  span.className = 'stock-logo-fb';
  span.style.background = img.dataset.bg;
  span.textContent = img.dataset.letter;
  img.parentElement.replaceChild(span, img);
}}
function showMore(btn, step) {{
  var wrap = btn.parentElement;
  var tbody = wrap.previousElementSibling.querySelector('tbody');
  var hidden = tbody.querySelectorAll('tr[data-hidden="1"]');
  var shown = 0;
  for (var i = 0; i < hidden.length && shown < step; i++) {{
    hidden[i].style.display = '';
    hidden[i].removeAttribute('data-hidden');
    shown++;
  }}
  var remaining = parseInt(btn.dataset.remaining) - shown;
  if (remaining <= 0) {{
    wrap.style.display = 'none';
  }} else {{
    btn.dataset.remaining = remaining;
    btn.textContent = '더보기 (' + remaining + '개 남음)';
  }}
}}
{scripts}
</script>
</body>
</html>
"""


def _naver_url(code: str) -> str:
    """네이버 증권 모바일 종목 페이지"""
    return f"https://m.stock.naver.com/domestic/stock/{code}/total"


# 종목코드별 fallback 아바타 색상 (코드 해시 기반 6종)
_FB_COLORS = ["#3498db", "#9b59b6", "#16a085", "#e67e22", "#e74c3c", "#34495e"]

# 섹터별 태그 컬러 (substring 매칭, 모르는 섹터는 hash로 fallback)
_SECTOR_COLORS = {
    "반도체": "#3498db", "IT": "#9b59b6",  "AI": "#8e44ad",
    "2차전지": "#16a085", "배터리": "#16a085",
    "바이오": "#e74c3c", "제약": "#c0392b", "헬스": "#e67e22",
    "자동차": "#34495e", "운송": "#34495e",
    "화학": "#27ae60", "에너지": "#d35400",
    "금융": "#f39c12", "증권": "#f39c12", "보험": "#d4a017",
    "통신": "#1abc9c", "미디어": "#2980b9", "엔터": "#e91e63",
    "건설": "#7f8c8d", "유통": "#c0392b", "음식료": "#16a085",
    "조선": "#2c3e50", "철강": "#7f8c8d", "방산": "#34495e",
    "소비재": "#9c27b0", "제조": "#607d8b", "소재": "#795548",
    "원자력": "#ff5722", "전력": "#ffc107",
}
_SECTOR_FB_PALETTE = ["#5dade2", "#af7ac5", "#48c9b0", "#f5b041", "#ec7063", "#5d6d7e"]


def _sector_color(sector: str | None) -> str:
    if not sector or sector == "-":
        return "#95a5a6"
    for keyword, color in _SECTOR_COLORS.items():
        if keyword in sector:
            return color
    return _SECTOR_FB_PALETTE[sum(ord(c) for c in sector) % len(_SECTOR_FB_PALETTE)]


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    """#RRGGBB → rgba(R, G, B, alpha)"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _sector_tag(sector: str | None) -> str:
    """진한 글자색 + 같은 색의 연한 배경"""
    if not sector or sector == "-":
        return '<span class="sector-tag" style="color:#7f8c8d;background:#ecf0f1">-</span>'
    color = _sector_color(sector)
    bg = _hex_to_rgba(color, 0.13)
    return f'<span class="sector-tag" style="color:{color};background:{bg}">{sector}</span>'


def _logo_html(code: str, name: str) -> str:
    """
    네이버 공식 종목 로고 img + onerror 시 첫글자 컬러 아바타로 교체.
    네이버 URL: ssl.pstatic.net/imgstock/fn/real/logo/png/stock/Stock{code}.png
    """
    logo_url = f"https://ssl.pstatic.net/imgstock/fn/real/logo/png/stock/Stock{code}.png"
    first = (name[0] if name else "?").replace('"', "")
    bg = _FB_COLORS[sum(ord(c) for c in code) % len(_FB_COLORS)]
    return (
        f'<span class="stock-logo">'
        f'<img src="{logo_url}" alt="" onerror="logoFail(this)" '
        f'data-bg="{bg}" data-letter="{first}">'
        f"</span>"
    )


def _stock_table(items: list[dict], title: str,
                 initial_visible: int = 20, expand_step: int = 10) -> str:
    """수익률 내림차순. 처음 initial_visible개 노출, 나머지는 더보기 버튼으로 펼침."""
    valid = [x for x in items if x.get("latest_return") is not None]
    if not valid:
        return ""
    valid.sort(key=lambda x: x["latest_return"], reverse=True)

    total = len(valid)
    rows = []
    for i, it in enumerate(valid):
        ret = it["latest_return"]
        cls = "up" if ret >= 0 else "down"
        sign = "▲" if ret >= 0 else "▼"
        url = _naver_url(it["stock_code"])
        # initial_visible 이후 행은 숨김
        hidden_attr = ' style="display:none" data-hidden="1"' if i >= initial_visible else ""
        # YYYY-MM-DD → YY.MM.DD
        if len(it["rec_date"]) == 10:
            date_short = it["rec_date"][2:].replace("-", ".")
        else:
            date_short = it["rec_date"]
        logo = _logo_html(it["stock_code"], it["stock_name"])
        sector_tag = _sector_tag(it.get("sector"))
        # 변동폭 2줄
        mfe = it.get("mfe_pct")
        mae = it.get("mae_pct")
        if mfe is not None and mae is not None:
            mfe_mae_cell = (
                f'<span class="row up">▲{mfe:.1f}%</span>'
                f'<span class="row down">▼{abs(mae):.1f}%</span>'
            )
        else:
            mfe_mae_cell = "—"
        rows.append(
            f"<tr{hidden_attr}>"
            f"<td>{date_short}</td>"
            f'<td class="stock-col">'
            f'<a href="{url}" target="_blank" rel="noopener" class="stock-link">'
            f'<div class="stock-row">'
            f"{logo}"
            f'<div class="stock-info">'
            f"<div>{sector_tag}</div>"
            f'<span class="stock-name">{it["stock_name"]}</span>'
            f"</div>"
            f"</div>"
            f"</a>"
            f"</td>"
            f'<td class="amount-col {cls}">{sign}{abs(ret):.2f}%</td>'
            f'<td class="mfe-mae">{mfe_mae_cell}</td>'
            f"</tr>"
        )

    hidden_count = max(0, total - initial_visible)
    button_html = ""
    if hidden_count > 0:
        button_html = (
            f'<div class="show-more-wrap">'
            f'<button class="show-more-btn" onclick="showMore(this, {expand_step})" '
            f'data-remaining="{hidden_count}">'
            f"더보기 ({hidden_count}개 남음)</button>"
            f"</div>"
        )

    return (
        f"<h3>{title} <span style='font-size:0.8rem;color:var(--muted);font-weight:normal'>"
        f"(총 {total}개)</span></h3>"
        f'<p style="font-size:0.8rem;color:var(--muted);margin:0 0 0.3rem">'
        f"수익률 높은 순 · 종목명을 누르면 네이버 증권에서 열립니다 · "
        f"<span class='up'>▲</span>최고상승(MFE) / <span class='down'>▼</span>최대하락(MAE)</p>"
        f'<div class="table-wrap"><table>'
        f"<thead><tr>"
        f"<th>추천일</th><th>종목</th>"
        f"<th>현재 수익률</th><th>변동폭</th>"
        f"</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"</table></div>"
        f"{button_html}"
    )


def _sector_table(sectors: list[dict]) -> str:
    if not sectors:
        return ""
    rows = []
    for s in sectors:
        cls = "up" if s["avg_return"] >= 0 else "down"
        sign = "▲" if s["avg_return"] >= 0 else "▼"
        rows.append(
            f"<tr>"
            f"<td>{s['sector']}</td>"
            f"<td>{s['total']}</td>"
            f'<td class="{cls}">{sign}{abs(s["avg_return"]):.2f}%</td>'
            f"</tr>"
        )
    return (
        f"<h3>섹터별 성과 (최근 30일)</h3>"
        f'<div class="table-wrap"><table>'
        f"<thead><tr><th>섹터</th><th>종목 수</th><th>평균 수익률</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"</table></div>"
    )


def _month_card(stats: dict) -> tuple[str, dict | None]:
    """월별 카드 HTML + 차트 데이터(없으면 None)"""
    month = stats["month"]
    matured = stats["matured_count"]

    badges = []
    if matured:
        cls = "up" if stats["avg_return"] >= 0 else "down"
        badges += [
            f'<div class="stat">만기 <strong>{matured}</strong>개</div>',
            f'<div class="stat">평균 <strong class="{cls}">{stats["avg_return"]:+.2f}%</strong></div>',
            f'<div class="stat">승률 <strong>{stats["win_rate"]:.0f}%</strong></div>',
            f'<div class="stat">강승(≥5%) <strong>{stats["strong_win_rate"]:.0f}%</strong></div>',
        ]
        if stats.get("avg_mfe", 0) or stats.get("avg_mae", 0):
            badges += [
                f'<div class="stat">평균 MFE <strong class="up">▲{stats["avg_mfe"]:.2f}%</strong></div>',
                f'<div class="stat">평균 MAE <strong class="down">▼{abs(stats["avg_mae"]):.2f}%</strong></div>',
            ]
    if stats["in_progress_count"]:
        badges.append(
            f'<div class="stat">진행 중 {stats["in_progress_count"]}개</div>'
        )
    if not badges:
        badges.append(f'<div class="stat">추천 없음</div>')

    chart_id = f"chart_{month.replace('-', '_')}"
    chart_data = None

    if matured > 0:
        labels, values, colors = [], [], []
        for i, c in enumerate(stats["tier_counts"]):
            if c > 0:
                labels.append(TIER_LABELS[i])
                values.append(c)
                colors.append(TIER_COLORS[i])
        chart_data = {"id": chart_id, "labels": labels, "values": values, "colors": colors}
        chart_html = f'<div id="{chart_id}" class="chart"></div>'
    else:
        chart_html = (
            '<div class="empty">아직 만기 도달 종목 없음<br>'
            "(추천일로부터 14일 경과 시 평가)</div>"
        )

    html = (
        f'<div class="month-card">'
        f'<div class="month-title">{month}</div>'
        f'<div class="stats-row">{"".join(badges)}</div>'
        f"{chart_html}"
        f"</div>"
    )
    return html, chart_data


def _plotly_script(chart: dict) -> str:
    """Plotly 도넛 차트 JS 코드"""
    return f"""
Plotly.newPlot({json.dumps(chart['id'])}, [{{
  type: 'pie',
  labels: {json.dumps(chart['labels'], ensure_ascii=False)},
  values: {json.dumps(chart['values'])},
  marker: {{colors: {json.dumps(chart['colors'])}, line: {{color: 'white', width: 2}}}},
  textinfo: 'label+percent',
  textposition: 'outside',
  hovertemplate: '<b>%{{label}}</b><br>%{{value}}종목 (%{{percent}})<extra></extra>',
  hole: 0.45,
  sort: false
}}], {{
  showlegend: true,
  legend: {{orientation: 'h', y: -0.05, font: {{size: 11}}}},
  margin: {{t: 10, b: 30, l: 10, r: 10}},
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: {{family: 'inherit'}}
}}, {{responsive: true, displayModeBar: false}});
"""


def generate_html_report(
    monthly_stats: list[dict],
    week_items: list[dict],
    sectors: list[dict],
    output_path: str | Path,
):
    """월별 통계 + 이번 주 종목 + 섹터를 HTML 한 페이지로 생성"""
    # 최신 월 먼저
    monthly_sorted = sorted(monthly_stats, key=lambda m: m["month"], reverse=True)

    body_parts: list[str] = []
    chart_datas: list[dict] = []

    if not monthly_sorted:
        body_parts.append('<div class="empty">아직 추천 데이터 없음</div>')
    else:
        for ms in monthly_sorted:
            html, chart = _month_card(ms)
            body_parts.append(html)
            if chart:
                chart_datas.append(chart)

    if week_items:
        body_parts.append(_stock_table(week_items, "이번 주 추천 종목 성과"))

    if sectors:
        body_parts.append(_sector_table(sectors))

    scripts = "\n".join(_plotly_script(c) for c in chart_datas)

    html = HTML_TEMPLATE.format(
        updated_at=datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        body="\n".join(body_parts),
        scripts=scripts,
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    logger.info("HTML 리포트 생성: %s (%d bytes)", output, output.stat().st_size)

    # 워크플로우 시각화 페이지도 같이 생성
    try:
        from workflow_page import generate_workflow_page
        workflow_out = output.parent / "workflow.html"
        generate_workflow_page(workflow_out)
    except Exception as e:
        logger.warning("워크플로우 페이지 생성 실패 (메인 리포트는 정상): %s", e)
