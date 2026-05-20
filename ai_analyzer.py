"""
Claude AI를 이용한 주식 분석 및 추천 종목 선정
"""
import json
import logging
import re
from datetime import date

import anthropic

from config import ANTHROPIC_API_KEY, TOP_N_STOCKS

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """\
당신은 한국 주식 시장 전문 애널리스트입니다.
아래 최신 금융 뉴스와 시장 지표를 분석하여 단기(1~2주) 투자 유망 종목 {n}개를 추천해주세요.

=== 오늘의 시장 지표 ({date}) ===
KOSPI: {kospi}
KOSDAQ: {kosdaq}

=== 최신 금융 뉴스 ===
{news}

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
2. code는 한국거래소 실제 상장 종목의 정확한 6자리 코드
3. 다양한 섹터에서 선정 (한 섹터 최대 3개)
4. risk_level은 "낮음", "중간", "높음" 중 하나
5. target_return은 숫자만 (단위: %)
"""


def _parse_ai_response(text: str) -> dict:
    text = text.strip()
    # 마크다운 코드블록 제거
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = text.rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # JSON 블록만 추출 시도
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def analyze_and_recommend(news_text: str, kospi: str, kosdaq: str) -> dict:
    """
    뉴스와 시장 지표를 분석하여 추천 종목 반환.
    반환값: {"market_summary": ..., "key_themes": [...], "risk_factors": ..., "recommendations": [...]}
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = ANALYSIS_PROMPT.format(
        n=TOP_N_STOCKS,
        date=date.today().strftime("%Y년 %m월 %d일"),
        kospi=kospi,
        kosdaq=kosdaq,
        news=news_text,
    )

    logger.info("AI 분석 시작...")
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system="당신은 한국 주식 전문 애널리스트입니다. 항상 유효한 JSON만 반환합니다.",
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text
    logger.info("AI 응답 수신 (tokens: %d)", message.usage.input_tokens + message.usage.output_tokens)

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
