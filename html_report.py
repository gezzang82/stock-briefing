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
  td a {{ color: #2980b9; text-decoration: none; border-bottom: 1px dashed #b9d6e8; }}
  td a:hover {{ color: #1c5980; border-bottom-color: #1c5980; }}
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
    평가 기준: 추천일 +14일 시점 수익률 (8개 티어)
  </p>

{body}

<script>
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
        rows.append(
            f"<tr{hidden_attr}>"
            f"<td>{it['rec_date']}</td>"
            f'<td><a href="{url}" target="_blank" rel="noopener">{it["stock_name"]}</a></td>'
            f"<td>{it.get('sector') or '-'}</td>"
            f'<td class="{cls}">{sign}{abs(ret):.2f}%</td>'
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
        f"수익률 높은 순 · 종목명을 누르면 네이버 증권에서 열립니다</p>"
        f'<div class="table-wrap"><table>'
        f"<thead><tr>"
        f"<th>추천일</th><th>종목명</th><th>섹터</th><th>수익률</th>"
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
