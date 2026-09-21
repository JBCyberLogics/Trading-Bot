"""Unit tests for correlation, filters, circuits, and unified risk manager."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from dataclasses import dataclass
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from src.risk.circuit_breakers import CircuitBreakers
from src.risk.correlation_manager import CorrelationManager
from src.risk.risk_governor import RiskState
from src.risk.risk_manager import RiskManager
from src.risk.session_filters import NewsFilter
from src.risk.session_filters import SessionFilter


class TestCorrelationManager(unittest.TestCase):
    def setUp(self):
        self.cm = CorrelationManager()

    def test_get_correlation_known_pair(self):
        value = self.cm.get_correlation("EURUSD", "GBPUSD")
        self.assertGreater(value, 0.7)

    def test_get_correlation_unknown_pair_defaults(self):
        value = self.cm.get_correlation("EURUSD", "USDCHF")
        self.assertEqual(value, 0.5)

    def test_get_correlation_same_and_reverse_and_update(self):
        self.assertEqual(self.cm.get_correlation("EURUSD", "EURUSD"), 1.0)
        self.assertAlmostEqual(self.cm.get_correlation("GBPUSD", "EURUSD"), 0.85, places=2)
        self.assertIsNone(self.cm.fetch_price_data(["EURUSD"]))
        self.cm.update_daily()
        self.assertIsNotNone(self.cm.last_update)

    def test_portfolio_correlation_and_penalty(self):
        positions = [{"symbol": "EURUSD"}, {"symbol": "GBPUSD"}]
        corr = self.cm.calculate_portfolio_correlation(positions, new_symbol="AUDUSD")
        self.assertGreater(corr, 0.0)

        penalty = self.cm.get_correlation_penalty(positions, "AUDUSD", max_correlation=0.6)
        self.assertLessEqual(penalty, 1.0)
        self.assertGreaterEqual(penalty, 0.3)

    def test_portfolio_with_object_positions(self):
        positions = [SimpleNamespace(symbol="EURUSD"), SimpleNamespace(symbol="USDJPY")]
        corr = self.cm.calculate_portfolio_correlation(positions)
        self.assertGreaterEqual(corr, 0.0)


class TestSessionAndNewsFilters(unittest.TestCase):
    def test_session_resolution(self):
        sf = SessionFilter()
        session, info = sf.get_current_session(datetime(2026, 1, 1, 13, 30))
        self.assertEqual(session, "NY_OPEN")
        self.assertGreater(info["multiplier"], 0)

    def test_session_allowed(self):
        sf = SessionFilter()
        with patch.object(sf, "get_current_session", return_value=("LONDON_OPEN", {"multiplier": 1.0})):
            self.assertTrue(sf.is_session_allowed())
            self.assertTrue(sf.is_session_allowed(["LONDON_OPEN", "NY_OPEN"]))
            self.assertFalse(sf.is_session_allowed(["ASIAN"]))

    def test_overnight_session_detection(self):
        sf = SessionFilter()
        session, _ = sf.get_current_session(datetime(2026, 1, 1, 23, 30))
        self.assertEqual(session, "ASIAN")

    def test_session_multiplier(self):
        sf = SessionFilter()
        with patch.object(sf, "get_current_session", return_value=("REGULAR", {"multiplier": 0.8})):
            self.assertAlmostEqual(sf.get_session_multiplier(), 0.8)

    def test_news_filter_blocks_without_config(self):
        nf = NewsFilter(api_key=None, hard_block_without_api=True, endpoint=None)
        self.assertTrue(nf.should_block_trade("EURUSD", buffer_minutes=60))

    def test_news_filter_without_api_when_not_hard_block(self):
        nf = NewsFilter(api_key=None, hard_block_without_api=False, endpoint=None)
        events = nf.fetch_news_events()
        self.assertEqual(events, [])
        self.assertEqual(nf.last_error, "Missing news API key")

    def test_news_filter_missing_endpoint(self):
        nf = NewsFilter(api_key="abc", hard_block_without_api=True, endpoint=None)
        events = nf.fetch_news_events()
        self.assertEqual(events[0]["event"], "NO_NEWS_ENDPOINT")

    @patch("src.risk.session_filters.requests.get")
    def test_news_filter_healthy_endpoint(self, mock_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"currency": "EUR", "impact": "HIGH", "time": "12:30", "event": "ECB"},
            {"currency": "USD", "impact": "LOW", "time": "12:00", "event": "Minor"},
        ]
        mock_get.return_value = response

        nf = NewsFilter(api_key="abc", hard_block_without_api=True, endpoint="https://example.com/news")
        events = nf.fetch_news_events()
        self.assertIsNone(nf.last_error)
        self.assertIsInstance(events, list)

    @patch("src.risk.session_filters.requests.get")
    def test_news_filter_invalid_payload_and_exception(self, mock_get):
        bad_response = Mock()
        bad_response.raise_for_status.return_value = None
        bad_response.json.return_value = {"bad": "shape"}
        mock_get.return_value = bad_response

        nf = NewsFilter(api_key="abc", hard_block_without_api=True, endpoint="https://example.com/news")
        events = nf.fetch_news_events()
        self.assertEqual(events[0]["event"], "INVALID_NEWS_PAYLOAD")

        mock_get.side_effect = RuntimeError("network down")
        events = nf.fetch_news_events()
        self.assertEqual(events[0]["event"], "NEWS_FETCH_FAILED")

    def test_symbol_currency_mapping_and_upcoming_news_paths(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        nf = NewsFilter(api_key="abc", hard_block_without_api=True, endpoint="https://example.com/news")
        with patch.object(
            nf,
            "fetch_news_events",
            return_value=[
                {"currency": "USD", "impact": "HIGH", "time": "00:01", "event": "STR_TIME"},
                {"currency": "USD", "impact": "HIGH", "time": now.time(), "event": "TIME_OBJ"},
                {"currency": "USD", "impact": "HIGH", "time": now, "event": "DT_OBJ"},
                {"currency": "USD", "impact": "HIGH", "time": object(), "event": "FALLBACK"},
                {"currency": "USD", "impact": "LOW", "time": "10:00", "event": "LOW_IMPACT"},
                {"currency": "EUR", "impact": "HIGH", "time": "bad", "event": "BAD_TIME"},
            ],
        ):
            events = nf.get_upcoming_news("XAUUSD", minutes_ahead=1440)
        self.assertTrue(any(e["impact"] == "HIGH" for e in events))
        self.assertEqual(NewsFilter._symbol_currencies("XAUUSD"), ["USD"])
        self.assertEqual(NewsFilter._symbol_currencies("BTC"), ["BTC"])

    def test_should_block_trade_false(self):
        nf = NewsFilter(api_key="abc", hard_block_without_api=True, endpoint="https://example.com/news")
        with patch.object(nf, "get_upcoming_news", return_value=[]):
            self.assertFalse(nf.should_block_trade("EURUSD"))


class TestCircuitBreakers(unittest.TestCase):
    def setUp(self):
        self.cb = CircuitBreakers({"risk": {"max_consecutive_losses": 5}})

    def test_spread_and_volatility_checks(self):
        ok, _ = self.cb.check_trade({"symbol": "EURUSD", "spread_pips": 0.8, "avg_spread_pips": 0.8, "atr_pips": 10, "avg_atr": 10})
        self.assertTrue(ok)

        ok, reason = self.cb.check_trade({"symbol": "EURUSD", "spread_pips": 5.0, "avg_spread_pips": 1.0, "atr_pips": 10, "avg_atr": 10})
        self.assertFalse(ok)
        self.assertIn("Spread", reason)

    def test_symbol_circuit_active_and_expired(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.cb.symbol_circuits["EURUSD"] = now + timedelta(minutes=5)
        ok, reason = self.cb.check_trade({"symbol": "EURUSD", "spread_pips": 0.8, "avg_spread_pips": 0.8, "atr_pips": 10, "avg_atr": 10})
        self.assertFalse(ok)
        self.assertIn("Symbol circuit active", reason)

        self.cb.symbol_circuits["EURUSD"] = now - timedelta(seconds=1)
        ok, _ = self.cb.check_trade({"symbol": "EURUSD", "spread_pips": 0.8, "avg_spread_pips": 0.8, "atr_pips": 10, "avg_atr": 10})
        self.assertTrue(ok)

    def test_global_circuit_expired_during_check(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.cb.global_circuit = True
        self.cb.global_expiry = now - timedelta(seconds=1)
        ok, _ = self.cb.check_trade({"symbol": "EURUSD", "spread_pips": 0.8, "avg_spread_pips": 0.8, "atr_pips": 10, "avg_atr": 10})
        self.assertTrue(ok)
        self.assertFalse(self.cb.global_circuit)

    def test_volatility_spike_triggers_symbol_breaker(self):
        ok, reason = self.cb.check_trade({"symbol": "EURUSD", "spread_pips": 0.8, "avg_spread_pips": 0.8, "atr_pips": 30, "avg_atr": 10})
        self.assertFalse(ok)
        self.assertEqual(reason, "Volatility spike")

    def test_global_circuit_from_loss_streak(self):
        for _ in range(5):
            self.cb.record_trade_outcome("LOSS", symbol="EURUSD")
        self.assertTrue(self.cb.global_circuit)

    def test_loss_streak_reset_on_win_and_low_win_rate_trigger(self):
        self.cb.record_trade_outcome("LOSS", symbol="EURUSD")
        self.cb.record_trade_outcome("WIN", symbol="EURUSD")
        self.assertEqual(self.cb.loss_streak, 0)

        for outcome in ["LOSS", "LOSS", "LOSS", "LOSS", "LOSS", "LOSS", "LOSS", "LOSS", "WIN", "WIN"]:
            self.cb.record_trade_outcome(outcome, symbol="EURUSD")
        self.assertTrue(self.cb.global_circuit)

    def test_reset_circuits_after_expiry(self):
        self.cb.trigger_global_circuit("test")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.cb.global_expiry = now - timedelta(seconds=1)
        self.cb.symbol_circuits["EURUSD"] = now - timedelta(seconds=1)
        self.cb.reset_circuits()
        self.assertFalse(self.cb.global_circuit)
        self.assertNotIn("EURUSD", self.cb.symbol_circuits)


class TestRiskManager(unittest.TestCase):
    def setUp(self):
        self.config = {
            "risk": {
                "base_risk_percent": 1.0,
                "max_concurrent_positions": 3,
                "max_correlation": 0.7,
                "live_deployment_target": 20,
                "live_deployment_multiplier": 0.1,
            },
            "deployment": {"min_live_lots": 0.01},
            "news": {
                "api_key": "key",
                "endpoint": "https://example.com/news",
                "hard_block_without_api": True,
                "buffer_minutes": 15,
            },
            "news_api_key": "key",
        }
        self.rm = RiskManager(self.config)
        self.rm.governor.update_account(10_000, 10_000, [])

    def _make_setup(self):
        return SimpleNamespace(
            symbol="EURUSD",
            direction="LONG",
            entry_price=1.1000,
            stop_loss=1.0980,
            take_profit_1=1.1030,
            take_profit_2=1.1050,
            confidence=0.9,
            regime_multiplier=1.0,
            poi_type="OB",
            poi_zone=[1.0990, 1.1010],
            bias="BULLISH",
            rr_ratio=1.5,
        )

    @dataclass
    class _DataclassSetup:
        symbol: str
        entry_price: float
        stop_loss: float
        confidence: float

    class _NoDictSetup:
        __slots__ = ()

    def test_approve_trade_success_path(self):
        setup = self._make_setup()
        with patch.object(self.rm.news_filter, "should_block_trade", return_value=False):
            approved = self.rm.approve_trade(setup, current_positions=[])
        self.assertIsNotNone(approved)
        self.assertIn("lots", approved)
        self.assertGreaterEqual(approved["lots"], 0.01)

    def test_approve_trade_rejected_by_news(self):
        setup = self._make_setup()
        with patch.object(self.rm.news_filter, "should_block_trade", return_value=True):
            approved = self.rm.approve_trade(setup, current_positions=[])
        self.assertIsNone(approved)

    def test_approve_trade_rejected_by_halt(self):
        self.rm.governor.current_state = RiskState.HALT
        setup = self._make_setup()
        with patch.object(self.rm.news_filter, "should_block_trade", return_value=False):
            approved = self.rm.approve_trade(setup, current_positions=[])
        self.assertIsNone(approved)

    def test_record_trade_outcome_updates_components(self):
        self.rm.record_trade_outcome("LOSS", symbol="EURUSD")
        self.assertEqual(self.rm.governor.consecutive_losses, 1)
        self.assertEqual(self.rm.circuits.loss_streak, 1)

    def test_internal_extractors_and_defaults(self):
        self.assertEqual(self.rm._point_size_for_symbol("USDJPY"), 0.001)
        self.assertEqual(self.rm._point_size_for_symbol("XAUUSD"), 0.01)
        self.assertEqual(self.rm._point_size_for_symbol("EURUSD"), 0.00001)

        self.assertEqual(self.rm._extract_positions(None), [])
        self.assertEqual(self.rm._extract_positions({"positions": []}), [])
        self.assertEqual(self.rm._extract_positions({"positions": "bad"}), [])
        self.assertEqual(self.rm._extract_positions("bad"), [])

        from_dataclass = self.rm._setup_to_dict(self._DataclassSetup("EURUSD", 1.1, 1.09, 0.8))
        self.assertIn("stop_loss_pips", from_dataclass)
        self.assertEqual(from_dataclass["symbol"], "EURUSD")

        fallback = self.rm._setup_to_dict(self._NoDictSetup())
        self.assertEqual(fallback["symbol"], "EURUSD")

    def test_governor_rejection_branch(self):
        setup = self._make_setup()
        with patch.object(self.rm.news_filter, "should_block_trade", return_value=False), patch.object(
            self.rm.governor, "approve_trade", return_value=False
        ):
            approved = self.rm.approve_trade(setup, current_positions=[{"symbol": "EURUSD"}])
        self.assertIsNone(approved)

    def test_risk_report_schema(self):
        report = self.rm.get_risk_report()
        self.assertIn("governor", report)
        self.assertIn("circuits", report)
        self.assertIn("current_session", report)


if __name__ == "__main__":
    unittest.main()
