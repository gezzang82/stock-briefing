"""
OpenAI를 이용한 주식 분석 및 추천 종목 선정
"""
import json
import logging
import re
from datetime import date

from openai import OpenAI

from config import OPENAI_API_KEY, TOP_N_STOCKS

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """\
당신은 한국 주식 시장 전문 애널리스트입니다.
아래 최신 금융 뉴스와 시장 지표를 분석하여 단기(1~2주) 투자 유망 종목 {n}개를 추천해주세요.

=== 오늘의 시장 지표 ({date}) ===
KOSPI: {kospi}
KOSDAQ: {kosdaq}

=== 최신 금융 뉴스 ===
{news}

=== 최근 7일 이미 추천된 종목 (가급적 제외) ===
{recent_excluded}

위 정보를 바탕으로 다음 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.

{{
  "market_summary": "시장 전반 분석 요약 (3문장 이내)",
  "key_themes": ["테마1", "테마2", "테마3"],
  "risk_factors": "주요 리스크 요인 (1~2문장)",
  "recommendations": [
    {{
      "rank": 1,
      "code": "005930",
      "name": "삼성전자",
      "sector": "반도체/IT",
      "reason": "추천 이유 (구체적으로 2~3문장)",
      "key_catalyst": "핵심 모멘텀 (한 문장)",
      "target_return": 5.0,
      "risk_level": "중간"
    }}
  ]
}}

반드시 지켜야 할 규칙:
1. recommendations는 정확히 {n}개
2. **code 정확성이 최우선**: 한국거래소 실제 상장 종목의 6자리 코드. 추측 금지.
   확실하지 않으면 그 종목은 빼고, 본인이 코드를 정확히 아는 종목만 추천.
   ❌ 중복 코드 금지 (같은 종목 두 번 추천 금지)
   ❌ 코드와 종목명이 일치하지 않으면 자동 무효 처리됨
3. 참고용 주요 종목 코드 (정확한 매칭):
   - 005930 삼성전자 / 000660 SK하이닉스 / 005380 현대차 / 005490 POSCO홀딩스
   - 035420 NAVER / 035720 카카오 / 036570 엔씨소프트 / 251270 넷마블
   - 051910 LG화학 / 006400 삼성SDI / 373220 LG에너지솔루션
   - 068270 셀트리온 / 207940 삼성바이오로직스 / 328130 루닛
   - 105560 KB금융 / 055550 신한지주 / 086790 하나금융지주
   - 042660 한화오션 / 329180 HD현대중공업 / 010140 삼성중공업
   - 003490 대한항공 / 003670 포스코퓨처엠 / 247540 에코프로비엠
4. 다양한 섹터에서 선정 (한 섹터 최대 3개)
5. risk_level은 "낮음", "중간", "높음" 중 하나
6. target_return은 숫자만 (단위: %)
7. **최근 7일 추천 목록과 가급적 겹치지 않게 다른 종목으로 다양화**.
   다만 새로운 강력한 모멘텀(실적 발표, 정책 변화 등)이 있어 다시 추천할
   가치가 있다면 포함 가능 — 그 경우 reason에 재추천 이유 명시.
"""


def _parse_ai_response(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = text.rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def _format_recent_excluded(recent: list[dict] | None) -> str:
    if not recent:
        return "(최근 추천 이력 없음 — 자유롭게 선정)"
    lines = []
    for r in recent[:30]:  # 토큰 절약 — 최대 30개
        lines.append(
            f"  - [{r['stock_code']}] {r['stock_name']} "
            f"(최근 {r['last_date']}, {r['times']}회 추천)"
        )
    return "\n".join(lines)


def analyze_and_recommend(
    news_text: str, kospi: str, kosdaq: str,
    recent_excluded: list[dict] | None = None,
) -> dict:
    """
    뉴스와 시장 지표를 분석하여 추천 종목 반환.
    recent_excluded: 최근 7일 추천 종목 목록 — AI가 회피하도록 힌트로 전달
    """
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = ANALYSIS_PROMPT.format(
        n=TOP_N_STOCKS,
        date=date.today().strftime("%Y년 %m월 %d일"),
        kospi=kospi,
        kosdaq=kosdaq,
        news=news_text,
        recent_excluded=_format_recent_excluded(recent_excluded),
    )

    logger.info("AI 분석 시작...")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=4096,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "당신은 한국 주식 전문 애널리스트입니다. 항상 유효한 JSON만 반환합니다."},
            {"role": "user", "content": prompt},
        ],
    )

    response_text = response.choices[0].message.content
    usage = response.usage
    logger.info("AI 응답 수신 (tokens: %d)", usage.prompt_tokens + usage.completion_tokens)

    try:
        result = _parse_ai_response(response_text)
        recs = result.get("recommendations", [])
        if len(recs) < TOP_N_STOCKS:
            raise ValueError(f"추천 종목 수 부족: {len(recs)}/{TOP_N_STOCKS}")
        logger.info("AI 분석 완료 - 추천 종목 %d개", len(recs))
        return result
    except Exception as e:
        logger.error("AI 응답 파싱 실패: %s\n응답: %s", e, response_text[:500])
        raise
