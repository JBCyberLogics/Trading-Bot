"""Weekly evolutionary optimization scheduler."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any, Dict

from loguru import logger
import yaml


def _utcnow_naive() -> datetime:
    """Return a UTC timestamp without tzinfo for SQLite compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WeeklyEvolution:
    """Runs periodic evolutionary optimization and challenger creation."""

    def __init__(self, db, champion_challenger, genetic_optimizer, config_path: str) -> None:
        self.db = db
        self.cc = champion_challenger
        self.genetic = genetic_optimizer
        self.config_path = config_path
        self.last_run: datetime | None = None
        logger.info("WeeklyEvolution initialized")

    def should_run(self) -> bool:
        """Check whether a weekly evolution run should execute."""
        now = _utcnow_naive()
        if self.last_run is None:
            return True

        # Weekly cadence at/after Sunday 00:00 UTC.
        if now.weekday() == 6 and now.hour == 0:
            return (now - self.last_run) >= timedelta(days=6)

        return (now - self.last_run) >= timedelta(days=7)

    def get_parameter_ranges_from_config(self) -> Dict[str, Dict[str, float]]:
        """Extract optimization ranges from parameters config.

        Numeric values are converted to +/-20% bounded ranges with a sensible step.
        """
        config_file = Path(self.config_path)
        if not config_file.exists():
            return {
                "rsi_oversold": {"min": 20.0, "max": 40.0, "step": 1.0},
                "rsi_overbought": {"min": 60.0, "max": 80.0, "step": 1.0},
                "fvg_min_pips": {"min": 1.0, "max": 5.0, "step": 0.5},
                "min_rr": {"min": 1.2, "max": 3.0, "step": 0.1},
                "impulse_atr_mult": {"min": 1.0, "max": 2.0, "step": 0.1},
                "trailing_activation_r": {"min": 0.8, "max": 2.5, "step": 0.1},
            }

        loaded = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return {}

        ranges: Dict[str, Dict[str, float]] = {}
        for key, value in loaded.items():
            if not isinstance(value, (int, float)):
                continue

            current = float(value)
            delta = max(abs(current) * 0.20, 0.1)
            minimum = current - delta
            maximum = current + delta

            if abs(current) >= 10:
                step = 1.0
            elif abs(current) >= 1:
                step = 0.1
            else:
                step = 0.01

            ranges[key] = {
                "min": float(minimum),
                "max": float(maximum),
                "step": float(step),
            }

        return ranges

    def run_evolution(self):
        """Execute weekly evolution run and create challenger if improvement is meaningful."""
        logger.info("Starting weekly evolution")

        ranges = self.get_parameter_ranges_from_config()
        if not ranges:
            logger.warning("No numeric parameters found for evolution")
            self.last_run = _utcnow_naive()
            return None

        self.genetic.parameter_ranges = ranges
        best_individual, best_fitness = self.genetic.evolve()

        champion = self.cc.champion
        if champion is None:
            logger.error("No champion found")
            self.last_run = _utcnow_naive()
            return None

        champion_params = champion.get_parameters()
        champion_fitness = self.genetic.fitness(champion_params)

        if champion_fitness > 0:
            improvement = (best_fitness - champion_fitness) / champion_fitness
        else:
            improvement = 0.0

        self.last_run = _utcnow_naive()
        if improvement <= 0.05:
            logger.info(f"Evolution improvement too small: {improvement:.1%}")
            return None

        logger.info(f"Evolution found {improvement:.1%} predicted improvement")
        hypothesis = f"Evolutionary discovery: {improvement:.1%} predicted improvement"
        return self.cc.create_challenger_from_evolution(best_individual, [champion.version_id], hypothesis)

    def check_and_run(self):
        """Run weekly evolution if schedule says it is due."""
        if self.should_run():
            return self.run_evolution()
        return None
