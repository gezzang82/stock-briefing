import sqlite3
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
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
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

CREATE INDEX IF NOT EXISTS idx_rec_date ON recommendations(rec_date);
CREATE INDEX IF NOT EXISTS idx_track_date ON price_tracking(track_date);
CREATE INDEX IF NOT EXISTS idx_tracking_code ON price_tracking(stock_code, rec_date);
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
    logger.info("DB 초기화 완료: %s", DB_PATH)


def save_recommendations(rec_date: str, recommendations: list[dict], market_summary: str):
    """AI 추천 종목 저장"""
    with get_conn() as conn:
        # 당일 기존 데이터 삭제 후 재저장 (재실행 안전)
        conn.execute("DELETE FROM recommendations WHERE rec_date = ?", (rec_date,))
        for r in recommendations:
            conn.execute(
                """INSERT INTO recommendations
                   (rec_date, rank, stock_code, stock_name, sector, entry_price,
                    target_return_pct, risk_level, reason, key_catalyst, market_summary)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rec_date, r["rank"], r["code"], r["name"],
                    r.get("sector"), r.get("entry_price"),
                    r.get("target_return"), r.get("risk_level"),
                    r.get("reason"), r.get("key_catalyst"), market_summary,
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
