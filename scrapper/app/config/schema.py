from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictBaseModel(BaseModel):
    # Forward-compatible parsing: keep required typed fields,
    # and preserve richer payloads from later phases.
    model_config = ConfigDict(extra="allow")


class FlexibleBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ConnectionMode(str, Enum):
    ZMQ_EA_BRIDGE = "zmq_ea_bridge"
    MT5_REMOTE_SERVER = "mt5_remote_server"


class MT5RemoteServerArgs(StrictBaseModel):
    host: str = "localhost"
    port: int = 18812
    wine_flag: bool = True


class WineConfig(StrictBaseModel):
    prefix: str
    wine_command: str = "wine"
    wineserver_command: str = "wineserver"
    mt5_install_path: str
    mt5_executable: str = "terminal64.exe"
    windows_python_path: str
    mt5_remote_server_args: MT5RemoteServerArgs


class PathsConfig(StrictBaseModel):
    log_directory: str
    data_directory: str
    config_directory: str
    database_path: str
    credentials_store: str


class AccountsSystemConfig(StrictBaseModel):
    storage_method: str = "encrypted_json"
    encryption_key_env: str = "BOT_ENCRYPTION_KEY"
    auto_load_last_account: bool = True
    max_saved_accounts: int = 5


class SystemConfig(StrictBaseModel):
    environment: str = "linux-wine"
    wine: WineConfig
    paths: PathsConfig
    accounts: AccountsSystemConfig = Field(default_factory=AccountsSystemConfig)


class ServerConfig(StrictBaseModel):
    auto_start_server: bool = True
    server_startup_delay_seconds: int = 10
    max_reconnect_attempts: int = 5
    reconnect_delay_seconds: int = 30
    connection_timeout_ms: int = 60000
    heartbeat_interval_seconds: int = 5


class TerminalConfig(StrictBaseModel):
    portable_mode: bool = True
    enable_algo_trading: bool = True
    auto_login: bool = False
    broker_discovery_required: bool = True
    login_retry_attempts: int = 3
    login_retry_delay_seconds: int = 5
    unsupported_login_action: Literal["skip", "fail"] = "skip"


class ConnectionHistoryItem(FlexibleBaseModel):
    timestamp: datetime
    event: str
    details: Dict[str, Any] = Field(default_factory=dict)


class ConnectionState(StrictBaseModel):
    status: str = "disconnected"
    current_account: Optional[str] = None
    last_connected: Optional[datetime] = None
    connection_history: List[ConnectionHistoryItem] = Field(default_factory=list)


