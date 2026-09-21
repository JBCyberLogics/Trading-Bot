"""Logging configuration using Loguru."""
import sys
from pathlib import Path
from loguru import logger


def setup_logging(log_dir="logs"):
    log_dir = Path(log_dir)
    log_dir.mkdir(exist_ok=True)
    logger.remove()
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        level="INFO",
        colorize=True,
    )
    logger.add(
        log_dir / "bot_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        retention="10 days",
        level="DEBUG",
        compression="gz",
    )
    logger.add(
        log_dir / "errors_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        retention="30 days",
        level="ERROR",
        compression="gz",
    )
    trade_logger = logger.bind(name="trade")
    trade_logger.add(
        log_dir / "trades_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        retention="90 days",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {extra[name]} | {message}",
        filter=lambda record: record["extra"].get("name") == "trade",
        level="INFO",
    )
    return logger


def log_trade(signal, result):
    logger.bind(name="trade").info(f"TRADE|{signal}|{result}")
