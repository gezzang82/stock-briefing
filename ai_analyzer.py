"""
OpenAI를 이용한 주식 분석 및 추천 종목 선정
"""
import json
import logging
import re
from market_calendar import today_kst

from openai import OpenAI

from config import OPENAI_API_KEY, TOP_N_STOCKS

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """\
당신은 한국 주식 시장 전문 애널리스트입니다.
아래 최신 금융 뉴스와 시장 지표를 분석하여 단기(1~2주) 투자 유망 종목 {n}개를 추천해주세요.

=== 오늘의 시장 지표 ({date}) ===
KOSPI: {kospi}
KOSDAQ: {kosdaq}

=== 시장 상태 (regime) ===
{regime_text}

=== 최신 금융 뉴스 ===
{news}

=== 기술적 분석 상위 후보 (거래량/모멘텀/돌파 기반 스크리닝) ===
{tech_candidates}

{signal_performance}=== 최근 7일 이미 추천된 종목 (가급적 제외) ===
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
1. recommendations는 **최대 {n}개**. 강한 시그널이 있는 종목 위주로 선정.
   약한 신호의 종목을 억지로 채울 필요 없음 — 후처리에서 기술 점수가 낮은
   종목은 자동 제외됨. 4~7개여도 충분.
2. **code 정확성이 최우선**: 한국거래소 실제 상장 종목의 6자리 코드. 추측 금지.
   확실하지 않으면 그 종목은 빼고, 본인이 코드를 정확히 아는 종목만 추천.
   ❌ 중복 코드 금지 / ❌ 코드↔이름 불일치는 자동 무효 처리됨
3. **기술적 후보 목록의 종목 위주로 선정** — 후보 외 종목은 점수가 없어
   자동 제외됨. 후보 중에서 외국인/기관 수급 + 거래대금 시그널이 가장 강한
   종목을 우선 골라야 함.
4. 참고용 시총 상위 코드 (필요시 사용):
   - 005930 삼성전자 / 000660 SK하이닉스 / 005380 현대차 / 005490 POSCO홀딩스
   - 035420 NAVER / 035720 카카오 / 051910 LG화학 / 006400 삼성SDI
   - 068270 셀트리온 / 207940 삼성바이오로직스 / 247540 에코프로비엠
5. 다양한 섹터에서 선정 (한 섹터 최대 3개), 우량주만 고르지 말고 기술적
   시그널이 강한 중소형도 적극 포함
6. risk_level은 "낮음", "중간", "높음" 중 하나
7. target_return은 숫자만 (단위: %)
8. **최근 7일 추천 목록과 가급적 겹치지 않게 다양화**. 새 모멘텀이 있어
   재추천이 합리적이라면 가능 — reason에 재추천 이유 명시.
9. reason 작성 시 가능하면 기술적 시그널(거래량 폭증/돌파/정배열 등)과
   뉴스/펀더멘털 근거를 함께 언급.
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
    tech_candidates_text: str = "",
    regime_text: str = "",
    signal_performance_text: str = "",
) -> dict:
    """
    뉴스 + 시장지표 + 기술적 스크리닝 후보를 종합해 추천 종목 반환.
    recent_excluded: 최근 7일 추천 종목 회피 힌트
    tech_candidates_text: 기술적 스크리너가 생성한 후보 포맷 문자열
    signal_performance_text: 과거 시그널 성과 (참고용, 비면 섹션 생략)
    """
    client = OpenAI(api_key=OPENAI_API_KEY)

    # 검증에서 일부 드롭될 가능성을 고려해 버퍼 포함하여 더 많이 요청
    ai_n = TOP_N_STOCKS + 3
    # 시그널 성과 섹션은 비어있으면 통째로 생략 (불필요한 공백 줄 방지)
    signal_perf_block = (
        signal_performance_text + "\n\n"
        if signal_performance_text.strip()
        else ""
    )
    prompt = ANALYSIS_PROMPT.format(
        n=ai_n,
        date=today_kst().strftime("%Y년 %m월 %d일"),
        kospi=kospi,
        kosdaq=kosdaq,
        news=news_text,
        recent_excluded=_format_recent_excluded(recent_excluded),
        tech_candidates=tech_candidates_text or "(기술적 스크리닝 결과 없음 — 뉴스 기반으로만 선정)",
        regime_text=regime_text or "(시장 상태 데이터 없음 — 기본 균형 전략)",
        signal_performance=signal_perf_block,
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
        # 가변 카운트 시스템 — AI가 적게 반환해도 OK (점수 필터 + 재시도로 보완)
        # 단, 0개거나 1개면 무언가 잘못된 것 (JSON 파싱 실패에 가까움)
        if len(recs) == 0:
            raise ValueError("추천 종목 0개 — 응답 형식 오류 가능성")
        if len(recs) < int(TOP_N_STOCKS * 0.3):
            logger.warning(
                "AI 추천 %d개 (요청 %d) — 시장 데이터 부족 또는 보수적 응답",
                len(recs), TOP_N_STOCKS,
            )
        logger.info("AI 분석 완료 - 추천 종목 %d개 (요청 %d)", len(recs), TOP_N_STOCKS)
        return result
    except Exception as e:
        logger.error("AI 응답 파싱 실패: %s\n응답: %s", e, response_text[:500])
        raise
