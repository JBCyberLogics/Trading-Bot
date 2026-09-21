"""Bootstrap statistical validation for champion-challenger comparisons."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import numpy as np
from loguru import logger


class BootstrapValidator:
    """Statistical validation using bootstrap resampling."""

    def __init__(self, n_iterations: int = 10_000, confidence_level: float = 0.95, seed: int | None = None) -> None:
        self.n_iterations = int(n_iterations)
        self.confidence_level = float(confidence_level)
        self._rng = np.random.default_rng(seed)
        logger.info(f"BootstrapValidator initialized with {self.n_iterations} iterations")

    def bootstrap_sample(self, data: Sequence[Any]) -> List[Any]:
        """Generate a bootstrap sample (resample with replacement)."""
        n = len(data)
        if n == 0:
            return []
        indices = self._rng.integers(0, n, size=n)
        return [data[i] for i in indices]

    @staticmethod
    def calculate_expectancy(trades: Sequence[Any]) -> float:
        """Calculate expectancy in R multiples."""
        if not trades:
            return 0.0
        r_values = [float(t.r_multiple) for t in trades if getattr(t, "r_multiple", None) is not None]
        return float(np.mean(r_values)) if r_values else 0.0

    def validate_challenger(
        self,
        champion_trades: Sequence[Any],
        challenger_trades: Sequence[Any],
        min_sample_size: int = 30,
        min_practical_improvement: float = 0.10,
    ) -> Dict[str, Any]:
        """Validate whether challenger statistically and practically outperforms champion."""
        if len(champion_trades) < min_sample_size or len(challenger_trades) < min_sample_size:
            logger.warning("Insufficient trades for validation")
            return {
                "passes": False,
                "reason": "Insufficient sample size",
                "confidence": 0.0,
                "actual_diff": 0.0,
                "champ_exp": 0.0,
                "chall_exp": 0.0,
                "pct_improvement": 0.0,
                "n_iterations": self.n_iterations,
                "threshold": self.confidence_level,
            }

        champ_exp = self.calculate_expectancy(champion_trades)
        chall_exp = self.calculate_expectancy(challenger_trades)
        actual_diff = chall_exp - champ_exp

        if actual_diff <= 0:
            logger.info(f"Challenger underperforms champion: {actual_diff:.4f}R")
            return {
                "passes": False,
                "reason": "No improvement",
                "confidence": 0.0,
                "actual_diff": actual_diff,
                "champ_exp": champ_exp,
                "chall_exp": chall_exp,
                "pct_improvement": (actual_diff / abs(champ_exp)) if champ_exp != 0 else 0.0,
                "n_iterations": self.n_iterations,
                "threshold": self.confidence_level,
            }

        better_count = 0
        for _ in range(self.n_iterations):
            champ_sample = self.bootstrap_sample(champion_trades)
            chall_sample = self.bootstrap_sample(challenger_trades)
            if self.calculate_expectancy(chall_sample) > self.calculate_expectancy(champ_sample):
                better_count += 1

        confidence = float(better_count / self.n_iterations)
        pct_improvement = float(actual_diff / abs(champ_exp)) if champ_exp != 0 else float("inf")

        passes = confidence >= self.confidence_level and pct_improvement >= min_practical_improvement
        result = {
            "passes": bool(passes),
            "confidence": confidence,
            "actual_diff": actual_diff,
            "champ_exp": champ_exp,
            "chall_exp": chall_exp,
            "pct_improvement": pct_improvement,
            "n_iterations": self.n_iterations,
            "threshold": self.confidence_level,
        }

        if passes:
            logger.info(f"Challenger validated with {confidence:.1%} confidence")
        else:
            logger.info(f"Challenger failed validation: {confidence:.1%} confidence")

        return result

    def validate_by_regime(
        self,
        champion_trades_by_regime: Dict[str, Sequence[Any]],
        challenger_trades_by_regime: Dict[str, Sequence[Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Validate challenger performance across multiple regimes."""
        results: Dict[str, Dict[str, Any]] = {}
        all_regimes = set(champion_trades_by_regime.keys()) | set(challenger_trades_by_regime.keys())

        for regime in all_regimes:
            champ_trades = champion_trades_by_regime.get(regime, [])
            chall_trades = challenger_trades_by_regime.get(regime, [])
            if len(champ_trades) < 10 or len(chall_trades) < 10:
                results[regime] = {
                    "passes": False,
                    "reason": "Insufficient data",
                    "champ_trades": len(champ_trades),
                    "chall_trades": len(chall_trades),
                }
                continue
            results[regime] = self.validate_challenger(champ_trades, chall_trades, min_sample_size=10)

        return results
