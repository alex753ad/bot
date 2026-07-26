"""s5_log.py — выделенная БД стратегии S5 (continuation).

Отдельный файл s5_signals.db: signals (основание входа) + trades (жизненный цикл).

[FIX] Авто-миграция: _ensure не только CREATE TABLE IF NOT EXISTS, но и добирает
недостающие столбцы через ALTER TABLE. Раньше при добавлении поля в код, но не в
существующую БД, значение молча терялось при INSERT (так пропал тройной NATR).
Теперь любое новое поле появляется в старой базе автоматически при первом обращении.

Все операции best-effort и в потоке (asyncio.to_thread) — не роняют торговлю.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

try:
    from version import BOT_VERSION
except Exception:
    BOT_VERSION = "unknown"

DB_PATH = "s5_signals.db"

# Полные схемы (source of truth). Миграция сверяет с ними существующие таблицы.
_SIGNALS_COLS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "ts": "REAL", "dt": "TEXT", "symbol": "TEXT",
    "entry": "REAL", "sl": "REAL", "tp1": "REAL", "tp2": "REAL", "rr": "REAL",
    "growth_pct": "REAL", "hours_since_peak": "REAL", "retr_pct": "REAL",
    "retr_candles": "INTEGER", "trig_vol_ratio": "REAL",
    "market_phase": "TEXT", "vol_decay": "REAL", "natr_now_pct": "REAL",
    "natr_1m": "REAL", "natr_5m": "REAL", "natr_15m": "REAL",
    "ema_fast": "REAL", "ema_slow": "REAL", "ema_dist_pct": "REAL",
    "delta_at_entry": "REAL", "buy_vol": "REAL", "sell_vol": "REAL",
    "delta_source": "TEXT",           # [NEW] 'aggtrades' | 'candle_proxy'
    "spread_pct": "REAL",             # [NEW] спред стакана на входе
    "candle_staleness_sec": "REAL",   # [NEW] возраст последней 1m-свечи
    "series_index": "INTEGER",        # [NEW] какой это вход по символу за 24ч (1,2,...)
    "sec_since_last_sl": "REAL",      # [NEW] секунд с последнего SL по символу
    "bot_version": "TEXT",            # [NEW] версия кода
    "basis": "TEXT", "opened": "INTEGER DEFAULT 0", "trade_id": "TEXT",
}
_TRADES_COLS = {
    "trade_id": "TEXT PRIMARY KEY",
    "signal_id": "INTEGER", "symbol": "TEXT",
    "entry_time": "REAL", "entry_dt": "TEXT", "entry_price": "REAL",
    "sl": "REAL", "tp1": "REAL", "tp2": "REAL", "rr": "REAL",
    "status": "TEXT DEFAULT 'open'",
    "exit_time": "REAL", "exit_price": "REAL", "exit_reason": "TEXT",
    "tp1_hit": "INTEGER DEFAULT 0",
    "pnl_pct": "REAL", "pnl_usdt": "REAL",
    "mfe_pct": "REAL", "mae_pct": "REAL",        # [NEW] max favorable/adverse за жизнь сделки
    "post_exit_high_30m": "REAL", "post_exit_low_30m": "REAL",  # [NEW] куда ушла цена после выхода
    "bot_version": "TEXT",                        # [NEW]
    "basis": "TEXT",
}


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _create(con, table, cols):
    body = ", ".join(f"{n} {t}" for n, t in cols.items())
    con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({body})")


def _migrate(con, table, cols):
    """ALTER TABLE ADD COLUMN для всех недостающих столбцов (идемпотентно)."""
    existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    for name, decl in cols.items():
        if name in existing or "PRIMARY KEY" in decl:
            continue
        col_type = decl.split("DEFAULT")[0].strip()
        default = f" DEFAULT {decl.split('DEFAULT')[1].strip()}" if "DEFAULT" in decl else ""
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}{default}")
        except sqlite3.OperationalError:
            pass


def _ensure(con: sqlite3.Connection) -> None:
    _create(con, "signals", _SIGNALS_COLS)
    _create(con, "trades", _TRADES_COLS)
    _migrate(con, "signals", _SIGNALS_COLS)
    _migrate(con, "trades", _TRADES_COLS)
    con.commit()


def _fmt(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


# ── синхронные ядра ──────────────────────────────────────────────────────────

def _log_signal_sync(sig: dict) -> int:
    con = _connect()
    try:
        _ensure(con)
        cols = [c for c in _SIGNALS_COLS if c != "id"]
        placeholders = ",".join("?" for _ in cols)
        vals = []
        for c in cols:
            if c == "dt":
                vals.append(_fmt(sig.get("ts", time.time())))
            elif c == "opened":
                vals.append(1 if sig.get("trade_id") else 0)
            elif c == "bot_version":
                vals.append(sig.get("bot_version", BOT_VERSION))
            else:
                vals.append(sig.get(c))
        cur = con.execute(
            f"INSERT INTO signals ({','.join(cols)}) VALUES ({placeholders})", vals)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _open_trade_sync(t: dict) -> None:
    con = _connect()
    try:
        _ensure(con)
        con.execute("""
            INSERT OR REPLACE INTO trades (trade_id, signal_id, symbol, entry_time,
                entry_dt, entry_price, sl, tp1, tp2, rr, status, bot_version, basis)
            VALUES (?,?,?,?,?,?,?,?,?,?, 'open', ?, ?)""", (
            t.get("trade_id"), t.get("signal_id"), t.get("symbol"), t.get("entry_time"),
            _fmt(t.get("entry_time", time.time())), t.get("entry_price"),
            t.get("sl"), t.get("tp1"), t.get("tp2"), t.get("rr"),
            t.get("bot_version", BOT_VERSION), t.get("basis"),
        ))
        con.commit()
    finally:
        con.close()


def _close_trade_sync(trade_id: str, exit_price: float, reason: str, tp1_hit: bool,
                      pnl_pct: float, pnl_usdt: float,
                      mfe_pct=None, mae_pct=None) -> None:
    con = _connect()
    try:
        _ensure(con)
        con.execute("""
            UPDATE trades SET status='closed', exit_time=?, exit_price=?, exit_reason=?,
                tp1_hit=?, pnl_pct=?, pnl_usdt=?, mfe_pct=?, mae_pct=? WHERE trade_id=?""", (
            time.time(), exit_price, reason, 1 if tp1_hit else 0,
            round(pnl_pct, 4), round(pnl_usdt, 4),
            round(mfe_pct, 4) if mfe_pct is not None else None,
            round(mae_pct, 4) if mae_pct is not None else None, trade_id,
        ))
        con.commit()
    finally:
        con.close()


def _set_post_exit_sync(trade_id: str, hi: float, lo: float) -> None:
    con = _connect()
    try:
        _ensure(con)
        con.execute("UPDATE trades SET post_exit_high_30m=?, post_exit_low_30m=? WHERE trade_id=?",
                    (hi, lo, trade_id))
        con.commit()
    finally:
        con.close()


# ── async-обёртки ────────────────────────────────────────────────────────────

async def log_signal(sig: dict) -> int:
    try: return await asyncio.to_thread(_log_signal_sync, sig)
    except Exception: return 0

async def open_trade(t: dict) -> None:
    try: await asyncio.to_thread(_open_trade_sync, t)
    except Exception: pass

async def close_trade(trade_id: str, exit_price: float, reason: str, tp1_hit: bool,
                      pnl_pct: float, pnl_usdt: float, mfe_pct=None, mae_pct=None) -> None:
    try: await asyncio.to_thread(_close_trade_sync, trade_id, exit_price, reason,
                                 tp1_hit, pnl_pct, pnl_usdt, mfe_pct, mae_pct)
    except Exception: pass

async def set_post_exit(trade_id: str, hi: float, lo: float) -> None:
    try: await asyncio.to_thread(_set_post_exit_sync, trade_id, hi, lo)
    except Exception: pass
