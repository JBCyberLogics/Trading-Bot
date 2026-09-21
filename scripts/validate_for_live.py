#!/usr/bin/env python3
"""Phase 6 validator for direct live deployment readiness."""

from __future__ import annotations

import argparse
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Tuple

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_config
from src.execution.zmq_bridge import ZMQBridge
from src.risk.session_filters import NewsFilter
from tests.backtest.backtest_engine import BacktestEngine
from tests.backtest.backtest_engine import BacktestThresholds
from tests.backtest.walk_forward_validator import WalkForwardValidator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _python_bin() -> str:
    venv_python = PROJECT_ROOT / "venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _run_command(command: List[str]) -> Tuple[bool, str]:
    completed = subprocess.run(command, capture_output=True, text=True)
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode == 0, output


def _validate_news(config: Dict[str, Any]) -> Tuple[bool, str]:
    news_cfg = config.get("news", {}) if isinstance(config, dict) else {}
    api_key = str(news_cfg.get("api_key", "") or "")
    endpoint = str(news_cfg.get("endpoint", "") or "")
    if not api_key:
        return False, "NEWS_API_KEY is missing"
    if not endpoint:
        return False, "NEWS_API_ENDPOINT is missing"

    news = NewsFilter(api_key=api_key, hard_block_without_api=True, endpoint=endpoint)
    events = news.fetch_news_events()
    if news.last_error:
        return False, news.last_error
    if not isinstance(events, list):
        return False, "News endpoint response is not a list"
    return True, f"News feed healthy ({len(events)} events)"


def _validate_mt5(config: Dict[str, Any], symbol: str, timeframe: str) -> Tuple[bool, str]:
    bridge = ZMQBridge(
        host=config.get("mt5", {}).get("zmq_host"),
        port=config.get("mt5", {}).get("zmq_python_port"),
        timeout_ms=5000,
    )
    try:
        ping = bridge.ping()
        if ping.get("error"):
            return False, f"PING failed: {ping}"

        account = bridge.get_account_info()
        if account.get("error"):
            return False, f"ACCOUNT failed: {account}"

        rates = bridge.get_rates(symbol=symbol, timeframe=timeframe, count=300)
        if rates.get("error"):
            return False, f"RATES failed: {rates}"
        if len(rates.get("bars", [])) < 100:
            return False, "Insufficient MT5 bars returned for validation"

        return True, "MT5 bridge and RATES pull healthy"
    finally:
        bridge.stop()


