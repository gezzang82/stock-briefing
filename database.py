import sqlite3
import json
import logging
from datetime import date, datetime
from contextlib import contextmanager
from config import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_date TEXT NOT NULL,          -- 추천일 (YYYY-MM-DD)
    rank INTEGER NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    sector TEXT,
    entry_price REAL,                -- 추천 당일 종가
    target_return_pct REAL,
    risk_level TEXT,
    reason TEXT,
    key_catalyst TEXT,
    market_summary TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    -- Outcome (추천일~만기 14일 사이의 가격 추이 결과)
    mfe_pct REAL,                    -- 최고 상승률 (Max Favorable Excursion)
    mae_pct REAL,                    -- 최대 하락률 (Max Adverse Excursion)
    final_return_pct REAL,           -- 만기일 시점 최종 수익률
    mfe_date TEXT,                   -- MFE 달성일
    mae_date TEXT,                   -- MAE 발생일
    outcome_updated_at TEXT          -- outcome 마지막 계산 시각
);

CREATE TABLE IF NOT EXISTS price_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL,
    stock_code TEXT NOT NULL,
    rec_date TEXT NOT NULL,
    track_date TEXT NOT NULL,        -- 가격 기록일
    close_price REAL,
    return_pct REAL,                 -- 추천일 대비 수익률
    FOREIGN KEY (recommendation_id) REFERENCES recommendations(id),
    UNIQUE (recommendation_id, track_date)
);

CREATE TABLE IF NOT EXISTS accuracy_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_date TEXT NOT NULL UNIQUE,
    total_stocks INTEGER,
    hit_count INTEGER,               -- 적중 (HIT_THRESHOLD 이상 상승)
    hit_rate_pct REAL,
    avg_return_pct REAL,
    best_return_pct REAL,
    worst_return_pct REAL,
    calculated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS snapshot_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    market_regime TEXT,
    kospi REAL,
    kosdaq REAL,
    news_sentiment REAL,
    recommendation_count INTEGER,
    snapshot_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS signal_performance_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    months INTEGER,
    result_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rec_date ON recommendations(rec_date);
CREATE INDEX IF NOT EXISTS idx_track_date ON price_tracking(track_date);
CREATE INDEX IF NOT EXISTS idx_tracking_code ON price_tracking(stock_code, rec_date);
CREATE INDEX IF NOT EXISTS idx_snapshot_date ON snapshot_logs(snapshot_date);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    logger.info("DB 초기화 완료: %s", DB_PATH)


def _migrate(conn):
    """기존 DB에 새 컬럼이 없으면 ALTER TABLE로 추가 (idempotent)"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(recommendations)").fetchall()}
    new_cols = [
        ("mfe_pct", "REAL"),
        ("mae_pct", "REAL"),
        ("final_return_pct", "REAL"),
        ("mfe_date", "TEXT"),
        ("mae_date", "TEXT"),
        ("outcome_updated_at", "TEXT"),
        ("tech_score", "REAL"),       # 기술적 스크리닝 점수 (0~100)
    ]
    for col, col_type in new_cols:
        if col not in existing:
            conn.execute(f"ALTER TABLE recommendations ADD COLUMN {col} {col_type}")
            logger.info("스키마 마이그레이션: recommendations.%s 추가", col)


def save_recommendations(rec_date: str, recommendations: list[dict], market_summary: str):
    """AI 추천 종목 저장 (tech_score 포함)"""
    with get_conn() as conn:
        conn.execute("DELETE FROM recommendations WHERE rec_date = ?", (rec_date,))
        for r in recommendations:
            conn.execute(
                """INSERT INTO recommendations
                   (rec_date, rank, stock_code, stock_name, sector, entry_price,
                    target_return_pct, risk_level, reason, key_catalyst,
                    market_summary, tech_score)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rec_date, r["rank"], r["code"], r["name"],
                    r.get("sector"), r.get("entry_price"),
                    r.get("target_return"), r.get("risk_level"),
                    r.get("reason"), r.get("key_catalyst"), market_summary,
                    r.get("tech_score"),
                ),
            )
    logger.info("%s 추천 종목 %d개 저장 완료", rec_date, len(recommendations))


