"""Parameter sensitivity analysis for self-learning."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, List

import numpy as np
from loguru import logger


class SensitivityAnalyzer:
    """Analyzes how parameter changes impact performance by market regime."""

    def __init__(self, db_session, min_trades_for_analysis: int = 50) -> None:
        self.db = db_session
        self.min_trades_for_analysis = int(min_trades_for_analysis)
        self.cache: Dict[str, Dict[str, Any]] = {}
        logger.info("SensitivityAnalyzer initialized")

    @contextmanager
    def _session_scope(self):
        """Yield a SQLAlchemy session from either Session or TradeDatabase."""
        if all(hasattr(self.db, attr) for attr in ("query", "add", "commit")):
            yield self.db
            return

        session_factory = getattr(self.db, "Session", None)
        if session_factory is None:
            raise TypeError("db_session must be a SQLAlchemy session or TradeDatabase")

        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @staticmethod
    def _version_id_for_trade(trade) -> str:
        return str(getattr(trade, "parameter_version_id", None) or getattr(trade, "version_id", None) or "UNKNOWN")

    def analyze_parameter_sensitivity(
        self,
        parameter_name: str,
        parameter_range: Dict[str, float] | None = None,
    ) -> Dict[str, Dict[str, float]]:
        """Compute sensitivity impact scores by regime and persist them."""
        from .models import SensitivityAnalysis
        from .models import Trade

        with self._session_scope() as session:
            trades: List[Trade] = session.query(Trade).filter(Trade.r_multiple.isnot(None)).all()
            if len(trades) < self.min_trades_for_analysis:
                logger.warning(
                    f"Insufficient trades for sensitivity analysis: {len(trades)} < {self.min_trades_for_analysis}"
                )
                return {}

            regimes: Dict[str, List[Trade]] = {}
            for trade in trades:
                regime_key = f"{trade.regime_trend}_{trade.regime_volatility}"
                regimes.setdefault(regime_key, []).append(trade)

            results: Dict[str, Dict[str, float]] = {}
            for regime_key, regime_trades in regimes.items():
                if len(regime_trades) < 30:
                    continue

                baseline_values = [float(t.r_multiple) for t in regime_trades if t.r_multiple is not None]
                if not baseline_values:
                    continue
                baseline_exp = float(np.mean(baseline_values))

                versions: Dict[str, List[Trade]] = {}
                for trade in regime_trades:
                    versions.setdefault(self._version_id_for_trade(trade), []).append(trade)

                impact_scores: List[float] = []
                for version_trades in versions.values():
                    if len(version_trades) < 10:
                        continue
                    version_values = [float(t.r_multiple) for t in version_trades if t.r_multiple is not None]
                    if not version_values:
                        continue
                    version_exp = float(np.mean(version_values))
                    if baseline_exp != 0:
                        impact = (version_exp - baseline_exp) / abs(baseline_exp)
                    else:
                        impact = version_exp - baseline_exp
                    impact_scores.append(float(impact))

                if not impact_scores:
                    continue

                # Use dominant directional impact so opposite variants do not cancel to zero.
                avg_impact = float(max(impact_scores, key=lambda value: abs(value)))
                confidence = float(min(1.0, len(regime_trades) / 100.0))

                row = session.query(SensitivityAnalysis).filter_by(
                    parameter_name=parameter_name,
                    regime=regime_key,
                ).first()
                if row is None:
                    row = SensitivityAnalysis(parameter_name=parameter_name, regime=regime_key)

                row.current_value = parameter_range.get("current") if parameter_range else None
                row.impact_score = avg_impact
                row.sample_size = len(regime_trades)
                row.confidence = confidence
                session.add(row)

                results[regime_key] = {
                    "impact": avg_impact,
                    "sample_size": float(len(regime_trades)),
                    "confidence": confidence,
                }

            session.commit()
            self.cache[parameter_name] = results
            logger.info(f"Sensitivity analysis completed for {parameter_name}")
            return results

    def get_high_impact_parameters(
        self,
        threshold: float = 0.05,
        min_confidence: float = 0.7,
    ) -> List[Dict[str, Any]]:
        """Return parameters whose absolute impact exceeds threshold."""
        from .models import SensitivityAnalysis

        with self._session_scope() as session:
            rows = session.query(SensitivityAnalysis).all()
            high_impact: List[Dict[str, Any]] = []
            for row in rows:
                impact = float(row.impact_score or 0.0)
                confidence = float(row.confidence or 0.0)
                if abs(impact) > threshold and confidence >= min_confidence:
                    high_impact.append(
                        {
                            "parameter": row.parameter_name,
                            "regime": row.regime,
                            "impact": impact,
                            "confidence": confidence,
                            "sample_size": int(row.sample_size or 0),
                        }
                    )
            return high_impact

    def suggest_parameter_tweaks(
        self,
        current_params: Dict[str, Any],
        threshold: float = 0.03,
        min_confidence: float = 0.7,
    ) -> List[Dict[str, Any]]:
        """Generate simple 10% up/down tweak suggestions from sensitivity scores."""
        from .models import SensitivityAnalysis

        with self._session_scope() as session:
            rows = session.query(SensitivityAnalysis).all()
            suggestions: List[Dict[str, Any]] = []

            for row in rows:
                param_name = row.parameter_name
                if param_name not in current_params:
                    continue

                current_value = current_params[param_name]
                if not isinstance(current_value, (int, float)):
                    continue

                impact = float(row.impact_score or 0.0)
                confidence = float(row.confidence or 0.0)
                if abs(impact) <= threshold or confidence < min_confidence:
                    continue

                if impact > 0:
                    suggested = float(current_value) * 1.10
                    reason = f"Positive impact in {row.regime} ({impact:.2f})"
                else:
                    suggested = float(current_value) * 0.90
                    reason = f"Negative impact in {row.regime} ({impact:.2f})"

                suggestions.append(
                    {
                        "parameter": param_name,
                        "current": float(current_value),
                        "suggested": float(suggested),
                        "regime": row.regime,
                        "reason": reason,
                        "confidence": confidence,
                    }
                )

            return suggestions
