"""Champion-challenger A/B testing system for self-learning."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from loguru import logger
import numpy as np
import yaml

from .models import ParameterVersion


def _utcnow_naive() -> datetime:
    """Return a UTC timestamp without tzinfo for SQLite compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ChampionChallenger:
    """Manages champion and challenger parameter versions with allocation logic."""

    def __init__(
        self,
        db,
        bootstrap_validator,
        config_path: str | None = None,
        champion_allocation: int = 80,
        max_challengers: int = 3,
    ) -> None:
        self.db = db
        self.validator = bootstrap_validator
        self.config_path = config_path

        self.champion_allocation = int(champion_allocation)
        self.challenger_allocation = max(0, 100 - self.champion_allocation)
        self.max_challengers = int(max_challengers)

        self.champion: Optional[ParameterVersion] = None
        self.load_champion()
        logger.info("ChampionChallenger initialized")

    def load_champion(self) -> None:
        """Load current champion from database or bootstrap one from config."""
        self.champion = self.db.get_champion_version()
        if self.champion is None:
            logger.warning("No champion found, creating initial champion")
            self.create_initial_champion()
            self.champion = self.db.get_champion_version()

    def _load_config_params(self) -> Dict[str, Any]:
        if not self.config_path:
            return {}
        config_file = Path(self.config_path)
        if not config_file.exists():
            return {}
        loaded = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    def create_initial_champion(self) -> Optional[ParameterVersion]:
        """Create an initial champion row using config parameters if available."""
        params = self._load_config_params()
        version_id = "CHAMPION_V1"

        session = self.db.Session()
        try:
            existing = session.query(ParameterVersion).filter_by(version_id=version_id).first()
            if existing:
                existing.is_champion = True
                existing.is_challenger = False
                existing.promotion_date = _utcnow_naive()
                existing.parameters_json = json.dumps(params, sort_keys=True)
                session.commit()
                return existing
        finally:
            session.close()

        created = self.db.add_challenger(
            {
                "version_id": version_id,
                "parameters_json": json.dumps(params, sort_keys=True),
                "is_champion": True,
                "is_challenger": False,
                "allocation_percent": 100,
                "promotion_date": _utcnow_naive(),
                "created_at": _utcnow_naive(),
            }
        )
        logger.info(f"Initial champion created: {version_id}")
        return created

    @staticmethod
    def generate_version_id(base: str = "CHALLENGER") -> str:
        """Generate unique version ID."""
        return f"{base}_{_utcnow_naive().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"

    def create_challenger_from_sensitivity(
        self,
        parameter_name: str,
        new_value: Any,
        reason: str,
    ) -> Optional[ParameterVersion]:
        """Create challenger by tweaking a single parameter from champion."""
        if self.champion is None:
            logger.error("No champion to base challenger on")
            return None

        champion_params = self.champion.get_parameters()
        new_params = dict(champion_params)
        new_params[parameter_name] = new_value

        return self.db.add_challenger(
            {
                "version_id": self.generate_version_id("SENS"),
                "parent_version": self.champion.version_id,
                "parameters_json": json.dumps(new_params, sort_keys=True),
                "is_champion": False,
                "is_challenger": True,
                "challenger_type": "SENSITIVITY",
                "hypothesis": reason,
                "allocation_percent": 0,
                "test_start_date": _utcnow_naive(),
                "test_trades_required": 100,
                "test_trades_completed": 0,
                "created_at": _utcnow_naive(),
            }
        )

    def create_challenger_from_evolution(
        self,
        params_dict: Dict[str, Any],
        parent_ids: List[str],
        hypothesis: str,
    ) -> Optional[ParameterVersion]:
        """Create challenger from genetic/evolutionary discovery."""
        return self.db.add_challenger(
            {
                "version_id": self.generate_version_id("EVO"),
                "parent_version": ",".join(parent_ids),
                "parameters_json": json.dumps(params_dict, sort_keys=True),
                "is_champion": False,
                "is_challenger": True,
                "challenger_type": "EVOLUTIONARY",
                "hypothesis": hypothesis,
                "allocation_percent": 0,
                "test_start_date": _utcnow_naive(),
                "test_trades_required": 100,
                "test_trades_completed": 0,
                "created_at": _utcnow_naive(),
            }
        )

    def select_parameter_version(self) -> Optional[ParameterVersion]:
        """Select parameter version for next trade based on allocation policy."""
        active_challengers = self.db.get_active_challengers()
        if not active_challengers:
            return self.champion

        selected_challengers = active_challengers[: self.max_challengers]
        if self.champion is None:
            return selected_challengers[0]

        roll = float(np.random.random() * 100.0)
        if roll < self.champion_allocation:
            return self.champion

        idx = int(np.random.randint(0, len(selected_challengers)))
        return selected_challengers[idx]

    def record_trade_with_version(self, trade_id: str, version_id: str) -> bool:
        """Attach parameter version metadata to trade and advance challenger counters."""
        updated = self.db.update_trade_exit(
            trade_id,
            {
                "parameter_version_id": version_id,
                "version_id": version_id,
            },
        )
        if not updated:
            return False

        if self.champion and version_id == self.champion.version_id:
            return True

        session = self.db.Session()
        try:
            challenger = session.query(ParameterVersion).filter_by(version_id=version_id).first()
            if challenger:
                challenger.test_trades_completed = int(challenger.test_trades_completed or 0) + 1
                session.commit()
        finally:
            session.close()
        return True

    def evaluate_challengers(self) -> List[Dict[str, Any]]:
        """Evaluate challengers against champion when minimum sample size is reached."""
        if self.champion is None:
            logger.error("No champion available for challenger evaluation")
            return []

        champion_trades = list(self.db.get_trades_by_regime({}, version_id=self.champion.version_id))
        challengers = self.db.get_active_challengers()
        results: List[Dict[str, Any]] = []

        for challenger in challengers:
            required = int(challenger.test_trades_required or 0)
            completed = int(challenger.test_trades_completed or 0)
            if completed < required:
                continue

            challenger_trades = list(self.db.get_trades_by_regime({}, version_id=challenger.version_id))
            validation = self.validator.validate_challenger(champion_trades, challenger_trades)
            results.append({"challenger": challenger, "validation": validation})

            session = self.db.Session()
            try:
                row = session.query(ParameterVersion).filter_by(version_id=challenger.version_id).first()
                if row is None:
                    continue
                row.test_end_date = _utcnow_naive()
                if validation.get("passes"):
                    logger.info(f"Challenger {challenger.version_id} passed validation")
                else:
                    row.is_challenger = False
                    row.rejection_reason = validation.get("reason", "Failed validation")
                session.commit()
            finally:
                session.close()

        return results

    def promote_challenger(self, challenger_version_id: str) -> bool:
        """Promote a validated challenger to champion and persist parameters to config."""
        session = self.db.Session()
        try:
            challenger = session.query(ParameterVersion).filter_by(version_id=challenger_version_id).first()
            if challenger is None:
                logger.error(f"Challenger {challenger_version_id} not found")
                return False

            session.query(ParameterVersion).filter_by(is_champion=True).update(
                {"is_champion": False, "demotion_date": _utcnow_naive()}
            )
            challenger.is_champion = True
            challenger.is_challenger = False
            challenger.promotion_date = _utcnow_naive()
            challenger.allocation_percent = 100
            session.commit()

            if self.config_path:
                params = challenger.get_parameters()
                Path(self.config_path).write_text(yaml.safe_dump(params, sort_keys=True), encoding="utf-8")

            self.load_champion()
            logger.info(f"Promoted {challenger_version_id} to champion")
            return True
        finally:
            session.close()