def update_entry_price(rec_date: str, stock_code: str, price: float):
    with get_conn() as conn:
        conn.execute(
            "UPDATE recommendations SET entry_price=? WHERE rec_date=? AND stock_code=?",
            (price, rec_date, stock_code),
        )


def save_price_tracking(rec_id: int, stock_code: str, rec_date: str,
                        track_date: str, price: float, entry_price: float | None):
    return_pct = None
    if entry_price and entry_price > 0:
        return_pct = (price - entry_price) / entry_price * 100

    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO price_tracking
               (recommendation_id, stock_code, rec_date, track_date, close_price, return_pct)
               VALUES (?,?,?,?,?,?)""",
            (rec_id, stock_code, rec_date, track_date, price, return_pct),
        )


def get_recommendations_for_tracking(target_date: str) -> list[sqlite3.Row]:
    """price_tracking이 필요한 당일 추천 종목 목록"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM recommendations WHERE rec_date = ?", (target_date,)
        ).fetchall()


def get_mature_recs(maturity_date: str) -> list[str]:
    """2주가 지나 적중률 계산이 필요한 추천일 목록"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT rec_date FROM recommendations
               WHERE rec_date <= ?
               AND rec_date NOT IN (SELECT rec_date FROM accuracy_results)""",
            (maturity_date,),
        ).fetchall()
    return [r["rec_date"] for r in rows]


def get_tracking_data(rec_date: str, final_date: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT pt.stock_code, pt.close_price, pt.return_pct, r.entry_price
               FROM price_tracking pt
               JOIN recommendations r ON pt.recommendation_id = r.id
               WHERE pt.rec_date = ? AND pt.track_date = ?""",
            (rec_date, final_date),
        ).fetchall()


def save_accuracy(rec_date: str, total: int, hits: int, returns: list[float]):
    hit_rate = hits / total * 100 if total else 0
    avg_ret = sum(returns) / len(returns) if returns else 0
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO accuracy_results
               (rec_date, total_stocks, hit_count, hit_rate_pct, avg_return_pct,
                best_return_pct, worst_return_pct)
               VALUES (?,?,?,?,?,?,?)""",
            (rec_date, total, hits, hit_rate, avg_ret,
             max(returns) if returns else 0, min(returns) if returns else 0),
        )


def get_recent_accuracy(limit: int = 10) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM accuracy_results ORDER BY rec_date DESC LIMIT ?", (limit,)
        ).fetchall()


def save_snapshot(
    snapshot_date: str,
    market_regime: str | None,
    kospi: float | None,
    kosdaq: float | None,
    news_sentiment: float | None,
    recommendation_count: int,
    snapshot_dict: dict,
) -> bool:
    """
    추천 당시 전체 컨텍스트 snapshot 저장.
    JSON 직렬화/DB 쓰기 실패해도 raise하지 않고 False 반환 → 브리핑 전체 흐름 유지.
    """
    try:
        snapshot_json = json.dumps(snapshot_dict, ensure_ascii=False, default=str)
    except Exception as e:
        logger.warning("Snapshot JSON 직렬화 실패: %s", e)
        return False
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO snapshot_logs
                   (snapshot_date, market_regime, kospi, kosdaq,
                    news_sentiment, recommendation_count, snapshot_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_date, market_regime, kospi, kosdaq,
                 news_sentiment, recommendation_count, snapshot_json),
            )
        return True
    except Exception as e:
        logger.warning("Snapshot DB 저장 실패: %s", e)
        return False


