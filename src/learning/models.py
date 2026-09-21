"""SQLAlchemy models and database access layer for trade and parameter tracking."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import create_engine
from sqlalchemy import or_
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()


def _utcnow_naive() -> datetime:
    """Return a UTC timestamp without tzinfo for SQLite compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Trade(Base):
    """Complete trade record with regime tagging and parameter version metadata."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    trade_id = Column(String, unique=True, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    symbol = Column(String, nullable=False)
    direction = Column(String, nullable=False)  # LONG or SHORT

    # Entry/exit
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float)
    stop_loss = Column(Float)
    take_profit_1 = Column(Float)
    take_profit_2 = Column(Float)
    lots = Column(Float)

    # Performance
    pnl = Column(Float)
    pnl_pips = Column(Float)
    r_multiple = Column(Float)
    outcome = Column(String)  # WIN, LOSS, BE
    exit_reason = Column(String)  # TP1_HIT, TP2_HIT, SL_HIT, MANUAL

    # Setup details
    bias_at_entry = Column(String)
    bias_confidence = Column(Integer)
    poi_type = Column(String)  # OB, FVG, etc.
    poi_id = Column(String)
    entry_zone_min = Column(Float)
    entry_zone_max = Column(Float)
    confidence_score = Column(Float)
    rr_ratio = Column(Float)

    # Regime tagging
    regime_trend = Column(String)  # STRONG, MODERATE, WEAK
    regime_trend_value = Column(Float)  # ADX value
    regime_volatility = Column(String)  # HIGH, MODERATE, LOW
    regime_volatility_value = Column(Float)  # ATR percentile
    regime_session = Column(String)  # LONDON_OPEN, NY_OPEN, ASIAN, etc.
    regime_spread = Column(String)  # TIGHT, NORMAL, WIDE
    regime_spread_value = Column(Float)  # spread in pips

    # Execution quality
    slippage_pips = Column(Float)
    fill_time_ms = Column(Integer)
    order_type = Column(String)  # LIMIT, MARKET
    spread_cost_pips = Column(Float)

    # Metadata
    parameter_version_id = Column(String)
    challenger_id = Column(String)
    version_id = Column(String)  # Backward-compatible alias from Phase 3
    created_at = Column(DateTime, default=_utcnow_naive, nullable=False)

    __table_args__ = (
        Index("idx_trade_timestamp", "timestamp"),
        Index("idx_trade_symbol", "symbol"),
        Index("idx_trade_regime", "regime_trend", "regime_volatility"),
        Index("idx_trade_outcome", "outcome"),
        Index("idx_trade_parameter_version", "parameter_version_id"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to plain dictionary."""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class ParameterVersion(Base):
    """Track parameter versions and champion/challenger lifecycle performance."""

    __tablename__ = "parameter_versions"

    id = Column(Integer, primary_key=True)
    version_id = Column(String, unique=True, nullable=False)
    parent_version = Column(String)
    parameters_json = Column(String)  # JSON string of parameters

    # A/B testing metadata
    is_champion = Column(Boolean, default=False, nullable=False)
    is_challenger = Column(Boolean, default=False, nullable=False)
    challenger_type = Column(String)  # SENSITIVITY, EVOLUTIONARY, MANUAL
    hypothesis = Column(String)
    allocation_percent = Column(Integer, default=0, nullable=False)

    # Test status
    test_start_date = Column(DateTime)
    test_end_date = Column(DateTime)
    test_trades_required = Column(Integer, default=100, nullable=False)
    test_trades_completed = Column(Integer, default=0, nullable=False)

    # Performance metrics
    trades_count = Column(Integer, default=0, nullable=False)
    win_count = Column(Integer, default=0, nullable=False)
    total_pnl = Column(Float, default=0, nullable=False)
    expectancy_r = Column(Float)
    profit_factor = Column(Float)
    max_drawdown = Column(Float)
    sharpe_ratio = Column(Float)
    performance_by_regime = Column(String)  # JSON dict

    # Lifecycle state
    promotion_date = Column(DateTime)
    demotion_date = Column(DateTime)
    rejection_reason = Column(String)

    created_at = Column(DateTime, default=_utcnow_naive, nullable=False)
    last_updated = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive, nullable=False)

    __table_args__ = (
        Index("idx_parameter_champion", "is_champion"),
        Index("idx_parameter_challenger", "is_challenger"),
    )

    def get_parameters(self) -> Dict[str, Any]:
        """Deserialize parameters from JSON string."""
        if not self.parameters_json:
            return {}
        loaded = json.loads(self.parameters_json)
        if isinstance(loaded, dict):
            return loaded
        return {}

    def set_parameters(self, params_dict: Dict[str, Any]) -> None:
        """Serialize parameter dictionary into JSON."""
        self.parameters_json = json.dumps(params_dict, sort_keys=True)


