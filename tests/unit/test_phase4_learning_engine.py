"""Unit tests for Phase 4 self-learning engine components."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from src.learning.bootstrap_validator import BootstrapValidator
from src.learning.champion_vs_challenger import ChampionChallenger
from src.learning.genetic_optimizer import GeneticOptimizer
from src.learning.models import ParameterVersion
from src.learning.models import TradeDatabase
from src.learning.sensitivity_analyzer import SensitivityAnalyzer
from src.learning.weekly_evolution import WeeklyEvolution


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MockTrade:
    def __init__(self, r_multiple: float):
        self.r_multiple = r_multiple


class TestPhase4LearningEngine(unittest.TestCase):
    def setUp(self):
        self.db = TradeDatabase("sqlite:///:memory:")

    def _seed_version(self, version_id: str, is_champion: bool = False, is_challenger: bool = False):
        return self.db.add_challenger(
            {
                "version_id": version_id,
                "parameters_json": "{}",
                "is_champion": is_champion,
                "is_challenger": is_challenger,
            }
        )

    def _seed_trade(self, trade_id: str, version_id: str, regime: str, r_multiple: float):
        trend, vol = regime.split("_")
        self.db.add_trade(
            {
                "trade_id": trade_id,
                "timestamp": _utcnow_naive(),
                "symbol": "EURUSD",
                "direction": "LONG",
                "entry_price": 1.1000,
                "stop_loss": 1.0990,
                "regime_trend": trend,
                "regime_volatility": vol,
                "regime_session": "LONDON_OPEN",
                "parameter_version_id": version_id,
                "version_id": version_id,
                "r_multiple": r_multiple,
                "pnl": r_multiple * 10.0,
                "outcome": "WIN" if r_multiple > 0 else "LOSS",
            }
        )

    def test_bootstrap_validator_confirms_better_challenger(self):
        validator = BootstrapValidator(n_iterations=500, confidence_level=0.9, seed=7)
        champion = [MockTrade(0.2) for _ in range(60)]
        challenger = [MockTrade(0.4) for _ in range(60)]

        result = validator.validate_challenger(champion, challenger)
        self.assertTrue(result["passes"])
        self.assertGreaterEqual(result["confidence"], 0.9)
        self.assertGreater(result["actual_diff"], 0)

    def test_sensitivity_analysis_and_suggestions(self):
        self._seed_version("CHAMPION_V1", is_champion=True)
        self._seed_version("CHALLENGER_A", is_challenger=True)

        for idx in range(40):
            self._seed_trade(f"C_{idx}", "CHAMPION_V1", "STRONG_HIGH", 0.20)
            self._seed_trade(f"X_{idx}", "CHALLENGER_A", "STRONG_HIGH", 0.40)

        analyzer = SensitivityAnalyzer(self.db, min_trades_for_analysis=20)
        results = analyzer.analyze_parameter_sensitivity("min_rr")
        self.assertIn("STRONG_HIGH", results)

        high_impact = analyzer.get_high_impact_parameters(threshold=0.01, min_confidence=0.2)
        self.assertTrue(any(row["parameter"] == "min_rr" for row in high_impact))

        suggestions = analyzer.suggest_parameter_tweaks({"min_rr": 2.0}, threshold=0.01, min_confidence=0.2)
        self.assertTrue(suggestions)
        self.assertGreater(suggestions[0]["suggested"], 2.0)

    def test_champion_challenger_lifecycle_and_promotion(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            params_path = Path(tmp_dir) / "parameters.yaml"
            params_path.write_text(yaml.safe_dump({"min_rr": 2.0}, sort_keys=True), encoding="utf-8")

            validator = BootstrapValidator(n_iterations=300, confidence_level=0.8, seed=3)
            cc = ChampionChallenger(
                self.db,
                validator,
                config_path=str(params_path),
                champion_allocation=0,
                max_challengers=2,
            )
            self.assertIsNotNone(cc.champion)
            self.assertEqual(cc.champion.version_id, "CHAMPION_V1")

            challenger = cc.create_challenger_from_sensitivity("min_rr", 2.2, "test tweak")
            self.assertIsNotNone(challenger)

            for idx in range(35):
                self._seed_trade(f"CH_{idx}", "CHAMPION_V1", "STRONG_HIGH", 0.20)
                self._seed_trade(f"CL_{idx}", challenger.version_id, "STRONG_HIGH", 0.40)

            session = self.db.Session()
            try:
                row = session.query(ParameterVersion).filter_by(version_id=challenger.version_id).first()
                self.assertIsNotNone(row)
                row.test_trades_required = 30
                row.test_trades_completed = 30
                session.commit()
            finally:
                session.close()

            selected = cc.select_parameter_version()
            self.assertIsNotNone(selected)

            results = cc.evaluate_challengers()
            self.assertTrue(results)
            self.assertTrue(results[0]["validation"]["passes"])

            promoted = cc.promote_challenger(challenger.version_id)
            self.assertTrue(promoted)

            champion = self.db.get_champion_version()
            self.assertIsNotNone(champion)
            self.assertEqual(champion.version_id, challenger.version_id)

            persisted_params = yaml.safe_load(params_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted_params["min_rr"], 2.2)

    def test_weekly_evolution_creates_challenger_when_improved(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            params_path = Path(tmp_dir) / "parameters.yaml"
            params_path.write_text(
                yaml.safe_dump({"min_rr": 2.0, "rsi_oversold": 30}, sort_keys=True),
                encoding="utf-8",
            )

            validator = BootstrapValidator(n_iterations=200, confidence_level=0.8, seed=9)
            cc = ChampionChallenger(self.db, validator, config_path=str(params_path))
            genetic = GeneticOptimizer(self.db, parameter_ranges={"min_rr": {"min": 1.0, "max": 3.0, "step": 0.1}})
            weekly = WeeklyEvolution(self.db, cc, genetic, str(params_path))

            with patch.object(genetic, "evolve", return_value=({"min_rr": 2.5, "rsi_oversold": 28}, 0.9)), patch.object(
                genetic,
                "fitness",
                return_value=0.5,
            ):
                challenger = weekly.check_and_run()

            self.assertIsNotNone(challenger)
            self.assertTrue(challenger.is_challenger)


if __name__ == "__main__":
    unittest.main()