class MT5ConnectionConfig(StrictBaseModel):
    server_config: ServerConfig = Field(default_factory=ServerConfig)
    terminal_config: TerminalConfig = Field(default_factory=TerminalConfig)
    connection_state: ConnectionState = Field(default_factory=ConnectionState)

    @model_validator(mode="before")
    @classmethod
    def normalize_primary_broker(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        if "server_config" in raw and "terminal_config" in raw:
            return raw

        primary = raw.get("primary_broker")
        if not isinstance(primary, dict):
            return raw

        normalized = dict(raw)
        server_cfg = primary.get("server_config")
        terminal_cfg = primary.get("terminal_config")
        if "server_config" not in normalized and isinstance(server_cfg, dict):
            normalized["server_config"] = server_cfg
        if "terminal_config" not in normalized and isinstance(terminal_cfg, dict):
            normalized["terminal_config"] = terminal_cfg
        return normalized


class PairManagementConfig(StrictBaseModel):
    dynamic_activation: bool = True
    save_selection_on_exit: bool = True
    auto_restore_on_start: bool = True
    fetch_from_mt5_on_connect: bool = True


class PairsConfig(StrictBaseModel):
    active: List[str] = Field(default_factory=list)
    available: List[str] = Field(default_factory=list)
    pair_config: Dict[str, Any] = Field(default_factory=dict)
    pair_management: PairManagementConfig


class TradingSessionConfig(StrictBaseModel):
    start_hour: int
    end_hour: int
    timezone: str = "UTC"


class TradingHoursConfig(StrictBaseModel):
    enabled: bool = True
    sessions: List[str] = Field(default_factory=lambda: ["london", "newyork"])
    london: TradingSessionConfig = Field(
        default_factory=lambda: TradingSessionConfig(start_hour=8, end_hour=17, timezone="UTC")
    )
    newyork: TradingSessionConfig = Field(
        default_factory=lambda: TradingSessionConfig(start_hour=13, end_hour=22, timezone="UTC")
    )
    asian: TradingSessionConfig = Field(
        default_factory=lambda: TradingSessionConfig(start_hour=0, end_hour=9, timezone="UTC")
    )
    overlap_only: bool = False
    weekdays_only: bool = True


class SpreadRequirementConfig(StrictBaseModel):
    max_pips: float = 2.0
    check_on_every_tick: bool = True
    reject_if_increasing: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_phase_payload(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        normalized = dict(raw)
        if "max_pips" not in normalized:
            base = normalized.get("base_max_pips")
            if isinstance(base, (int, float)):
                normalized["max_pips"] = float(base)
        return normalized


class OrderBookImbalanceConfig(StrictBaseModel):
    enabled: bool = True
    min_imbalance_long: float = 0.7
    min_imbalance_short: float = -0.7
    levels_to_scan: int = 10
    require_consecutive_readings: int = 3
    consecutive_interval_ms: int = 100
    weighting_enabled: bool = True
    decay_factor: float = 0.8

    @model_validator(mode="before")
    @classmethod
    def normalize_phase_payload(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        normalized = dict(raw)

        calc = normalized.get("calculation")
        if isinstance(calc, dict):
            if "levels_to_scan" not in normalized and isinstance(calc.get("levels_to_scan"), int):
                normalized["levels_to_scan"] = calc["levels_to_scan"]
            if "decay_factor" not in normalized and isinstance(calc.get("decay_factor"), (int, float)):
                normalized["decay_factor"] = float(calc["decay_factor"])
            if "weighting_enabled" not in normalized:
                method = str(calc.get("method", "")).lower()
                if method:
                    normalized["weighting_enabled"] = "weighted" in method

        confirmation = normalized.get("confirmation")
        if isinstance(confirmation, dict):
            if "require_consecutive_readings" not in normalized and isinstance(
                confirmation.get("require_consecutive_readings"), int
            ):
                normalized["require_consecutive_readings"] = confirmation["require_consecutive_readings"]
            if "consecutive_interval_ms" not in normalized and isinstance(
                confirmation.get("consecutive_interval_ms"), int
            ):
                normalized["consecutive_interval_ms"] = confirmation["consecutive_interval_ms"]

        thresholds = normalized.get("thresholds")
        if isinstance(thresholds, dict):
            if "min_imbalance_long" not in normalized and isinstance(thresholds.get("long"), (int, float)):
                normalized["min_imbalance_long"] = float(thresholds["long"])
            if "min_imbalance_short" not in normalized and isinstance(thresholds.get("short"), (int, float)):
                normalized["min_imbalance_short"] = float(thresholds["short"])

        dynamic = normalized.get("dynamic_thresholds")
        if isinstance(dynamic, dict) and "min_imbalance_long" not in normalized:
            minimum = dynamic.get("minimum_value")
            maximum = dynamic.get("maximum_value")
            if isinstance(minimum, (int, float)) and isinstance(maximum, (int, float)) and minimum <= maximum:
                midpoint = (float(minimum) + float(maximum)) / 2.0
                normalized["min_imbalance_long"] = midpoint
                if "min_imbalance_short" not in normalized:
                    normalized["min_imbalance_short"] = -midpoint

        if "min_imbalance_short" not in normalized and isinstance(normalized.get("min_imbalance_long"), (int, float)):
            normalized["min_imbalance_short"] = -float(normalized["min_imbalance_long"])

        return normalized


class TradeFrequencyConfig(StrictBaseModel):
    max_trades_per_pair_per_hour: int | Dict[str, int] = 1
    min_time_between_trades_minutes: int | Dict[str, int] = 60
    cooloff_after_loss_minutes: int | Dict[str, int] = 30
    cooloff_after_win_minutes: int | Dict[str, int] = 5


class ConcurrentPositionsConfig(StrictBaseModel):
    max_total: int | Dict[str, int] = 3
    max_per_pair: int = 1
    max_direction_exposure: int | Dict[str, int] = 2
    max_same_currency_exposure: Optional[int | Dict[str, int]] = None


class EntryRulesConfig(StrictBaseModel):
    trading_hours: TradingHoursConfig
    spread_requirement: SpreadRequirementConfig
    order_book_imbalance: OrderBookImbalanceConfig
    trade_frequency: TradeFrequencyConfig
    concurrent_positions: ConcurrentPositionsConfig


class ExitLeafConfig(StrictBaseModel):
    type: Optional[str] = None
    value: Optional[float] = None
    enabled: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_base_value(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        normalized = dict(raw)
        if "value" not in normalized and isinstance(normalized.get("base_value"), (int, float)):
            normalized["value"] = float(normalized["base_value"])
        return normalized


class TimeExitConfig(StrictBaseModel):
    enabled: bool = True
    max_duration_minutes: int = 10
    exit_immediately_if_not_profitable: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_phase_payload(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        normalized = dict(raw)
        if "exit_immediately_if_not_profitable" not in normalized:
            legacy = normalized.get("exit_if_not_profitable")
            if isinstance(legacy, bool):
                normalized["exit_immediately_if_not_profitable"] = legacy
        return normalized


class ExitRulesConfig(StrictBaseModel):
    take_profit: ExitLeafConfig
    stop_loss: ExitLeafConfig
    time_exit: TimeExitConfig
    trailing_stop: ExitLeafConfig
    breakeven: ExitLeafConfig


class TradingLogicConfig(StrictBaseModel):
    name: str
    description: str
    entry_rules: EntryRulesConfig
    exit_rules: ExitRulesConfig
    requires_connection: bool = True
    auto_start_on_connect: bool = True
    pause_on_disconnect: bool = True


class DailyLimitsConfig(StrictBaseModel):
    max_loss_percent: float = 2.0
    max_loss_absolute: float = 0.0
    max_profit_target_percent: float = 5.0
    reset_at_midnight: bool = True
    action_on_limit_hit: str = "stop_trading_until_next_day"


class DrawdownLimitsConfig(StrictBaseModel):
    max_drawdown_percent: float = 5.0
    max_drawdown_absolute: float = 0.0
    measure_from_peak: bool = True
    action_on_limit_hit: str = "stop_trading_permanent_review"


class PositionLimitsConfig(StrictBaseModel):
    max_lot_size_per_trade: float = 0.1
    max_total_lot_size: float = 0.3
    max_position_value_usd: float = 10000.0


class CorrelationGuardConfig(StrictBaseModel):
    enabled: bool = True
    correlation_threshold: float = 0.7
    forbidden_pairs: List[List[str]] = Field(default_factory=list)
    check_on_entry: bool = True
    check_every_minute: bool = True


class NewsGuardConfig(StrictBaseModel):
    enabled: bool = True
    calendar_source: str = "builtin"
    high_impact_only: bool = True
    minutes_before: int = 5
    minutes_after: int = 5
    flat_during_news: bool = True
    close_positions_before_news: bool = True


class ConnectionGuardConfig(StrictBaseModel):
    max_disconnect_seconds: int = 10
    action_on_disconnect: str = "close_all_positions_and_halt"
    reconnect_attempts: int = 3
    reconnect_delay_seconds: int = 5
    require_credentials_for_reconnect: bool = True


class HeartbeatConfig(StrictBaseModel):
    enabled: bool = True
    interval_seconds: int = 30
    missed_heartbeats_tolerance: int = 2
    action_on_timeout: str = "halt_trading"


class SafetyMonitorConfig(StrictBaseModel):
    enabled: bool = True
    independent_process: bool = True
    requires_account: bool = True
    daily_limits: DailyLimitsConfig
    drawdown_limits: DrawdownLimitsConfig
    position_limits: PositionLimitsConfig
    correlation_guard: CorrelationGuardConfig
    news_guard: NewsGuardConfig
    connection_guard: ConnectionGuardConfig
    heartbeat: HeartbeatConfig


class PositionSizingConfig(StrictBaseModel):
    method: str = "fixed_lot"
    fixed_lot_size: float = 0.1
    auto_calculate_from_risk: bool = False


class RiskParametersConfig(StrictBaseModel):
    base_currency: str = "USD"
    account_balance: float = 0.0
    risk_per_trade_percent: float = 0.5
    max_risk_per_day_percent: float = 2.0
    position_sizing: PositionSizingConfig
    requires_account_info: bool = True


class TickDataConfig(StrictBaseModel):
    enabled: bool = True
    interval_ms: int = 100
    store_in_database: bool = True
    retention_days: int = 30
    start_on_connect: bool = True


class OrderBookConfig(StrictBaseModel):
    enabled: bool = True
    levels: int = 10
    interval_ms: int = 500
    store_in_database: bool = True
    retention_days: int = 7
    start_on_connect: bool = True


class OHLCConfig(StrictBaseModel):
    timeframes: List[str] = Field(default_factory=lambda: ["M1", "M5"])
    bars_to_keep: int = 1000
    update_interval_seconds: int = 60
    start_on_connect: bool = True


class AccountInfoDataConfig(StrictBaseModel):
    update_interval_seconds: int = 10
    store_balance_history: bool = True


class DataScrapingConfig(StrictBaseModel):
    tick_data: TickDataConfig
    order_book: OrderBookConfig
    ohlc_data: OHLCConfig
    account_info: AccountInfoDataConfig


class LogOutputConfig(FlexibleBaseModel):
    type: str


class LoggingConfig(StrictBaseModel):
    level: str = "INFO"
    format: str = "json"
    outputs: List[LogOutputConfig] = Field(default_factory=list)
    components: Dict[str, str] = Field(default_factory=dict)


class AlertsConfig(FlexibleBaseModel):
    enabled: bool = True
    channels: List[Dict[str, Any]] = Field(default_factory=list)
    events: Dict[str, Any] = Field(default_factory=dict)


class DashboardAuthConfig(StrictBaseModel):
    enabled: bool = True
    username: str = "admin"
    password_hash: str = ""
    session_timeout_minutes: int = 60


class DashboardWebServerConfig(StrictBaseModel):
    host: str = "0.0.0.0"
    port: int = 5000
    session_secret: str
    ssl_enabled: bool = False
    ssl_cert_path: str = ""
    ssl_key_path: str = ""


class DashboardConfig(StrictBaseModel):
    enabled: bool = True
    web_server: DashboardWebServerConfig
    authentication: DashboardAuthConfig
    refresh_interval_seconds: float = 2
    ui_components: Dict[str, Any] = Field(default_factory=dict)


class AccountFieldConfig(FlexibleBaseModel):
    name: str
    label: str
    type: str
    required: bool = True


class AccountManagerConfig(StrictBaseModel):
    max_saved_accounts: int = 5
    store_encrypted: bool = True
    auto_test_on_save: bool = True
    account_fields: List[AccountFieldConfig] = Field(default_factory=list)


class ConnectionFlowStep(StrictBaseModel):
    step: str
    timeout_seconds: int = 5


class ConnectionFlowConfig(StrictBaseModel):
    steps: List[ConnectionFlowStep] = Field(default_factory=list)
    on_success: Dict[str, Any] = Field(default_factory=dict)
    on_failure: Dict[str, Any] = Field(default_factory=dict)


class HealthCheckConfig(StrictBaseModel):
    mt5_connection: bool = False
    wine_environment: bool = True
    database_connection: bool = True
    minimum_disk_space_gb: int = 10

    @model_validator(mode="before")
    @classmethod
    def normalize_phase_payload(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        normalized = dict(raw)
        if "minimum_disk_space_gb" not in normalized and isinstance(normalized.get("disk_space_gb"), int):
            normalized["minimum_disk_space_gb"] = normalized["disk_space_gb"]
        return normalized


class StartupConfig(StrictBaseModel):
    auto_start_trading: bool = False
    wait_for_account: bool = True
    load_last_pair_selection: bool = True
    validate_config_on_start: bool = True
    health_check: HealthCheckConfig
    initial_delay_seconds: int = 5


class ShutdownConfig(StrictBaseModel):
    close_all_positions_on_exit: bool = False
    save_state: bool = True
    save_current_account: bool = True
    max_shutdown_wait_seconds: int = 30
    force_kill_if_hung: bool = True


class AppConfig(StrictBaseModel):
    version: str = "1.0.0"
    phase: int = 1
    description: str
    connection_mode: ConnectionMode = ConnectionMode.ZMQ_EA_BRIDGE
    system: SystemConfig
    mt5_connection: MT5ConnectionConfig
    pairs: PairsConfig
    trading_logic: TradingLogicConfig
    safety_monitor: SafetyMonitorConfig
    risk_parameters: RiskParametersConfig
    data_scraping: DataScrapingConfig
    logging: LoggingConfig
    alerts: AlertsConfig
    dashboard: DashboardConfig
    account_manager: AccountManagerConfig
    connection_flow: ConnectionFlowConfig
    startup: StartupConfig
    shutdown: ShutdownConfig

    @model_validator(mode="before")
    @classmethod
    def normalize_phase4_structure(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        normalized = dict(raw)

        # Backfill legacy trading-hours session windows from market_awareness if
        # phase3+/phase4 payload omits explicit london/newyork/asian blocks.
        trading_logic = normalized.get("trading_logic")
        market_awareness = normalized.get("market_awareness")
        if not isinstance(trading_logic, dict) or not isinstance(market_awareness, dict):
            return normalized

        entry_rules = trading_logic.get("entry_rules")
        if not isinstance(entry_rules, dict):
            return normalized
        trading_hours = entry_rules.get("trading_hours")
        if not isinstance(trading_hours, dict):
            return normalized

        sessions_root = market_awareness.get("sessions")
        if not isinstance(sessions_root, dict):
            return normalized
        sessions = sessions_root.get("sessions")
        if not isinstance(sessions, dict):
            return normalized

        updated_hours = dict(trading_hours)
        for key in ("asian", "london", "newyork"):
            if key in updated_hours:
                continue
            source = sessions.get(key)
            if not isinstance(source, dict):
                continue
            if not isinstance(source.get("start_hour"), int) or not isinstance(source.get("end_hour"), int):
                continue
            updated_hours[key] = {
                "start_hour": source["start_hour"],
                "end_hour": source["end_hour"],
                "timezone": str(source.get("timezone", "UTC")),
            }

        if updated_hours != trading_hours:
            updated_entry = dict(entry_rules)
            updated_entry["trading_hours"] = updated_hours
            updated_logic = dict(trading_logic)
            updated_logic["entry_rules"] = updated_entry
            normalized["trading_logic"] = updated_logic
        return normalized

    @property
    def remote_server_active(self) -> bool:
        return self.connection_mode == ConnectionMode.MT5_REMOTE_SERVER

    @property
    def zmq_active(self) -> bool:
        return self.connection_mode == ConnectionMode.ZMQ_EA_BRIDGE