class DailyPerformance(Base):
    """Daily performance summary by regime and champion/challenger split."""

    __tablename__ = "daily_performance"

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)

    # Overall
    total_trades = Column(Integer, default=0, nullable=False)
    winning_trades = Column(Integer, default=0, nullable=False)
    total_pnl = Column(Float, default=0, nullable=False)

    # Champion vs challenger
    champion_trades = Column(Integer, default=0, nullable=False)
    champion_wins = Column(Integer, default=0, nullable=False)
    challenger_trades = Column(Integer, default=0, nullable=False)
    challenger_wins = Column(Integer, default=0, nullable=False)

    # Flexible breakdowns encoded as JSON strings
    performance_by_regime = Column(String)
    performance_by_poi = Column(String)
    performance_by_session = Column(String)

    created_at = Column(DateTime, default=_utcnow_naive, nullable=False)

    __table_args__ = (Index("idx_daily_date", "date"),)


class SensitivityAnalysis(Base):
    """Track parameter impact by regime over time."""

    __tablename__ = "sensitivity_analysis"

    id = Column(Integer, primary_key=True)
    parameter_name = Column(String, nullable=False)
    regime = Column(String, nullable=False)
    current_value = Column(Float)
    impact_score = Column(Float, default=0)
    confidence = Column(Float, default=0)
    sample_size = Column(Integer, default=0, nullable=False)
    last_updated = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive, nullable=False)

    __table_args__ = (
        Index("idx_sensitivity_param_regime", "parameter_name", "regime", unique=True),
    )