def get_snapshot_by_date(snapshot_date: str) -> dict | None:
    """
    특정 일자의 snapshot 조회 (같은 날 여러 번 저장됐다면 최신 것 반환).
    snapshot_data 필드에 파싱된 dict가 들어감.
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id, snapshot_date, market_regime, kospi, kosdaq,
                      news_sentiment, recommendation_count, snapshot_json, created_at
               FROM snapshot_logs
               WHERE snapshot_date = ?
               ORDER BY created_at DESC LIMIT 1""",
            (snapshot_date,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["snapshot_data"] = json.loads(result["snapshot_json"])
    except Exception as e:
        logger.warning("Snapshot JSON 파싱 실패 [%s]: %s", snapshot_date, e)
        result["snapshot_data"] = None
    return result


def save_signal_performance(months: int, result: dict) -> bool:
    """
    시그널 성과 분석 결과를 캐시 테이블에 저장.
    실패해도 raise하지 않음 — 분석 자체는 read-only이므로 호출자 영향 최소화.
    """
    try:
        result_json = json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        logger.warning("signal_performance JSON 직렬화 실패: %s", e)
        return False
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO signal_performance_cache (months, result_json)
                   VALUES (?, ?)""",
                (months, result_json),
            )
        return True
    except Exception as e:
        logger.warning("signal_performance DB 저장 실패: %s", e)
        return False


def get_latest_signal_performance() -> dict:
    """
    최신 시그널 성과 캐시 로드. 없거나 파싱 실패 시 {} 반환.
    """
    try:
        with get_conn() as conn:
            row = conn.execute(
                """SELECT result_json FROM signal_performance_cache
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
    except Exception as e:
        logger.debug("signal_performance 조회 실패: %s", e)
        return {}
    if not row:
        return {}
    try:
        return json.loads(row["result_json"])
    except Exception as e:
        logger.warning("signal_performance JSON 파싱 실패: %s", e)
        return {}


def has_recommendations_for_date(target_date: str) -> bool:
    """
    해당 날짜에 추천 데이터가 존재하는지 확인.

    ⚠️ DEPRECATED for idempotency guard — qualified=0인 날엔 row가
    안 들어가 가드를 우회시킴. 멱등성 가드는 has_briefing_run_for_date() 사용.

    그 외 일반 조회 용도로는 계속 사용 가능.
    """
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM recommendations WHERE rec_date = ? LIMIT 1",
                (target_date,),
            ).fetchone()
        return row is not None
    except Exception as e:
        logger.debug("has_recommendations_for_date 체크 실패 (무시): %s", e)
        return False


def has_briefing_run_for_date(target_date: str) -> bool:
    """
    해당 날짜에 브리핑이 이미 실행되었는지 확인 (멱등성 가드 — 권장).

    snapshot_logs 기준 — 자격 통과 추천이 0개여도 snapshot은 항상 저장되므로
    "오늘 이미 돌았다"는 사실을 견고하게 판단 가능.

    has_recommendations_for_date()는 recommendations 테이블 기준이라
    qualified=0인 날에는 row가 안 들어가서 가드 우회 사고 발생 (2026-05-27).

    Graceful fallback: 어떤 에러에도 False 반환.
    """
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM snapshot_logs WHERE snapshot_date = ? LIMIT 1",
                (target_date,),
            ).fetchone()
        return row is not None
    except Exception as e:
        logger.debug("has_briefing_run_for_date 체크 실패 (무시): %s", e)
        return False


def get_recent_recommended_stocks(days: int = 7) -> list[dict]:
    """최근 N일간 추천된 고유 종목 (중복 횟수 포함). AI에 회피 힌트로 전달"""
    from datetime import timedelta
    from market_calendar import today_kst
    today = today_kst()
    cutoff = (today - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT stock_code, stock_name,
                      MAX(rec_date) AS last_date,
                      COUNT(*) AS times
               FROM recommendations
               WHERE rec_date >= ? AND rec_date < ?
               GROUP BY stock_code, stock_name
               ORDER BY times DESC, last_date DESC""",
            (cutoff, today.isoformat()),
        ).fetchall()
    return [dict(r) for r in rows]
