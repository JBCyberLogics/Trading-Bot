from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List

from sqlalchemy import delete, select
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.models import (
    AccountSnapshot,
    Base,
    ConnectionEvent,
    OHLCBar,
    OrderBookSnapshot,
    SafetyEvent,
    TickData,
    TradeRecord,
)


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.engine = create_engine(f"sqlite:///{db_path}", future=True)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        db = self.SessionLocal()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def log_connection_event(self, status: str, details: Dict[str, Any]) -> None:
        with self.session() as db:
            db.add(ConnectionEvent(timestamp=datetime.now(timezone.utc), status=status, details=details))

    def add_tick(self, symbol: str, bid: float, ask: float, spread_pips: float, raw: Dict[str, Any]) -> None:
        with self.session() as db:
            db.add(
                TickData(
                    symbol=symbol,
                    timestamp=datetime.now(timezone.utc),
                    bid=bid,
                    ask=ask,
                    spread_pips=spread_pips,
                    raw=raw,
                )
            )

    def add_order_book(self, symbol: str, imbalance: float, levels: Dict[str, Any]) -> None:
        with self.session() as db:
            db.add(
                OrderBookSnapshot(
                    symbol=symbol,
                    timestamp=datetime.now(timezone.utc),
                    imbalance=imbalance,
                    levels_json=levels,
                )
            )

    def add_ohlc_rows(self, symbol: str, timeframe: str, rows: Iterable[Dict[str, Any]]) -> None:
        with self.session() as db:
            for row in rows:
                ts = row.get("timestamp")
                if isinstance(ts, (int, float)):
                    timestamp = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                elif isinstance(ts, datetime):
                    timestamp = ts
                else:
                    continue
                db.add(
                    OHLCBar(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=timestamp,
                        open=float(row.get("open", 0.0)),
                        high=float(row.get("high", 0.0)),
                        low=float(row.get("low", 0.0)),
                        close=float(row.get("close", 0.0)),
                        volume=float(row.get("tick_volume", row.get("volume", 0.0)) or 0.0),
                    )
                )

    def add_account_snapshot(
        self,
        account_login: str,
        balance: float,
        equity: float,
        margin: float,
        daily_pnl: float,
    ) -> None:
        with self.session() as db:
            db.add(
                AccountSnapshot(
                    timestamp=datetime.now(timezone.utc),
                    account_login=account_login,
                    balance=balance,
                    equity=equity,
                    margin=margin,
                    daily_pnl=daily_pnl,
                )
            )

    def add_safety_event(self, severity: str, event: str, details: str) -> None:
        with self.session() as db:
            db.add(SafetyEvent(timestamp=datetime.now(timezone.utc), severity=severity, event=event, details=details))

    def add_trade(
        self,
        symbol: str,
        direction: str,
        lot_size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        source: str = "phase1",
    ) -> int:
        with self.session() as db:
            trade = TradeRecord(
                symbol=symbol,
                direction=direction,
                lot_size=lot_size,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                opened_at=datetime.now(timezone.utc),
                status="OPEN",
                pnl=0.0,
                source=source,
            )
            db.add(trade)
            db.flush()
            return int(trade.id)

    def close_trade(self, trade_id: int, pnl: float) -> None:
        with self.session() as db:
            row = db.get(TradeRecord, trade_id)
            if row is None:
                return
            row.status = "CLOSED"
            row.closed_at = datetime.now(timezone.utc)
            row.pnl = float(pnl)

    def list_open_trades(self) -> List[TradeRecord]:
        with self.session() as db:
            rows = db.execute(select(TradeRecord).where(TradeRecord.status == "OPEN")).scalars().all()
            return rows

    def recent_safety_events(self, limit: int = 100) -> List[SafetyEvent]:
        with self.session() as db:
            rows = (
                db.execute(select(SafetyEvent).order_by(SafetyEvent.timestamp.desc()).limit(max(1, limit))).scalars().all()
            )
            return rows

    def cleanup_retention(self, tick_days: int, order_book_days: int, ohlc_days: int) -> None:
        now = datetime.now(timezone.utc)
        tick_cutoff = now - timedelta(days=max(1, tick_days))
        book_cutoff = now - timedelta(days=max(1, order_book_days))
        ohlc_cutoff = now - timedelta(days=max(1, ohlc_days))

        with self.session() as db:
            db.execute(delete(TickData).where(TickData.timestamp < tick_cutoff))
            db.execute(delete(OrderBookSnapshot).where(OrderBookSnapshot.timestamp < book_cutoff))
            db.execute(delete(OHLCBar).where(OHLCBar.timestamp < ohlc_cutoff))

    def account_latest(self, account_login: str | None = None) -> Dict[str, Any]:
        with self.session() as db:
            query = select(AccountSnapshot).order_by(AccountSnapshot.timestamp.desc())
            if account_login:
                query = query.where(AccountSnapshot.account_login == account_login)
            row = db.execute(query.limit(1)).scalar_one_or_none()
            if row is None:
                return {}
            return {
                "timestamp": row.timestamp.isoformat(),
                "account_login": row.account_login,
                "balance": row.balance,
                "equity": row.equity,
                "margin": row.margin,
                "daily_pnl": row.daily_pnl,
            }
