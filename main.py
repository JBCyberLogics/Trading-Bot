#!/usr/bin/env python3
"""Main runtime entrypoint for the SMC/ICT bot (Phases 1-6)."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Dict

from loguru import logger
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_config
from src.learning.bootstrap_validator import BootstrapValidator
from src.learning.champion_vs_challenger import ChampionChallenger
from src.learning.genetic_optimizer import GeneticOptimizer
from src.learning.models import TradeDatabase
from src.learning.sensitivity_analyzer import SensitivityAnalyzer
from src.learning.weekly_evolution import WeeklyEvolution
from src.monitoring.logger import setup_logging
from src.risk.risk_manager import RiskManager


def _default_parameter_ranges() -> Dict[str, Dict[str, float]]:
    return {
        "rsi_oversold": {"min": 20.0, "max": 40.0, "step": 1.0},
        "rsi_overbought": {"min": 60.0, "max": 80.0, "step": 1.0},
        "fvg_min_pips": {"min": 1.0, "max": 5.0, "step": 0.5},
        "min_rr": {"min": 1.2, "max": 3.0, "step": 0.1},
        "impulse_atr_mult": {"min": 1.0, "max": 2.0, "step": 0.1},
        "trailing_activation_r": {"min": 0.8, "max": 2.5, "step": 0.1},
    }


def _load_user_selected_symbol(project_root: Path) -> str | None:
    user_cfg_path = project_root / "config" / "user_config.yaml"
    if not user_cfg_path.exists():
        return None
    try:
        payload = yaml.safe_load(user_cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    symbol = str(payload.get("symbol", "")).upper().strip()
    return symbol or None


class RuntimeBootstrap:
    """Wires Phase 4 learning + Phase 5 risk stack for CLI entry operations."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.config = load_config(project_root / "config")
        setup_logging(project_root / "logs")

        db_path = Path(self.config.get("db_path", "database/trades.db"))
        if not db_path.is_absolute():
            db_path = project_root / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db = TradeDatabase(f"sqlite:///{db_path}")

        self.sensitivity = SensitivityAnalyzer(self.db)
        self.bootstrap = BootstrapValidator()
        self.champion_challenger = ChampionChallenger(
            self.db,
            self.bootstrap,
            config_path=str(project_root / "config" / "parameters.yaml"),
        )
        self.genetic = GeneticOptimizer(self.db, _default_parameter_ranges())
        self.weekly = WeeklyEvolution(
            self.db,
            self.champion_challenger,
            self.genetic,
            str(project_root / "config" / "parameters.yaml"),
        )

        self.risk_manager = RiskManager(self.config, db_session=self.db)

    def run_daily_learning_tasks(self) -> None:
        self.sensitivity.analyze_parameter_sensitivity("rsi_oversold")
        self.champion_challenger.evaluate_challengers()
        logger.info("Daily learning tasks completed")

    def run_weekly_evolution_if_due(self) -> None:
        created = self.weekly.check_and_run()
        if created is not None:
            logger.info("Weekly evolution created challenger: {}", created.version_id)
        else:
            logger.info("Weekly evolution not triggered or no qualifying improvement")

    def print_risk_report(self) -> None:
        report = self.risk_manager.get_risk_report()
        logger.info("Risk report: {}", report)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SMC/ICT bot runtime entrypoint")
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Run learning tasks, risk report, then start live runtime",
    )
    parser.add_argument("--learning-daily", action="store_true", help="Run Phase 4 daily learning tasks")
    parser.add_argument("--learning-weekly", action="store_true", help="Run Phase 4 weekly evolution check")
    parser.add_argument("--risk-report", action="store_true", help="Print current risk subsystem report")

    parser.add_argument("--live", action="store_true", help="Run direct live deployment runtime")
    parser.add_argument("--symbol", default=None, help="Live mode symbol override")
    parser.add_argument("--timeframe", default=None, help="Live mode timeframe override")
    parser.add_argument("--poll-seconds", type=int, default=None, help="Live mode poll interval")
    parser.add_argument("--bars", type=int, default=300, help="Live mode candles to pull")
    parser.add_argument("--max-loops", type=int, default=0, help="Live mode loop cap (0=infinite)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    bootstrap = RuntimeBootstrap(PROJECT_ROOT)
    logger.info("Runtime initialized")

    has_explicit_action = any([args.run_all, args.learning_daily, args.learning_weekly, args.risk_report, args.live])
    if args.run_all or not has_explicit_action:
        if not has_explicit_action:
            logger.info("No action selected. Defaulting to full startup sequence.")
        args.learning_daily = True
        args.learning_weekly = True
        args.risk_report = True
        args.live = True

    if args.learning_daily:
        bootstrap.run_daily_learning_tasks()
    if args.learning_weekly:
        bootstrap.run_weekly_evolution_if_due()
    if args.risk_report:
        bootstrap.print_risk_report()

    if args.live:
        from scripts.live_deploy import LiveDeploymentBot

        deployment_cfg = bootstrap.config.get("deployment", {})

        persisted_symbol = _load_user_selected_symbol(PROJECT_ROOT)
        symbol = (args.symbol or persisted_symbol or deployment_cfg.get("default_symbol") or "EURUSD").upper()
        timeframe = (args.timeframe or deployment_cfg.get("default_timeframe") or "H1").upper()
        poll_interval = args.poll_seconds or int(deployment_cfg.get("poll_interval_seconds", 5))

        bot = LiveDeploymentBot(
            bootstrap.config,
            symbol=symbol,
            timeframe=timeframe,
            poll_interval_seconds=poll_interval,
            bar_count=args.bars,
            max_loops=args.max_loops,
        )
        return bot.run()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
