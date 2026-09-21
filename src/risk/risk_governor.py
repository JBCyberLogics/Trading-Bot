"""Risk Governor state machine for adaptive risk management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timezone
from enum import Enum
from types import SimpleNamespace
from typing import Any, Dict, List

from loguru import logger


class RiskState(Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"
    HALT = "HALT"


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class Transition:
    timestamp: datetime
    from_state: str
    to_state: str
    reasons: List[str]


class RiskGovernor:
    """State machine that scales or halts risk based on account stress."""

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = config or {}
        risk_cfg = cfg.get("risk", cfg)

        self.max_daily_loss_pct = float(risk_cfg.get("max_daily_loss_pct", 3.0))
        self.max_weekly_loss_pct = float(risk_cfg.get("max_weekly_loss_pct", 6.0))
        self.max_drawdown_pct = float(risk_cfg.get("max_drawdown_pct", 8.0))
        self.max_consecutive_losses = int(risk_cfg.get("max_consecutive_losses", 5))
        self.max_concurrent_positions = int(risk_cfg.get("max_concurrent_positions", 3))
        self.max_correlation = float(risk_cfg.get("max_correlation", 0.70))
        self.base_risk_percent = float(risk_cfg.get("base_risk_percent", 1.0))

        self.live_deployment_multiplier = float(risk_cfg.get("live_deployment_multiplier", 0.1))
        self.live_deployment_trades = 0
        self.live_deployment_target = int(risk_cfg.get("live_deployment_target", 20))
        self.rollout_confidence_floor = float(risk_cfg.get("rollout_confidence_floor", 0.70))

        self.current_state = RiskState.NORMAL
        self.state_since = _utcnow_naive()

        self.previous_balance: float = 0.0
        self.current_equity: float = 0.0
        self.daily_pnl: float = 0.0
        self.weekly_pnl: float = 0.0
        self.daily_start_balance: float | None = None
        self.weekly_start_balance: float | None = None
        self.daily_anchor: date | None = None
        self.weekly_anchor: tuple[int, int] | None = None

        self.peak_balance: float = 0.0
        self.current_drawdown: float = 0.0
        self.consecutive_losses: int = 0
        self.open_positions: List[Any] = []

        self.transition_history: List[Dict[str, Any]] = []
        logger.info(f"RiskGovernor initialized in {self.current_state.value} state")

    @staticmethod
    def _setup_confidence_to_ratio(setup: Any) -> float:
        confidence = float(getattr(setup, "confidence", 0.0))
        if confidence > 1.0:
            return confidence / 100.0
        return confidence

    def _maybe_reset_period_anchors(self, now: datetime, balance: float) -> None:
        if self.daily_anchor != now.date():
            self.daily_anchor = now.date()
            self.daily_start_balance = balance
            self.daily_pnl = 0.0

        week_key = now.isocalendar()[:2]
        if self.weekly_anchor != week_key:
            self.weekly_anchor = week_key
            self.weekly_start_balance = balance
            self.weekly_pnl = 0.0

    def update_account(self, balance: float, equity: float, positions: List[Any]) -> None:
        """Update account, PnL anchors, and drawdown metrics."""
        now = _utcnow_naive()
        balance = float(balance)
        equity = float(equity)

        self._maybe_reset_period_anchors(now, balance)

        if self.daily_start_balance is not None:
            self.daily_pnl = balance - self.daily_start_balance
        if self.weekly_start_balance is not None:
            self.weekly_pnl = balance - self.weekly_start_balance

        self.previous_balance = balance
        self.current_equity = equity
        self.open_positions = positions or []

        if balance > self.peak_balance:
            self.peak_balance = balance

        if self.peak_balance > 0:
            self.current_drawdown = ((self.peak_balance - balance) / self.peak_balance) * 100.0
        else:
            self.current_drawdown = 0.0

        self._check_state_transitions()

    def record_trade_outcome(self, outcome: str) -> None:
        """Update loss streak and rollout progress after a closed trade."""
        normalized = str(outcome or "").upper()
        if normalized == "LOSS":
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
            self.live_deployment_trades += 1
        self._check_state_transitions()

    def transition_to(self, new_state: RiskState, reasons: List[str]) -> None:
        old = self.current_state
        self.current_state = new_state
        self.state_since = _utcnow_naive()
        transition = {
            "timestamp": self.state_since,
            "from": old.value,
            "to": new_state.value,
            "reasons": list(reasons),
        }
        self.transition_history.append(transition)
        logger.warning(f"Risk state transition: {old.value} -> {new_state.value}")
        for reason in reasons:
            logger.warning(f"  - {reason}")

    def daily_loss_limit_exceeded(self) -> bool:
        if not self.daily_start_balance or self.daily_start_balance <= 0:
            return False
        loss_pct = max(0.0, (-self.daily_pnl / self.daily_start_balance) * 100.0)
        return loss_pct >= self.max_daily_loss_pct

    def weekly_loss_limit_exceeded(self) -> bool:
        if not self.weekly_start_balance or self.weekly_start_balance <= 0:
            return False
        loss_pct = max(0.0, (-self.weekly_pnl / self.weekly_start_balance) * 100.0)
        return loss_pct >= self.max_weekly_loss_pct

    def drawdown_limit_exceeded(self) -> bool:
        return self.current_drawdown >= self.max_drawdown_pct

    def _check_state_transitions(self) -> None:
        new_state = self.current_state
        reasons: List[str] = []

        if self.daily_loss_limit_exceeded():
            new_state = RiskState.HALT
            reasons.append("Daily loss limit exceeded")
        elif self.weekly_loss_limit_exceeded():
            new_state = RiskState.HALT
            reasons.append("Weekly loss limit exceeded")
        elif self.drawdown_limit_exceeded():
            new_state = RiskState.HALT
            reasons.append(f"Drawdown limit exceeded ({self.current_drawdown:.2f}%)")
        elif self.consecutive_losses >= self.max_consecutive_losses:
            new_state = RiskState.HALT
            reasons.append(f"Consecutive losses reached {self.consecutive_losses}")
        elif self.current_drawdown > self.max_drawdown_pct * 0.7:
            new_state = RiskState.DEFENSIVE
            reasons.append("Drawdown in defensive zone")
        elif self.consecutive_losses >= 3:
            new_state = RiskState.DEFENSIVE
            reasons.append("Three consecutive losses")
        elif self.daily_start_balance and self.daily_pnl < -(self.max_daily_loss_pct * 0.5 / 100.0) * self.daily_start_balance:
            new_state = RiskState.DEFENSIVE
            reasons.append("Daily loss above 50% of limit")
        elif self.consecutive_losses >= 2:
            new_state = RiskState.CAUTION
            reasons.append("Two consecutive losses")
        elif self.current_drawdown > self.max_drawdown_pct * 0.3:
            new_state = RiskState.CAUTION
            reasons.append("Drawdown in caution zone")
        elif len(self.open_positions) >= max(1, int(self.max_concurrent_positions * 0.7)):
            new_state = RiskState.CAUTION
            reasons.append("Near maximum concurrent positions")
        else:
            new_state = RiskState.NORMAL

        if new_state != self.current_state:
            self.transition_to(new_state, reasons or ["State normalization"])

    def get_position_size_multiplier(self) -> float:
        state_multipliers = {
            RiskState.NORMAL: 1.0,
            RiskState.CAUTION: 0.5,
            RiskState.DEFENSIVE: 0.25,
            RiskState.HALT: 0.0,
        }
        base = state_multipliers[self.current_state]

        if self.live_deployment_target <= 0:
            rollout = 1.0
        elif self.live_deployment_trades >= self.live_deployment_target:
            rollout = 1.0
        else:
            progress = self.live_deployment_trades / float(self.live_deployment_target)
            rollout = self.live_deployment_multiplier + progress * (1.0 - self.live_deployment_multiplier)

        return max(0.0, base * rollout)

    def get_confidence_threshold(self) -> float:
        thresholds = {
            RiskState.NORMAL: 0.70,
            RiskState.CAUTION: 0.80,
            RiskState.DEFENSIVE: 0.90,
            RiskState.HALT: 1.00,
        }
        threshold = thresholds[self.current_state]
        if self.live_deployment_trades < self.live_deployment_target:
            return max(float(self.rollout_confidence_floor), threshold)
        return threshold

    def can_trade(self) -> bool:
        return self.current_state is not RiskState.HALT

    def approve_trade(self, setup: Any, correlation_score: float = 0.0) -> bool:
        """Approve/reject trade by state, confidence, position and correlation."""
        if not self.can_trade():
            return False

        confidence_ratio = self._setup_confidence_to_ratio(setup)
        if confidence_ratio < self.get_confidence_threshold():
            return False

        if len(self.open_positions) >= self.max_concurrent_positions:
            return False

        if float(correlation_score) > self.max_correlation:
            return False

        return True

    def calculate_position_size(
        self,
        account_balance: float,
        stop_loss_pips: float,
        pip_value: float,
        confidence: float,
        regime_multiplier: float = 1.0,
        min_lot: float = 0.01,
    ) -> float:
        """Compute lots from base risk, state multiplier, confidence and regime."""
        stop_loss_pips = float(stop_loss_pips)
        pip_value = float(pip_value)
        if stop_loss_pips <= 0 or pip_value <= 0:
            return 0.0

        risk_amount = float(account_balance) * (self.base_risk_percent / 100.0)
        risk_amount *= self.get_position_size_multiplier()

        conf_ratio = confidence / 100.0 if confidence > 1.0 else confidence
        confidence_multiplier = min(max(conf_ratio / 0.70, 0.25), 1.50)
        risk_amount *= confidence_multiplier
        risk_amount *= float(regime_multiplier)

        risk_per_lot = stop_loss_pips * pip_value
        if risk_per_lot <= 0:
            return 0.0

        lots = risk_amount / risk_per_lot
        lots = round(lots * 100.0) / 100.0
        return max(float(min_lot), lots)

    def get_state_report(self) -> Dict[str, Any]:
        """Return a structured snapshot of risk state."""
        return {
            "state": self.current_state.value,
            "state_since": self.state_since.isoformat(),
            "daily_pnl": float(self.daily_pnl),
            "weekly_pnl": float(self.weekly_pnl),
            "drawdown": float(self.current_drawdown),
            "consecutive_losses": int(self.consecutive_losses),
            "open_positions": len(self.open_positions),
            "position_size_multiplier": float(self.get_position_size_multiplier()),
            "confidence_threshold": float(self.get_confidence_threshold()),
            "can_trade": self.can_trade(),
            "live_deployment_progress": f"{self.live_deployment_trades}/{self.live_deployment_target}",
        }
