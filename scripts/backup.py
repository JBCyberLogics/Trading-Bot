#!/usr/bin/env python3
"""Automated backup system for production."""

from __future__ import annotations

import argparse
from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_config


class BackupSystem:
    """Create and restore backups of db/config/log subsets."""

    def __init__(self):
        self.config = load_config(PROJECT_ROOT / "config")

        backup_cfg = self.config.get("backup", {}) if isinstance(self.config, dict) else {}
        db_cfg = self.config.get("database", {}) if isinstance(self.config, dict) else {}

        self.backup_dir = Path(backup_cfg.get("local_path") or (PROJECT_ROOT / "backups"))
        self.db_path = Path(db_cfg.get("path") or self.config.get("db_path") or (PROJECT_ROOT / "database" / "live_trades.db"))
        if not self.db_path.is_absolute():
            self.db_path = PROJECT_ROOT / self.db_path

        self.retention_days = int(backup_cfg.get("retention_days", 30))
        self.s3_bucket = str(backup_cfg.get("s3_bucket", "") or "")

        self.config_dir = PROJECT_ROOT / "config"
        self.logs_dir = PROJECT_ROOT / "logs"

        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self) -> Path:
        now = _utcnow_naive()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"backup_{timestamp}.tar.gz"
        temp_dir = self.backup_dir / f"temp_{timestamp}"
        temp_dir.mkdir(exist_ok=True)

        print(f"Creating backup: {backup_file}")
        try:
            if self.db_path.exists():
                db_backup = temp_dir / "live_trades.db"
                self._backup_sqlite(self.db_path, db_backup)
                print(f"  Database backed up: {db_backup.name}")

            if self.config_dir.exists():
                config_backup = temp_dir / "config"
                config_backup.mkdir(exist_ok=True)
                for cfg in self.config_dir.glob("*.yaml"):
                    shutil.copy2(cfg, config_backup / cfg.name)
                env_example = self.config_dir / ".env.example"
                if env_example.exists():
                    shutil.copy2(env_example, config_backup / env_example.name)
                print("  Config backup complete")

            if self.logs_dir.exists():
                logs_backup = temp_dir / "logs"
                logs_backup.mkdir(exist_ok=True)
                for log_file in self.logs_dir.glob("*.log*"):
                    age_days = (now.timestamp() - log_file.stat().st_mtime) / 86400.0
                    if age_days <= 7:
                        shutil.copy2(log_file, logs_backup / log_file.name)
                print("  Recent logs backup complete")

            metadata: Dict[str, Any] = {
                "timestamp": timestamp,
                "database": str(self.db_path),
                "files": [str(p.relative_to(temp_dir)) for p in temp_dir.glob("**/*") if p.is_file()],
                "version": "1.0.0",
            }
            (temp_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

            subprocess.run(["tar", "-czf", str(backup_file), "-C", str(temp_dir), "."], check=True)
            print(f"  Backup archive created: {backup_file.name}")

            if self.s3_bucket:
                self._upload_to_s3(backup_file)

            self._cleanup_old_backups()
            return backup_file
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _backup_sqlite(source: Path, dest: Path) -> None:
        conn = sqlite3.connect(str(source))
        backup_conn = sqlite3.connect(str(dest))
        try:
            conn.backup(backup_conn)
        finally:
            backup_conn.close()
            conn.close()

    def _upload_to_s3(self, backup_file: Path) -> None:
        try:
            import boto3

            s3 = boto3.client("s3")
            s3.upload_file(str(backup_file), self.s3_bucket, f"backups/{backup_file.name}")
            print(f"  Uploaded to s3://{self.s3_bucket}/backups/{backup_file.name}")
        except ImportError:
            print("  boto3 not installed; skipping S3 upload")
        except Exception as exc:
            print(f"  S3 upload failed: {exc}")

    def _cleanup_old_backups(self) -> None:
        cutoff = _utcnow_naive().timestamp() - (self.retention_days * 86400)
        for archive in self.backup_dir.glob("backup_*.tar.gz"):
            if archive.stat().st_mtime < cutoff:
                archive.unlink(missing_ok=True)
                print(f"  Removed old backup: {archive.name}")

    def restore_backup(self, backup_file: Path) -> None:
        if not backup_file.exists():
            raise FileNotFoundError(f"Backup not found: {backup_file}")

        print(f"Restoring backup: {backup_file}")
        temp_dir = self.backup_dir / "restore_temp"
        temp_dir.mkdir(exist_ok=True)

        try:
            subprocess.run(["tar", "-xzf", str(backup_file), "-C", str(temp_dir)], check=True)

            db_backup = temp_dir / "live_trades.db"
            if db_backup.exists():
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(db_backup, self.db_path)
                print("  Database restored")

            config_backup = temp_dir / "config"
            if config_backup.exists():
                for cfg in config_backup.glob("*.yaml"):
                    shutil.copy2(cfg, self.config_dir / cfg.name)
                print("  Config restored")

            print("Restore complete")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or restore bot backups")
    parser.add_argument("--restore", type=str, default=None, help="Restore from backup tarball")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    backup = BackupSystem()

    if args.restore:
        backup.restore_backup(Path(args.restore))
        return 0

    backup.create_backup()
    return 0


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


if __name__ == "__main__":
    raise SystemExit(main())
