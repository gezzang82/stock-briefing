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
    --bg: #f6f7f9;
    --card: #ffffff;
    --text: #1d2433;
    --muted: #8b95a1;
    --border: #eef0f3;
    --separator: #e5e8ec;
    --pill-bg: #f1f3f5;
    --tab-active: #1d2433;
    --link: #2563eb;
    --up: #16a34a;
    --down: #dc2626;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo',
                 'Pretendard', 'Noto Sans KR', 'Helvetica Neue', sans-serif;
    max-width: 760px; margin: 0 auto; padding: 1.2rem 1rem;
    background: var(--bg); color: var(--text);
    line-height: 1.5;
    -webkit-text-size-adjust: 100%;
    overflow-x: hidden;  /* 가로 스크롤 방지 (잘못된 요소 폭 보호) */
  }}
  /* ── Header ── */
  h1 {{ margin: 0 0 0.4rem; font-size: 1.575rem; font-weight: 700; letter-spacing: -0.01em; }}
  .meta {{
    color: var(--muted); font-size: 0.945rem;
    margin: 0 0 1.5rem; padding: 0 0 0 1.1rem;
  }}
  .meta li {{ margin: 0.1rem 0; }}
  .workflow-link {{ color: var(--link); text-decoration: none; font-weight: 500; }}
  .workflow-link:hover {{ text-decoration: underline; }}
  /* ── Filter bar (year + month tabs) ── */
  .filter-bar {{
    display: flex; align-items: center; gap: 0.5rem;
    margin-bottom: 1rem;
    overflow-x: auto; -webkit-overflow-scrolling: touch;
  }}
  .filter-bar::-webkit-scrollbar {{ display: none; }}
  .year-selector {{
    background: white; border: 1px solid var(--border);
    padding: 0.5rem 0.9rem; border-radius: 999px;
    font-size: 1.005rem; font-weight: 500; color: var(--text);
    cursor: pointer; font-family: inherit;
    appearance: none; -webkit-appearance: none;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24'><path fill='%231d2433' d='M7 10l5 5 5-5z'/></svg>");
    background-repeat: no-repeat; background-position: right 0.7rem center;
    padding-right: 2rem;
  }}
  .month-tab {{
    background: transparent; border: none;
    padding: 0.5rem 0.95rem; border-radius: 999px;
    font-size: 1.005rem; font-weight: 500; color: var(--muted);
    cursor: pointer; font-family: inherit;
    white-space: nowrap; transition: all 0.15s;
  }}
  .month-tab:hover {{ color: var(--text); }}
  .month-tab.active {{
    background: var(--tab-active); color: white; font-weight: 600;
  }}
  /* ── Month card ── */
  .month-card {{
    background: var(--card); border-radius: 16px;
    padding: 1.3rem 1.2rem; margin-bottom: 1.2rem;
    box-shadow: 0 1px 2px rgba(0,0,0,0.03);
  }}
  .month-card[hidden] {{ display: none; }}
  .month-title {{ font-size: 1.525rem; font-weight: 700; margin: 0 0 0.25rem; }}
  .month-subtitle {{ font-size: 0.945rem; color: var(--muted); margin: 0 0 1rem; }}
  .stats-grid {{
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem;
    margin-bottom: 0.5rem;
  }}
  .stats-grid.cols-2 {{ grid-template-columns: repeat(2, 1fr); }}
  .stat-cell {{
    background: var(--pill-bg); border-radius: 10px;
    padding: 0.7rem 0.9rem;
    display: flex; align-items: center; justify-content: space-between;
    gap: 0.4rem; font-size: 0.945rem; color: var(--muted);
  }}
  .stat-cell strong {{ color: var(--text); font-weight: 700; font-size: 1.025rem; }}
  .stat-cell strong.up {{ color: var(--up); }}
  .stat-cell strong.down {{ color: var(--down); }}
  .chart {{
    width: 100%; height: 340px; min-height: 300px;
    margin-top: 0.5rem; overflow: hidden;
  }}
  .chart .main-svg {{ background: transparent !important; }}
  .chart-legend {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 0.4rem 0.6rem; margin-top: 0.5rem;
    font-size: 0.825rem; color: var(--muted);
  }}
  .legend-item {{ display: flex; align-items: center; gap: 0.35rem; }}
  .legend-dot {{
    width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0;
  }}
  @media (max-width: 480px) {{
    .chart-legend {{
      grid-template-columns: repeat(2, 1fr) !important;
      font-size: 0.805rem; gap: 0.35rem 0.4rem;
    }}
  }}
  /* ── Table headings ── */
  h3.section-title {{
    margin: 1.8rem 0 0.25rem; font-size: 1.175rem; font-weight: 700;
  }}
  h3.section-title .count {{ color: var(--muted); font-size: 0.975rem; font-weight: 500; }}
  .section-desc {{ font-size: 0.905rem; color: var(--muted); margin: 0 0 0.7rem; }}
  /* ── Tables ── */
  table {{
    width: 100%; border-collapse: collapse;
    margin-top: 0.5rem; font-size: 0.975rem;
    background: var(--card); border-radius: 12px; overflow: hidden;
    table-layout: fixed;
  }}
  table col.col-date {{ width: 68px; }}
  table col.col-stock {{ width: auto; }}
  table col.col-ret {{ width: 100px; }}
  table col.col-range {{ width: 70px; }}
  table col.sector-name {{ width: auto; }}
  table col.sector-count {{ width: 80px; }}
  table col.sector-ret {{ width: 100px; }}
  th, td {{
    padding: 16px 14px; border-bottom: 1px solid var(--border);
    text-align: left; vertical-align: middle;
    position: relative;
  }}
  th {{
    background: white; font-weight: 500; font-size: 0.905rem;
    color: var(--muted); border-bottom: 1px solid var(--separator);
    padding-top: 14px; padding-bottom: 14px;
    text-align: center;  /* 헤더 텍스트만 중앙 정렬 (body td는 영향 없음) */
    white-space: nowrap;
  }}
  /* 세로 구분선 — 헤더 영역에만 (셀 중앙에 짧은 세로 라인) */
  th:not(:last-child)::after {{
    content: ''; position: absolute; right: 0;
    top: 28%; bottom: 28%; width: 1px;
    background: var(--separator);
  }}
  th.align-right, td.align-right {{ text-align: right; }}
  td a {{ color: var(--link); text-decoration: none; }}
  td a:not(.stock-link) {{ border-bottom: 1px dashed #b9d6e8; }}
  td a:hover {{ color: #1d4ed8; }}
  td.mfe-mae {{ font-size: 0.905rem; white-space: nowrap; line-height: 1.35; text-align: right; }}
  td.mfe-mae .row {{ display: block; }}
  td.stock-col {{ padding: 16px 14px; }}
  td.amount-col {{ font-weight: 600; text-align: center; }}
  /* Stock cell: logo + (sector tag / stock name) */
  .stock-link {{ display: block; text-decoration: none; color: inherit; }}
  .stock-row {{ display: flex; align-items: center; gap: 10px; }}
  .stock-info {{ display: flex; flex-direction: column; gap: 3px; min-width: 0; }}
  .stock-info .stock-name {{
    color: var(--text); font-weight: 500; font-size: 1.005rem;
    border-bottom: 1px dashed transparent;
  }}
  .stock-link:hover .stock-name {{ color: var(--link); }}
  .sector-tag {{
    display: inline-block; font-size: 0.745rem;
    padding: 1px 7px; border-radius: 6px;
    line-height: 1.4; font-weight: 700;
    letter-spacing: -0.01em;
  }}
  .stock-logo, .stock-logo-fb {{
    display: inline-block; width: 32px; height: 32px;
    vertical-align: middle; border-radius: 50%; overflow: hidden;
    flex-shrink: 0;
  }}
  .stock-logo {{ background: white; border: 1px solid var(--border); }}
  .stock-logo img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}
  .stock-logo-fb {{
    color: white; font-size: 12px; font-weight: 700;
    text-align: center; line-height: 32px;
  }}
  /* Show more */
  .show-more-wrap {{ text-align: center; margin-top: 0.8rem; }}
  .show-more-btn {{
    background: white; border: 1px solid var(--border); color: var(--text);
    padding: 0.55rem 1.4rem; border-radius: 10px; cursor: pointer;
    font-size: 0.975rem; font-family: inherit;
  }}
  .show-more-btn:hover {{ background: #f0f3f5; }}
  /* Empty */
  .empty {{
    color: var(--muted); padding: 2.5rem 1rem; text-align: center;
    font-size: 1.005rem; line-height: 1.6;
  }}
  /* Up/Down colors */
  .up {{ color: var(--up); font-weight: 600; }}
  .down {{ color: var(--down); font-weight: 600; }}
  .table-wrap {{ overflow-x: auto; }}
  /* Responsive */
  @media (max-width: 480px) {{
    body {{ padding: 0.9rem 0.7rem; }}
    h1 {{ font-size: 1.275rem; }}
    .meta {{ font-size: 0.905rem; }}
    .month-card {{ padding: 1.1rem 0.9rem; }}
    .month-title {{ font-size: 1.375rem; }}
    .chart {{ height: 280px; }}
    .stats-grid {{ gap: 0.4rem; }}
    .stat-cell {{
      padding: 0.5rem 0.55rem; font-size: 0.825rem;
      flex-direction: column; align-items: flex-start; gap: 0.15rem;
    }}
    .stat-cell strong {{ font-size: 1.045rem; }}
    /* 표: 4컬럼 모바일 — 세로 구분선은 헤더에만 + 충분한 padding */
    table {{ font-size: 0.885rem; table-layout: fixed !important; }}
    table th:nth-child(1), table td:nth-child(1) {{ width: 60px !important; }}
    table th:nth-child(3), table td:nth-child(3) {{ width: 95px !important; }}
    table th:nth-child(4), table td:nth-child(4) {{ width: 70px !important; }}
    th, td {{ padding: 14px 8px; overflow: hidden; }}
    .stock-info {{ overflow: hidden; }}
    .stock-info .stock-name {{
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }}
    th {{ font-size: 0.805rem; padding: 12px 8px; }}
    td.stock-col {{ padding: 14px 8px; }}
    .stock-logo, .stock-logo-fb {{ width: 26px; height: 26px; }}
    .stock-logo-fb {{ line-height: 26px; font-size: 10px; }}
    .stock-row {{ gap: 6px; }}
    .stock-info {{ gap: 2px; }}
    .stock-info .stock-name {{ font-size: 0.925rem; }}
    .sector-tag {{ font-size: 0.705rem; padding: 1px 5px; }}
    td.mfe-mae {{ font-size: 0.845rem; }}
    td.amount-col {{ font-size: 0.945rem; }}
    /* 추천일 column nowrap + 작게 */
    td:first-child, th:first-child {{ white-space: nowrap; font-size: 0.825rem; }}
  }}
</style>
</head>
<body>
  <h1>📊 주식 AI 백테스트 리포트</h1>
  <ul class="meta">
    <li>최종 업데이트 : {updated_at} KST</li>
    <li>평가 기준 : 추천일 +14일 시점 수익률 (8개 티어)</li>
    <li><a href="./workflow.html" class="workflow-link">🔧 시스템 워크플로우 보기 →</a></li>
    <li><a href="./demo.html" class="workflow-link">🎨 디자인 미리보기 (가짜 데이터) →</a></li>
  </ul>

{body}

<script>
function logoFail(img) {{
  var span = document.createElement('span');
  span.className = 'stock-logo-fb';
  span.style.background = img.dataset.bg;
  span.textContent = img.dataset.letter;
  img.parentElement.replaceChild(span, img);
}}
function resizePlotlyIn(card) {{
  // 카드가 보이게 된 후 그 안의 차트를 다시 사이즈 계산
  if (!card || !window.Plotly) return;
  var chart = card.querySelector('.chart');
  if (chart) {{
    // 다음 paint 후에 resize (display: block 적용 대기)
    requestAnimationFrame(function() {{
      try {{ Plotly.Plots.resize(chart); }} catch(e) {{}}
    }});
  }}
}}
function selectMonth(btn) {{
  var month = btn.dataset.month;
  var year = btn.dataset.year;
  document.querySelectorAll('.month-tab').forEach(function(b) {{
    b.classList.toggle('active', b === btn);
  }});
  document.querySelectorAll('.month-card').forEach(function(card) {{
    var match = (!month || card.dataset.month === year + '-' + month);
    card.hidden = !match;
    if (match) resizePlotlyIn(card);
  }});
}}
function selectYear(sel) {{
  var year = sel.value;
  document.querySelectorAll('.month-tab').forEach(function(b) {{
    b.dataset.year = year;
    b.classList.remove('active');
  }});
  document.querySelectorAll('.month-card').forEach(function(card) {{
    card.hidden = !card.dataset.month.startsWith(year + '-');
  }});
  var firstTab = document.querySelector('.month-tab');
  if (firstTab) {{
    firstTab.classList.add('active');
    selectMonth(firstTab);
  }}
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
        f'<h3 class="section-title">{title} <span class="count">(총 {total}개)</span></h3>'
        f'<p class="section-desc">'
        f"수익률 높은 순 · 종목명을 누르면 네이버 증권에서 열립니다.<br>"
        f"<span class='up'>▲</span> 최고상승 (MFE) / <span class='down'>▼</span> 최대하락 (MAE)"
        f"</p>"
        f'<div class="table-wrap"><table>'
        f"<colgroup>"
        f'<col class="col-date"><col class="col-stock">'
        f'<col class="col-ret"><col class="col-range">'
        f"</colgroup>"
        f"<thead><tr>"
        f"<th>추천일</th><th>종목</th>"
        f"<th>현재 수익률</th>"
        f"<th>변동폭</th>"
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
            f'<td class="align-right">{s["total"]}</td>'
            f'<td class="align-right {cls}">{sign}{abs(s["avg_return"]):.2f}%</td>'
            f"</tr>"
        )
    return (
        f'<h3 class="section-title">섹터별 성과 <span class="count">(최근 30일)</span></h3>'
        f'<div class="table-wrap"><table>'
        f"<colgroup>"
        f'<col class="sector-name"><col class="sector-count"><col class="sector-ret">'
        f"</colgroup>"
        f"<thead><tr><th>섹터</th>"
        f"<th>종목 수</th>"
        f"<th>평균 수익률</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"</table></div>"
    )


def _month_card(stats: dict, hidden: bool = False) -> tuple[str, dict | None]:
    """월별 카드 HTML + 차트 데이터(없으면 None). hidden=True이면 hidden 속성 부여."""
    month = stats["month"]  # YYYY-MM
    title = month.replace("-", ". ")  # "2026. 05"
    matured = stats["matured_count"]
    in_progress = stats.get("in_progress_count", 0)

    subtitle = f"진행 총 {in_progress}개 / 만기 {matured}개"

    chart_id = f"chart_{month.replace('-', '_')}"
    chart_data = None
    chart_html = ""
    legend_html = ""
    stats_html = ""

    if matured > 0:
        cls = "up" if stats["avg_return"] >= 0 else "down"
        sign = "+" if stats["avg_return"] >= 0 else ""
        # 1행: 평균 / 승률 / 강승 (라벨 짧게)
        row1 = (
            f'<div class="stat-cell">평균 <strong class="{cls}">{sign}{stats["avg_return"]:.2f}%</strong></div>'
            f'<div class="stat-cell">승률 <strong>{stats["win_rate"]:.0f}%</strong></div>'
            f'<div class="stat-cell">강승 <strong>{stats["strong_win_rate"]:.0f}%</strong></div>'
        )
        stats_html = f'<div class="stats-grid">{row1}</div>'
        # 2행: MFE / MAE (있을 때만)
        if stats.get("avg_mfe", 0) or stats.get("avg_mae", 0):
            row2 = (
                f'<div class="stat-cell">평균 MFE <strong class="up">▲{stats["avg_mfe"]:.2f}%</strong></div>'
                f'<div class="stat-cell">평균 MAE <strong class="down">▼{abs(stats["avg_mae"]):.2f}%</strong></div>'
            )
            stats_html += f'<div class="stats-grid cols-2">{row2}</div>'

        # 차트 데이터
        labels, values, colors = [], [], []
        for i, c in enumerate(stats["tier_counts"]):
            if c > 0:
                labels.append(TIER_LABELS[i])
                values.append(c)
                colors.append(TIER_COLORS[i])
        chart_data = {"id": chart_id, "labels": labels, "values": values, "colors": colors}
        chart_html = f'<div id="{chart_id}" class="chart"></div>'

        # 차트 아래 8 tier 범례 (전체 8개 표시 — 0건이어도)
        legend_items = []
        for i, label in enumerate(TIER_LABELS):
            legend_items.append(
                f'<div class="legend-item">'
                f'<span class="legend-dot" style="background:{TIER_COLORS[i]}"></span>'
                f'{label}</div>'
            )
        legend_html = f'<div class="chart-legend">{"".join(legend_items)}</div>'
    else:
        chart_html = (
            '<div class="empty">아직 만기 도달 종목 없음<br>'
            "(추천일로부터 14일 경과 시 평가)</div>"
        )

    hidden_attr = ' hidden' if hidden else ''
    html = (
        f'<div class="month-card" data-month="{month}"{hidden_attr}>'
        f'<div class="month-title">{title}</div>'
        f'<div class="month-subtitle">{subtitle}</div>'
        f'{stats_html}'
        f'{chart_html}'
        f'{legend_html}'
        f'</div>'
    )
    return html, chart_data


def _pick_default_month(monthly_sorted: list[dict]) -> str:
    """기본 선택 월: 가장 최신 '만기 도달 데이터 있는' 월. 없으면 최신 월."""
    for m in monthly_sorted:
        if m.get("matured_count", 0) > 0:
            return m["month"]
    return monthly_sorted[0]["month"] if monthly_sorted else ""


def _filter_bar(monthly_sorted: list[dict], default_month: str) -> str:
    """연도 selector + 월 탭. default_month가 active."""
    if not monthly_sorted:
        return ""
    years = sorted({m["month"][:4] for m in monthly_sorted}, reverse=True)
    default_year = default_month[:4]
    default_mm = default_month[5:7]
    months_in_year = sorted(
        {m["month"][5:7] for m in monthly_sorted if m["month"][:4] == default_year},
        reverse=True,
    )
    year_options = "".join(
        f'<option value="{y}"{" selected" if y == default_year else ""}>{y}년</option>'
        for y in years
    )
    month_tabs = "".join(
        f'<button class="month-tab{" active" if m == default_mm else ""}" '
        f'data-month="{m}" data-year="{default_year}" '
        f'onclick="selectMonth(this)">{int(m)}월</button>'
        for m in months_in_year
    )
    return (
        f'<div class="filter-bar">'
        f'<select class="year-selector" onchange="selectYear(this)">{year_options}</select>'
        f'{month_tabs}'
        f'</div>'
    )


def _plotly_script(chart: dict) -> str:
    """Plotly 도넛 차트 JS 코드. HTML 범례를 따로 그리므로 차트 자체는 legend off."""
    return f"""
Plotly.newPlot({json.dumps(chart['id'])}, [{{
  type: 'pie',
  labels: {json.dumps(chart['labels'], ensure_ascii=False)},
  values: {json.dumps(chart['values'])},
  marker: {{colors: {json.dumps(chart['colors'])}, line: {{color: 'white', width: 2}}}},
  textinfo: 'percent',
  textposition: 'inside',
  insidetextorientation: 'horizontal',
  insidetextfont: {{size: 11, color: 'white'}},
  hovertemplate: '<b>%{{label}}</b><br>%{{value}}종목 (%{{percent}})<extra></extra>',
  hole: 0.5,
  sort: false,
  automargin: true
}}], {{
  showlegend: false,
  margin: {{t: 10, b: 10, l: 10, r: 10}},
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: {{family: 'inherit', size: 11}}
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
        # 기본 선택: 만기 도달 데이터 있는 가장 최신 월
        default_month = _pick_default_month(monthly_sorted)
        body_parts.append(_filter_bar(monthly_sorted, default_month))
        for ms in monthly_sorted:
            html, chart = _month_card(ms, hidden=(ms["month"] != default_month))
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

    # 데모 페이지 생성 (디자인 미리보기용 — 가짜 데이터)
    try:
        demo_out = output.parent / "demo.html"
        generate_demo_page(demo_out)
    except Exception as e:
        logger.warning("데모 페이지 생성 실패 (메인 리포트는 정상): %s", e)


def generate_demo_page(output_path: str | Path):
    """디자인 검증용 데모 — 현실적 가짜 데이터로 모든 컴포넌트 렌더링"""
    monthly = [
        {
            "month": "2026-05", "matured_count": 30, "in_progress_count": 12,
            "total_recs": 42, "pending_count": 0,
            "tier_counts": [3, 7, 8, 5, 4, 2, 1, 0],
            "avg_return": 4.85, "win_rate": 76.7, "strong_win_rate": 60.0,
            "avg_mfe": 8.45, "avg_mae": -2.30,
        },
        {
            "month": "2026-04", "matured_count": 22, "in_progress_count": 0,
            "total_recs": 22, "pending_count": 0,
            "tier_counts": [0, 2, 3, 4, 5, 4, 3, 1],
            "avg_return": -2.15, "win_rate": 40.9, "strong_win_rate": 22.7,
            "avg_mfe": 3.20, "avg_mae": -4.85,
        },
        {
            "month": "2026-03", "matured_count": 18, "in_progress_count": 0,
            "total_recs": 18, "pending_count": 0,
            "tier_counts": [1, 3, 4, 5, 3, 1, 1, 0],
            "avg_return": 2.45, "win_rate": 72.2, "strong_win_rate": 44.4,
            "avg_mfe": 5.60, "avg_mae": -2.80,
        },
    ]
    items = [
        {"rec_date": "2026-05-22", "stock_code": "005930", "stock_name": "삼성전자", "sector": "반도체", "latest_return": 7.8, "mfe_pct": 9.2, "mae_pct": -1.5},
        {"rec_date": "2026-05-22", "stock_code": "000660", "stock_name": "SK하이닉스", "sector": "반도체", "latest_return": 5.2, "mfe_pct": 7.4, "mae_pct": -0.8},
        {"rec_date": "2026-05-21", "stock_code": "051910", "stock_name": "LG화학", "sector": "2차전지", "latest_return": 4.5, "mfe_pct": 6.1, "mae_pct": -1.2},
        {"rec_date": "2026-05-21", "stock_code": "009420", "stock_name": "한올바이오파마", "sector": "바이오", "latest_return": 3.8, "mfe_pct": 5.2, "mae_pct": -2.1},
        {"rec_date": "2026-05-20", "stock_code": "105560", "stock_name": "KB금융", "sector": "금융", "latest_return": 2.1, "mfe_pct": 3.5, "mae_pct": -0.5},
        {"rec_date": "2026-05-20", "stock_code": "042660", "stock_name": "한화오션", "sector": "조선", "latest_return": 1.5, "mfe_pct": 2.8, "mae_pct": -1.0},
        {"rec_date": "2026-05-19", "stock_code": "005380", "stock_name": "현대차", "sector": "자동차", "latest_return": -0.5, "mfe_pct": 1.2, "mae_pct": -2.3},
        {"rec_date": "2026-05-19", "stock_code": "015760", "stock_name": "한국전력", "sector": "전력", "latest_return": -1.8, "mfe_pct": 0.5, "mae_pct": -3.1},
        {"rec_date": "2026-05-18", "stock_code": "035420", "stock_name": "NAVER", "sector": "IT", "latest_return": -2.5, "mfe_pct": 0.8, "mae_pct": -4.2},
        {"rec_date": "2026-05-18", "stock_code": "247540", "stock_name": "에코프로비엠", "sector": "2차전지", "latest_return": -3.5, "mfe_pct": 1.5, "mae_pct": -5.8},
    ]
    sectors = [
        {"sector": "반도체", "total": 12, "avg_return": 5.85},
        {"sector": "2차전지", "total": 8, "avg_return": 2.15},
        {"sector": "바이오", "total": 6, "avg_return": 1.40},
        {"sector": "금융", "total": 5, "avg_return": 0.85},
        {"sector": "자동차", "total": 4, "avg_return": -0.50},
        {"sector": "IT", "total": 4, "avg_return": -1.25},
    ]
    # 임시로 generate_html_report 직접 호출 (workflow_page/demo 재귀 방지를 위해 다른 경로 사용)
    monthly_sorted = sorted(monthly, key=lambda m: m["month"], reverse=True)
    body_parts: list[str] = []
    chart_datas: list[dict] = []
    default_month = _pick_default_month(monthly_sorted)
    body_parts.append(_filter_bar(monthly_sorted, default_month))
    for ms in monthly_sorted:
        html, chart = _month_card(ms, hidden=(ms["month"] != default_month))
        body_parts.append(html)
        if chart:
            chart_datas.append(chart)
    body_parts.append(_stock_table(items, "이번 주 추천 종목 성과"))
    body_parts.append(_sector_table(sectors))

    # 데모임을 알리는 배너 추가
    demo_banner = (
        '<div style="background:#fef3c7;border:1px solid #fcd34d;'
        'padding:0.8rem 1rem;border-radius:10px;margin-bottom:1rem;'
        'font-size:0.9rem;color:#92400e">'
        '🎨 <strong>디자인 미리보기</strong> — 가짜 데이터 기반. '
        '실제 운영 화면은 <a href="./" style="color:#92400e;font-weight:500">메인</a>에서 확인.'
        '</div>'
    )
    body_parts.insert(0, demo_banner)

    scripts = "\n".join(_plotly_script(c) for c in chart_datas)
    html = HTML_TEMPLATE.format(
        updated_at=datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        body="\n".join(body_parts),
        scripts=scripts,
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    logger.info("데모 페이지 생성: %s (%d bytes)", out, out.stat().st_size)