class TradeDatabase:
    """Database interface for trade operations and parameter versioning."""

    def __init__(self, db_path: str = "sqlite:///trades.db") -> None:
        self.db_path = db_path
        self.engine = create_engine(db_path)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    @staticmethod
    def _sync_trade_version_aliases(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Mirror legacy and new version fields for backward compatibility."""
        data = dict(payload)
        if data.get("parameter_version_id") and not data.get("version_id"):
            data["version_id"] = data["parameter_version_id"]
        if data.get("version_id") and not data.get("parameter_version_id"):
            data["parameter_version_id"] = data["version_id"]
        return data

    def add_trade(self, trade_data: Dict[str, Any]) -> int:
        """Add a new trade record and return row id."""
        session = self.Session()
        try:
            payload = self._sync_trade_version_aliases(trade_data)
            trade = Trade(**payload)
            session.add(trade)
            session.commit()
            return int(trade.id)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_trade_exit(self, trade_id: str, exit_data: Dict[str, Any]) -> bool:
        """Update trade with execution/exit fields. Returns True when updated."""
        session = self.Session()
        try:
            trade = session.query(Trade).filter_by(trade_id=trade_id).first()
            if trade is None:
                return False
            for key, value in exit_data.items():
                setattr(trade, key, value)
            if "parameter_version_id" in exit_data or "version_id" in exit_data:
                trade.parameter_version_id = exit_data.get("parameter_version_id", trade.parameter_version_id)
                trade.version_id = exit_data.get("version_id", trade.version_id or trade.parameter_version_id)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_trade_by_trade_id(self, trade_id: str) -> Optional[Trade]:
        """Fetch trade by external trade id."""
        session = self.Session()
        try:
            return session.query(Trade).filter_by(trade_id=trade_id).first()
        finally:
            session.close()

    def get_trades_by_regime(
        self,
        regime_filters: Dict[str, str],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        version_id: Optional[str] = None,
    ) -> Iterable[Trade]:
        """Query trades by regime criteria and optional parameter version."""
        session = self.Session()
        try:
            query = session.query(Trade)

            if "trend" in regime_filters:
                query = query.filter_by(regime_trend=regime_filters["trend"])
            if "volatility" in regime_filters:
                query = query.filter_by(regime_volatility=regime_filters["volatility"])
            if "session" in regime_filters:
                query = query.filter_by(regime_session=regime_filters["session"])

            if version_id:
                query = query.filter(
                    or_(
                        Trade.parameter_version_id == version_id,
                        Trade.version_id == version_id,
                    )
                )

            if start_date is not None:
                query = query.filter(Trade.timestamp >= start_date)
            if end_date is not None:
                query = query.filter(Trade.timestamp <= end_date)

            return query.all()
        finally:
            session.close()

    def get_champion_parameters(self) -> Optional[Dict[str, Any]]:
        """Get currently active champion parameters (dictionary)."""
        champion = self.get_champion_version()
        return champion.get_parameters() if champion else None

    def get_champion_version(self) -> Optional[ParameterVersion]:
        """Get currently active champion row."""
        session = self.Session()
        try:
            return session.query(ParameterVersion).filter_by(is_champion=True).first()
        finally:
            session.close()

    def set_champion_parameters(self, version_id: str) -> bool:
        """Set champion version and demote previous champion if present."""
        session = self.Session()
        try:
            now = _utcnow_naive()
            session.query(ParameterVersion).filter_by(is_champion=True).update(
                {"is_champion": False, "demotion_date": now}
            )
            updated = session.query(ParameterVersion).filter_by(version_id=version_id).update(
                {
                    "is_champion": True,
                    "is_challenger": False,
                    "promotion_date": now,
                    "demotion_date": None,
                }
            )
            session.commit()
            return bool(updated)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_active_challengers(self) -> List[ParameterVersion]:
        """Get active challengers that are still under evaluation."""
        session = self.Session()
        try:
            return (
                session.query(ParameterVersion)
                .filter(
                    ParameterVersion.is_challenger.is_(True),
                    ParameterVersion.test_end_date.is_(None),
                )
                .all()
            )
        finally:
            session.close()

    def add_challenger(self, version_data: Dict[str, Any]) -> ParameterVersion:
        """Add a parameter version row (challenger or initial champion)."""
        session = self.Session()
        try:
            payload = dict(version_data)
            if "parameters_json" not in payload and "parameters" in payload:
                payload["parameters_json"] = json.dumps(payload.pop("parameters"), sort_keys=True)
            if isinstance(payload.get("parameters_json"), dict):
                payload["parameters_json"] = json.dumps(payload["parameters_json"], sort_keys=True)
            version = ParameterVersion(**payload)
            session.add(version)
            session.commit()
            return version
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _trades_for_version(self, session, version_id: str) -> List[Trade]:
        return (
            session.query(Trade)
            .filter(
                or_(
                    Trade.parameter_version_id == version_id,
                    Trade.version_id == version_id,
                )
            )
            .all()
        )

    def update_parameter_performance(self, version_id: str) -> bool:
        """Recalculate performance metrics for a parameter version."""
        session = self.Session()
        try:
            version = session.query(ParameterVersion).filter_by(version_id=version_id).first()
            if version is None:
                return False

            trades = self._trades_for_version(session, version_id)
            if not trades:
                version.trades_count = 0
                version.win_count = 0
                version.total_pnl = 0.0
                version.expectancy_r = 0.0
                version.profit_factor = 0.0
                version.max_drawdown = 0.0
                version.performance_by_regime = json.dumps({})
                version.last_updated = _utcnow_naive()
                session.commit()
                return True

            wins = [t for t in trades if t.outcome == "WIN"]
            losses = [t for t in trades if t.outcome == "LOSS"]
            r_values = [float(t.r_multiple) for t in trades if t.r_multiple is not None]

            version.trades_count = len(trades)
            version.test_trades_completed = len(trades)
            version.win_count = len(wins)
            version.total_pnl = float(sum(float(t.pnl or 0.0) for t in trades))
            version.expectancy_r = float(sum(r_values) / len(r_values)) if r_values else 0.0

            gross_profit = float(sum(float(t.pnl or 0.0) for t in wins))
            gross_loss = float(abs(sum(float(t.pnl or 0.0) for t in losses)))
            version.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

            cumulative = 0.0
            peak = 0.0
            max_dd = 0.0
            for trade in sorted(trades, key=lambda x: x.timestamp):
                cumulative += float(trade.pnl or 0.0)
                peak = max(peak, cumulative)
                max_dd = max(max_dd, peak - cumulative)
            version.max_drawdown = max_dd

            regime_perf: Dict[str, Dict[str, float]] = {}
            for trade in trades:
                regime_key = f"{trade.regime_trend}_{trade.regime_volatility}"
                if regime_key not in regime_perf:
                    regime_perf[regime_key] = {"wins": 0.0, "total": 0.0, "pnl": 0.0}
                regime_perf[regime_key]["total"] += 1.0
                regime_perf[regime_key]["pnl"] += float(trade.pnl or 0.0)
                if trade.outcome == "WIN":
                    regime_perf[regime_key]["wins"] += 1.0

            for regime_key in regime_perf:
                total = regime_perf[regime_key]["total"]
                regime_perf[regime_key]["win_rate"] = (regime_perf[regime_key]["wins"] / total) if total else 0.0

            version.performance_by_regime = json.dumps(regime_perf, sort_keys=True)
            version.last_updated = _utcnow_naive()
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
