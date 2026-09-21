"""Alert system for Telegram and email notifications."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
from typing import Any, Dict

from loguru import logger
import requests


class AlertSystem:
    """Send alerts via Telegram and optional email."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}

        alerts_cfg = self.config.get("alerts", {}) if isinstance(self.config, dict) else {}
        self.telegram_enabled = bool(alerts_cfg.get("telegram_enabled", True))
        self.email_enabled = bool(alerts_cfg.get("email_enabled", False))

        self.telegram_token = self.config.get("telegram_bot_token") or self.config.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = self.config.get("telegram_chat_id") or self.config.get("TELEGRAM_CHAT_ID")

        self.email_config = self.config.get("email", {}) if isinstance(self.config, dict) else {}
        self.last_alert_time: Dict[str, float] = {}
        self.alert_cooldown = 300

    def send_telegram(self, message: str) -> bool:
        if not self.telegram_enabled:
            return False
        if not self.telegram_token or not self.telegram_chat_id:
            return False

        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML",
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.debug("Telegram alert sent")
                return True
            logger.error(f"Telegram send failed: {response.text}")
            return False
        except Exception as exc:
            logger.error(f"Telegram error: {exc}")
            return False

    def send_email(self, subject: str, body: str) -> bool:
        if not self.email_enabled:
            return False

        cfg = self.email_config
        required = ["from", "to", "smtp_server", "smtp_port", "username", "password"]
        if not all(k in cfg and cfg.get(k) for k in required):
            return False

        try:
            message = MIMEMultipart()
            message["From"] = cfg["from"]
            message["To"] = cfg["to"]
            message["Subject"] = f"[SMC Bot] {subject}"
            message.attach(MIMEText(body, "html"))

            server = smtplib.SMTP(cfg["smtp_server"], int(cfg["smtp_port"]))
            server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.send_message(message)
            server.quit()
            logger.debug(f"Email alert sent: {subject}")
            return True
        except Exception as exc:
            logger.error(f"Email error: {exc}")
            return False

    def alert(self, level: str, title: str, message: str, force: bool = False) -> bool:
        alert_key = f"{level}_{title}"
        now_ts = datetime.now(timezone.utc).timestamp()

        if not force and alert_key in self.last_alert_time:
            if now_ts - self.last_alert_time[alert_key] < self.alert_cooldown:
                return False

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        formatted = f"<b>[{level}] {title}</b>\n{message}\n\n<pre>{timestamp}</pre>"

        telegram_sent = self.send_telegram(formatted)
        email_sent = self.send_email(title, formatted) if level in {"CRITICAL", "ERROR"} else False

        self.last_alert_time[alert_key] = now_ts
        return telegram_sent or email_sent

    def alert_trade(self, trade_data: Dict[str, Any]) -> None:
        pnl = float(trade_data.get("pnl", 0.0) or 0.0)
        if abs(pnl) <= 100:
            return

        outcome = "WIN" if pnl > 0 else "LOSS"
        self.alert(
            "INFO" if pnl > 0 else "WARNING",
            f"Trade {outcome}: {trade_data.get('symbol', 'UNKNOWN')}",
            (
                f"Direction: {trade_data.get('direction', 'UNKNOWN')}\n"
                f"P&L: ${pnl:.2f}\n"
                f"R: {float(trade_data.get('r_multiple', 0.0) or 0.0):.2f}"
            ),
        )

    def alert_risk_state(self, old_state: str, new_state: str, reasons: list[str]) -> None:
        level = "WARNING" if new_state in {"CAUTION", "DEFENSIVE"} else "CRITICAL"
        self.alert(level, f"Risk State Changed: {old_state} -> {new_state}", "Reasons:\n" + "\n".join(reasons))

    def alert_system_error(self, error: str) -> None:
        self.alert("CRITICAL", "System Error", f"Error: {error}", force=True)

    def send_daily_report(self, stats: Dict[str, Any]) -> None:
        message = (
            "<b>Daily Performance Report</b>\n\n"
            f"Trades: {int(stats.get('trades', 0) or 0)}\n"
            f"Wins: {int(stats.get('wins', 0) or 0)}\n"
            f"Losses: {int(stats.get('losses', 0) or 0)}\n"
            f"Win Rate: {float(stats.get('win_rate', 0.0) or 0.0):.1f}%\n"
            f"P&L: ${float(stats.get('pnl', 0.0) or 0.0):.2f}\n"
            f"Expectancy: {float(stats.get('expectancy', 0.0) or 0.0):.2f}R\n"
            f"Drawdown: {float(stats.get('drawdown', 0.0) or 0.0):.1f}%\n"
            f"Risk State: {stats.get('risk_state', 'NORMAL')}"
        )
        self.send_telegram(message)

    def alert_signal(self, signal_data: Dict[str, Any], mode: str = "DRY-RUN") -> bool:
        """Send a trading signal notification to Telegram."""
        symbol = str(signal_data.get("symbol", "UNKNOWN"))
        direction = str(signal_data.get("direction", "UNKNOWN"))
        entry = signal_data.get("entry_price")
        stop = signal_data.get("stop_loss")
        tp1 = signal_data.get("take_profit_1")
        tp2 = signal_data.get("take_profit_2")
        lots = signal_data.get("lots")
        confidence = signal_data.get("confidence", signal_data.get("confidence_score", 0.0))
        rr_ratio = signal_data.get("rr_ratio", 0.0)
        version_id = signal_data.get("version_id")

        title = "<b>Trading Signal</b>"
        lines = [
            title,
            f"Mode: {mode}",
            f"Symbol: {symbol}",
            f"Direction: {direction}",
        ]
        if entry is not None:
            lines.append(f"Entry: {float(entry):.5f}")
        if stop is not None:
            lines.append(f"Stop: {float(stop):.5f}")
        if tp1 is not None:
            lines.append(f"TP1: {float(tp1):.5f}")
        if tp2 is not None:
            lines.append(f"TP2: {float(tp2):.5f}")
        if lots is not None:
            lines.append(f"Lots: {float(lots):.2f}")
        lines.append(f"Confidence: {float(confidence):.2f}")
        lines.append(f"R/R: {float(rr_ratio):.2f}")
        if version_id:
            lines.append(f"Strategy Version: {version_id}")

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines.append(f"<pre>{timestamp}</pre>")
        return self.send_telegram("\n".join(lines))