def _load_champion_params(parameters_path: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    params = {}
    if parameters_path.exists() and parameters_path.stat().st_size > 0:
        loaded = yaml.safe_load(parameters_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            params = loaded

    return {
        "indicators": {
            "rsi_period": int(params.get("rsi_period", 14)),
            "atr_period": int(config.get("atr_period", 14)),
        },
        "strategy": {
            "rsi_oversold": float(params.get("rsi_oversold", 30)),
            "rsi_overbought": float(params.get("rsi_overbought", 70)),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate readiness for direct live deployment")
    parser.add_argument("--symbol", default=None, help="Validation symbol override")
    parser.add_argument("--timeframe", default=None, help="Validation timeframe override")
    parser.add_argument("--reports-dir", default="reports", help="Directory for validation outputs")
    parser.add_argument("--quick", action="store_true", help="Run prerequisite-only validation for service pre-start")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config(PROJECT_ROOT / "config")

    deployment_cfg = cfg.get("deployment", {})
    symbol = (args.symbol or deployment_cfg.get("default_symbol") or "EURUSD").upper()
    timeframe = (args.timeframe or deployment_cfg.get("default_timeframe") or "H1").upper()

    reports_dir = PROJECT_ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "timestamp": _utc_now().isoformat(),
        "symbol": symbol,
        "timeframe": timeframe,
        "gates": {},
        "ready": False,
    }

    # Gate 1: News feed config + health
    ok, detail = _validate_news(cfg)
    summary["gates"]["news"] = {"passed": ok, "detail": detail}
    if not ok:
        _write_json(reports_dir / "live_validation_summary.json", summary)
        print("NOT READY: news prerequisite failed")
        print(detail)
        return 1

    # Gate 2: MT5 bridge + data pull
    ok, detail = _validate_mt5(cfg, symbol, timeframe)
    summary["gates"]["mt5"] = {"passed": ok, "detail": detail}
    if not ok:
        _write_json(reports_dir / "live_validation_summary.json", summary)
        print("NOT READY: MT5 prerequisite failed")
        print(detail)
        return 1

    if args.quick:
        summary["ready"] = True
        summary["mode"] = "quick"
        _write_json(reports_dir / "live_validation_summary.json", summary)
        print("READY: quick validation passed")
        print(f"Summary: {reports_dir / 'live_validation_summary.json'}")
        return 0

    # Gate 3: Existing Phase 1-4 + Phase 5 tests
    phase_suite = [
        _python_bin(),
        "-m",
        "pytest",
        "-v",
        "tests/unit/test_bias_detector.py",
        "tests/unit/test_regime_detector.py",
        "tests/unit/test_state_machine.py",
        "tests/unit/test_smc_patterns.py",
        "tests/unit/test_learning_models.py",
        "tests/unit/test_trade_recorder.py",
        "tests/unit/test_performance_tracker.py",
        "tests/unit/test_phase4_learning_engine.py",
        "tests/unit/test_risk_governor.py",
        "tests/unit/test_risk_components.py",
        "tests/integration/test_risk_flow.py",
        "tests/integration/test_zmq_bridge.py",
    ]
    ok, output = _run_command(phase_suite)
    summary["gates"]["phase_tests"] = {
        "passed": ok,
        "command": " ".join(phase_suite),
        "detail": output[-4000:],
    }
    if not ok:
        _write_json(reports_dir / "live_validation_summary.json", summary)
        print("NOT READY: phase test suite failed")
        return 1

    # Gate 4: Risk coverage >= 90%
    cov_threshold = int(cfg.get("validation", {}).get("risk_coverage_min", 90))
    risk_cov_cmd = [
        _python_bin(),
        "-m",
        "pytest",
        "--cov=src/risk",
        f"--cov-fail-under={cov_threshold}",
        "tests/unit/test_risk_governor.py",
        "tests/unit/test_risk_components.py",
        "tests/integration/test_risk_flow.py",
    ]
    ok, output = _run_command(risk_cov_cmd)
    summary["gates"]["risk_coverage"] = {
        "passed": ok,
        "command": " ".join(risk_cov_cmd),
        "detail": output[-4000:],
    }
    if not ok:
        _write_json(reports_dir / "live_validation_summary.json", summary)
        print("NOT READY: risk coverage gate failed")
        return 1

    # Gate 5: MT5-required walk-forward validation (6 months)
    wf_cfg = cfg.get("validation", {}).get("walk_forward", {})
    lookback_days = int(wf_cfg.get("lookback_days", 180))
    thresholds = BacktestThresholds(
        min_sharpe=float(wf_cfg.get("min_sharpe", 1.0)),
        min_win_rate=float(wf_cfg.get("min_win_rate", 50.0)),
        min_consistency=float(wf_cfg.get("min_consistency", 0.70)),
    )

    bridge = ZMQBridge(
        host=cfg.get("mt5", {}).get("zmq_host"),
        port=cfg.get("mt5", {}).get("zmq_python_port"),
        timeout_ms=5000,
    )
    try:
        engine = BacktestEngine()
        validator = WalkForwardValidator(bridge, engine, thresholds)

        end_dt = _utc_now()
        start_dt = end_dt - timedelta(days=lookback_days)

        data = validator.load_historical_data(symbol, start_dt, end_dt, timeframe=timeframe)
        champion_params = _load_champion_params(PROJECT_ROOT / "config" / "parameters.yaml", cfg)
        wf_result = validator.validate_parameter_set(champion_params, data)

        report_path = reports_dir / "walk_forward_validation.html"
        ready = validator.generate_validation_report(wf_result, report_path)

        summary["gates"]["walk_forward"] = {
            "passed": bool(ready),
            "metrics": wf_result.get("metrics", {}),
            "report": str(report_path),
        }
    except Exception as exc:
        summary["gates"]["walk_forward"] = {"passed": False, "detail": str(exc)}
        _write_json(reports_dir / "live_validation_summary.json", summary)
        print("NOT READY: walk-forward gate failed")
        print(str(exc))
        return 1
    finally:
        bridge.stop()

    if not summary["gates"]["walk_forward"].get("passed"):
        _write_json(reports_dir / "live_validation_summary.json", summary)
        print("NOT READY: walk-forward thresholds not met")
        return 1

    summary["ready"] = True
    _write_json(reports_dir / "live_validation_summary.json", summary)

    print("READY: all validation gates passed")
    print(f"Summary: {reports_dir / 'live_validation_summary.json'}")
    print(f"Walk-forward report: {reports_dir / 'walk_forward_validation.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
