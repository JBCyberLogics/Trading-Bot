"""Unified risk manager that composes all Phase 5 risk controls."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Tuple

from loguru import logger

from .circuit_breakers import CircuitBreakers
from .correlation_manager import CorrelationManager
from .risk_governor import RiskGovernor
from .session_filters import NewsFilter
from .session_filters import SessionFilter


class RiskManager:
    """Pipeline-based trade approval and sizing coordinator."""

    def __init__(self, config: Dict[str, Any] | None = None, db_session=None):
        self.config = config or {}

        news_cfg = self.config.get("news", {}) if isinstance(self.config, dict) else {}
        news_api_key = self.config.get("news_api_key") or news_cfg.get("api_key")
        self.news_buffer_minutes = int(news_cfg.get("buffer_minutes", 15))
        hard_block_without_api = bool(news_cfg.get("hard_block_without_api", True))

        self.governor = RiskGovernor(self.config)
        self.correlation = CorrelationManager(db_session)
        self.session_filter = SessionFilter(self.config)
        self.news_filter = NewsFilter(
            api_key=news_api_key,
            hard_block_without_api=hard_block_without_api,
            endpoint=news_cfg.get("endpoint"),
            rapidapi_host=news_cfg.get("rapidapi_host"),
            rapidapi_app=news_cfg.get("rapidapi_app"),
        )
        self.circuits = CircuitBreakers(self.config)

        logger.info("Unified RiskManager initialized")

    @staticmethod
    def _point_size_for_symbol(symbol: str) -> float:
        symbol = str(symbol or "").upper()
        if "JPY" in symbol:
            return 0.001
        if symbol.startswith(("XAU", "XAG")):
            return 0.01
        return 0.00001

    @staticmethod
    def _extract_positions(positions: Any) -> List[Any]:
        if positions is None:
            return []
        if isinstance(positions, dict):
            payload = positions.get("positions", [])
            if isinstance(payload, list):
                return payload
            return []
        if isinstance(positions, list):
            return positions
        return []

    def _setup_to_dict(self, setup: Any) -> Dict[str, Any]:
        if isinstance(setup, dict):
            data = dict(setup)
        elif hasattr(setup, "__dict__"):
            data = dict(vars(setup))
        else:
            try:
                data = asdict(setup)
            except Exception:
                data = {}

        symbol = str(data.get("symbol") or "EURUSD").upper()
        entry_price = float(data.get("entry_price") or 0.0)
        stop_loss = float(data.get("stop_loss") or 0.0)
        if data.get("stop_loss_pips") is None:
            point_size = self._point_size_for_symbol(symbol)
            data["stop_loss_pips"] = abs(entry_price - stop_loss) / point_size if point_size > 0 else 0.0

        data.setdefault("pip_value", 10.0)
        data.setdefault("confidence", 0.7)
        data.setdefault("regime_multiplier", 1.0)
        data.setdefault("spread_pips", float(data.get("spread", 0.0) or 0.0))
        data.setdefault("avg_spread_pips", 1.0)
        data.setdefault("atr_pips", 10.0)
        data.setdefault("avg_atr", 10.0)
        data.setdefault("direction", "LONG")
        data["symbol"] = symbol
        return data

    def update_market_data(
        self,
        account_balance: float,
        equity: float,
        positions: Any,
        market_data: Dict[str, Any] | None = None,
    ) -> None:
        _ = market_data
        parsed_positions = self._extract_positions(positions)
        self.governor.update_account(account_balance, equity, parsed_positions)
        self.circuits.reset_circuits()

    def can_trade_symbol(self, symbol: str, setup_data: Dict[str, Any]) -> Tuple[bool, str]:
        if not self.governor.can_trade():
            return False, f"Risk state: {self.governor.current_state.value}"

        circuit_ok, circuit_reason = self.circuits.check_trade(setup_data)
        if not circuit_ok:
            return False, circuit_reason

        if self.news_filter.should_block_trade(symbol, buffer_minutes=self.news_buffer_minutes):
            return False, "News event blocking"

        return True, "OK"

    def approve_trade(self, setup: Any, current_positions: Any) -> Dict[str, Any] | None:
        setup_data = self._setup_to_dict(setup)
        symbol = setup_data["symbol"]

        can_trade, reason = self.can_trade_symbol(symbol, setup_data)
        if not can_trade:
            logger.debug(f"Trade rejected ({symbol}): {reason}")
            return None

        positions = self._extract_positions(current_positions)
        correlation_score = self.correlation.calculate_portfolio_correlation(positions, symbol)
        setup_ns = SimpleNamespace(confidence=setup_data["confidence"])
        if not self.governor.approve_trade(setup_ns, correlation_score=correlation_score):
            logger.debug(f"Trade rejected by governor ({symbol}) correlation={correlation_score:.3f}")
            return None

        account_balance = self.governor.previous_balance or 10000.0
        base_size = self.governor.calculate_position_size(
            account_balance=account_balance,
            stop_loss_pips=float(setup_data["stop_loss_pips"]),
            pip_value=float(setup_data["pip_value"]),
            confidence=float(setup_data["confidence"]),
            regime_multiplier=float(setup_data.get("regime_multiplier", 1.0)),
            min_lot=float(self.config.get("deployment", {}).get("min_live_lots", 0.01))
            if isinstance(self.config, dict)
            else 0.01,
        )

        session_mult = self.session_filter.get_session_multiplier()
        corr_penalty = self.correlation.get_correlation_penalty(
            positions,
            symbol,
            max_correlation=self.governor.max_correlation,
        )

        final_lots = base_size * session_mult * corr_penalty
        final_lots = round(final_lots * 100.0) / 100.0
        min_lot = float(self.config.get("deployment", {}).get("min_live_lots", 0.01)) if isinstance(self.config, dict) else 0.01
        final_lots = max(min_lot, final_lots)

        approved = dict(setup_data)
        approved["lots"] = final_lots
        approved["session_multiplier"] = session_mult
        approved["correlation_penalty"] = corr_penalty
        approved["correlation_score"] = correlation_score

        logger.info(
            f"Trade approved: {symbol} {approved.get('direction')} lots={final_lots:.2f} "
            f"(base={base_size:.2f}, session={session_mult:.2f}, corr={corr_penalty:.2f})"
        )
        return approved

    def record_trade_outcome(self, outcome: str, symbol: str | None = None) -> None:
        self.governor.record_trade_outcome(outcome)
        self.circuits.record_trade_outcome(outcome, symbol=symbol)

    def get_risk_report(self) -> Dict[str, Any]:
        return {
            "governor": self.governor.get_state_report(),
            "circuits": self.circuits.get_status(),
            "current_session": self.session_filter.get_current_session()[0],
            "session_multiplier": self.session_filter.get_session_multiplier(),
            "correlations_updated": self.correlation.last_update.isoformat() if self.correlation.last_update else None,
        }
