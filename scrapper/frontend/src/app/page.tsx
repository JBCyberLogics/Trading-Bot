"use client";

import { type FormEvent, type MouseEvent as ReactMouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dashboardSpec from "@/config/dashboard-spec.json";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type LooseObject = Record<string, any>;

type PositionRow = {
  symbol?: string;
  pnl?: number;
  profit?: number;
  volume?: number;
};

type AlertRow = {
  timestamp?: string;
  level?: string;
  event?: string;
  message?: string;
};

type RateBar = {
  timestamp?: number | string;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
};

type StatusPayload = {
  connection?: { status?: string; current_account?: string | null; missed_heartbeats?: number };
  trading?: { running?: boolean; paused?: boolean };
  safety?: { reason?: string; trading_blocked?: boolean; current_drawdown_percent?: number; daily_pnl?: number };
  active_pairs?: string[];
  available_pairs?: string[];
  halted?: boolean;
  halt_reason?: string | null;
  system?: { uptime_seconds?: number; load_1m?: number | null; memory_mb?: number | null; connection_mode?: string };
};

type AccountPayload = {
  login?: number;
  account_login?: string;
  balance?: number;
  equity?: number;
  margin?: number;
  daily_pnl?: number;
  connected?: boolean;
  source?: "bridge" | "cache" | "none" | string;
};

type AccountRow = {
  id?: string;
  account_name?: string;
  login?: number;
  server?: string;
};

type AccountsPayload = {
  items?: AccountRow[];
};

type PairsPayload = {
  active?: string[];
  available?: string[];
};

type PositionsPayload = {
  positions?: PositionRow[];
};

type SafetyPayload = {
  reason?: string;
  trading_blocked?: boolean;
  current_drawdown_percent?: number;
  daily_pnl?: number;
};

type LogsPayload = {
  alerts?: AlertRow[];
};

type TickPayload = {
  symbol?: string;
  bid?: number;
  ask?: number;
  spread_pips?: number;
  error?: string;
  connected?: boolean;
  source?: string;
  warning?: string;
};

type OrderBookPayload = {
  symbol?: string;
  bids?: Array<{ price?: number; volume?: number }>;
  asks?: Array<{ price?: number; volume?: number }>;
  imbalance?: number;
  error?: string;
  connected?: boolean;
  source?: string;
  warning?: string;
};

type RatesPayload = {
  bars?: RateBar[];
  error?: string;
  connected?: boolean;
  source?: string;
};

type SafeConfigPayload = {
  startup?: {
    auto_start_trading?: boolean;
  };
  trading_logic?: {
    entry_rules?: {
      trading_hours?: {
        london?: { start_hour?: number; end_hour?: number };
        newyork?: { start_hour?: number; end_hour?: number };
        asian?: { start_hour?: number; end_hour?: number };
      };
    };
  };
};

type KpiCard = {
  id: string;
  title: string;
  value: number;
  change: number;
  metrics: string[];
  requiresLiveAccount?: boolean;
};

type ChartPoint = {
  id: string;
  x: number;
  timestamp?: number | string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  openY: number;
  closeY: number;
  highY: number;
  lowY: number;
  bodyTop: number;
  bodyHeight: number;
  volumeTop: number;
  volumeHeight: number;
  bullish: boolean;
};

type ChartGridPrice = {
  y: number;
  value: number;
};

type ChartGridTime = {
  x: number;
  label: string;
};

type ChartModel = {
  viewWidth: number;
  viewHeight: number;
  axisWidth: number;
  bodyWidth: number;
  plotLeft: number;
  plotRight: number;
  plotTop: number;
  plotBottom: number;
  minPrice: number;
  maxPrice: number;
  points: ChartPoint[];
  priceGrid: ChartGridPrice[];
  timeGrid: ChartGridTime[];
  lastPrice: number | null;
  lastPriceY: number | null;
  lastDelta: number;
};

type ChartHoverState = {
  id: string;
  y: number;
};

const config = dashboardSpec as LooseObject;
const API_BASE = process.env.NEXT_PUBLIC_BACKEND_BASE ?? "/backend";
const TOKEN_KEY = "forex_dashboard_token";

function usd(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function signed(value: number, suffix = ""): string {
  const prefix = value >= 0 ? "+" : "";
  return `${prefix}${value.toFixed(2)}${suffix}`;
}

function hhmmss(iso: string | undefined): string {
  if (!iso) return "--:--:--";
  const date = new Date(iso);
  if (Number.isNaN(date.valueOf())) return "--:--:--";
  return date.toISOString().slice(11, 19);
}

function formatUptime(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return "0m";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function inWindow(hour: number, start: number, end: number): boolean {
  return hour >= start && hour < end;
}

function parseBarTimestamp(raw: number | string | undefined): number | null {
  if (raw === undefined || raw === null) return null;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw > 10_000_000_000 ? raw : raw * 1000;
  }
  if (typeof raw === "string") {
    const asNumber = Number(raw);
    if (Number.isFinite(asNumber)) {
      return asNumber > 10_000_000_000 ? asNumber : asNumber * 1000;
    }
    const parsed = Date.parse(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatAxisTime(raw: number | string | undefined): string {
  const stamp = parseBarTimestamp(raw);
  if (!stamp) return "--:--";
  return new Date(stamp).toISOString().slice(11, 16);
}

function formatTooltipTime(raw: number | string | undefined): string {
  const stamp = parseBarTimestamp(raw);
  if (!stamp) return "--:--:--";
  return new Date(stamp).toISOString().slice(11, 19);
}

export default function Home() {
  const [clock, setClock] = useState<string>("--:--:--");
  const [isHydrated, setIsHydrated] = useState<boolean>(false);
  const [timeframe, setTimeframe] = useState<string>("M5");
  const [syncMode, setSyncMode] = useState<string>("live");
  const [pairFilter, setPairFilter] = useState<string>("all");
  const [pairQuery, setPairQuery] = useState<string>("");
  const [selectedSymbol, setSelectedSymbol] = useState<string>("");

  const [username, setUsername] = useState<string>("admin");
  const [password, setPassword] = useState<string>("");
  const [token, setToken] = useState<string>("");
  const [authError, setAuthError] = useState<string>("");
  const [liveError, setLiveError] = useState<string>("");
  const [liveNotice, setLiveNotice] = useState<string>("");
  const [alertsPanelOpen, setAlertsPanelOpen] = useState<boolean>(false);
  const [alertsFilter, setAlertsFilter] = useState<string>("ALL");
  const [alertsQuery, setAlertsQuery] = useState<string>("");
  const [alertsLastSeenAt, setAlertsLastSeenAt] = useState<string>("");
  const [authLoading, setAuthLoading] = useState<boolean>(false);
  const [connectBusy, setConnectBusy] = useState<boolean>(false);
  const [disconnectBusy, setDisconnectBusy] = useState<boolean>(false);
  const [pairRefreshBusy, setPairRefreshBusy] = useState<boolean>(false);

  const [health, setHealth] = useState<{ status?: string; connection_mode?: string }>({ status: "offline" });
  const [statusData, setStatusData] = useState<StatusPayload>({});
  const [accountData, setAccountData] = useState<AccountPayload>({});
  const [pairsData, setPairsData] = useState<PairsPayload>({ active: [], available: [] });
  const [positionsData, setPositionsData] = useState<PositionsPayload>({ positions: [] });
  const [safetyData, setSafetyData] = useState<SafetyPayload>({});
  const [logsData, setLogsData] = useState<LogsPayload>({ alerts: [] });
  const [safeConfigData, setSafeConfigData] = useState<SafeConfigPayload>({});
  const [ticksBySymbol, setTicksBySymbol] = useState<Record<string, TickPayload>>({});
  const [orderBook, setOrderBook] = useState<OrderBookPayload>({ bids: [], asks: [] });
  const [ratesBars, setRatesBars] = useState<RateBar[]>([]);
  const [latencyMs, setLatencyMs] = useState<number>(0);
  const [chartHover, setChartHover] = useState<ChartHoverState | null>(null);
  const chartSvgRef = useRef<SVGSVGElement | null>(null);

  const colors = config.theme.primary_colors;
  const chartColors = config.theme.chart_colors;
  const headerCfg = config.header.components;

  useEffect(() => {
    const stored = window.localStorage.getItem(TOKEN_KEY);
    if (stored) {
      setToken(stored);
    }
  }, []);

  useEffect(() => {
    setIsHydrated(true);
    const updateClock = () => setClock(new Date().toISOString().slice(11, 19));
    updateClock();
    const id = window.setInterval(updateClock, 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    let stopped = false;

    async function pullHealth() {
      try {
        const response = await fetch(`${API_BASE}/health`, { cache: "no-store" });
        if (!response.ok) return;
        const payload = (await response.json()) as { status?: string; connection_mode?: string };
        if (!stopped) {
          setHealth(payload);
        }
      } catch {
        if (!stopped) {
          setHealth({ status: "offline" });
        }
      }
    }

    void pullHealth();
    const id = window.setInterval(() => {
      void pullHealth();
    }, 5000);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, []);

  const authorizedFetch = useCallback(
    async <T,>(path: string, init?: RequestInit): Promise<T> => {
      const headers = new Headers(init?.headers ?? {});
      if (token) {
        headers.set("Authorization", `Bearer ${token}`);
      }
      if (init?.body && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }

      const response = await fetch(`${API_BASE}${path}`, {
        ...init,
        headers,
        cache: "no-store",
      });

      if (response.status === 401) {
        window.localStorage.removeItem(TOKEN_KEY);
        setToken("");
        throw new Error("unauthorized");
      }

      if (!response.ok) {
        let detail = "";
        const raw = await response.text();
        if (raw) {
          try {
            const parsed = JSON.parse(raw) as { detail?: string };
            detail = parsed.detail ?? raw;
          } catch {
            detail = raw;
          }
        }
        throw new Error(detail || `request_failed:${response.status}`);
      }

      return (await response.json()) as T;
    },
    [token],
  );

  const refreshLiveFeeds = useCallback(async () => {
    if (!token) return;
    try {
      const started = performance.now();
      // Validate auth/session first to avoid parallel 401 bursts on stale tokens.
      const statusPayload = await authorizedFetch<StatusPayload>("/api/status");
      const [accountPayload, pairsPayload, positionsPayload, safetyPayload, logsPayload, cfgPayload] = await Promise.all(
        [
          authorizedFetch<AccountPayload>("/api/account"),
          authorizedFetch<PairsPayload>("/api/pairs"),
          authorizedFetch<PositionsPayload>("/api/positions"),
          authorizedFetch<SafetyPayload>("/api/safety"),
          authorizedFetch<LogsPayload>("/api/logs?limit=120"),
          authorizedFetch<SafeConfigPayload>("/api/config"),
        ],
      );

      setLatencyMs(Math.round(performance.now() - started));
      setStatusData(statusPayload ?? {});
      setAccountData(accountPayload ?? {});
      setPairsData(pairsPayload ?? { active: [], available: [] });
      setPositionsData(positionsPayload ?? { positions: [] });
      setSafetyData(safetyPayload ?? {});
      setLogsData(logsPayload ?? { alerts: [] });
      setSafeConfigData(cfgPayload ?? {});

      const active = Array.isArray(pairsPayload?.active) ? pairsPayload.active : [];
      const streamSymbol = selectedSymbol || active[0] || "";
      if (!selectedSymbol && active[0]) {
        setSelectedSymbol(active[0]);
      }

      if (streamSymbol) {
        try {
          const tickPayload = await authorizedFetch<TickPayload>(`/api/market/tick/${streamSymbol}`);
          setTicksBySymbol({ [streamSymbol]: tickPayload ?? {} });
        } catch {
          setTicksBySymbol({ [streamSymbol]: { symbol: streamSymbol, error: "unavailable" } });
        }

        const [ratesPayload, orderBookPayload] = await Promise.all([
          authorizedFetch<RatesPayload>(
            `/api/market/rates/${streamSymbol}?timeframe=${encodeURIComponent(timeframe)}&count=72`,
          ),
          authorizedFetch<OrderBookPayload>(`/api/market/order-book/${streamSymbol}?levels=20`),
        ]);
        setRatesBars(Array.isArray(ratesPayload?.bars) ? ratesPayload.bars : []);
        setOrderBook(orderBookPayload ?? { bids: [], asks: [] });
      } else {
        setTicksBySymbol({});
        setRatesBars([]);
        setOrderBook({ bids: [], asks: [] });
      }

      setLiveError("");
    } catch (error) {
      if ((error as Error).message !== "unauthorized") {
        setLiveError("Live feed update failed.");
      }
    }
  }, [authorizedFetch, selectedSymbol, timeframe, token]);

  const setSingleActivePair = useCallback(
    async (symbol: string) => {
      const pair = String(symbol).toUpperCase();
      const payload = await authorizedFetch<{ active: string[] }>("/api/pairs/active", {
        method: "PUT",
        body: JSON.stringify({ active: [pair] }),
      });
      const active = Array.isArray(payload.active) ? payload.active : [pair];
      setPairsData((prev) => ({ active, available: Array.isArray(prev.available) ? prev.available : [] }));
      return active;
    },
    [authorizedFetch],
  );

  const loadSymbolIntoChart = useCallback(
    async (symbol: string, tf: string = timeframe) => {
      if (!token || !symbol) return;
      const pair = String(symbol).toUpperCase();
      try {
        await setSingleActivePair(pair);
        const [tickPayload, orderBookPayload, ratesPayload] = await Promise.all([
          authorizedFetch<TickPayload>(`/api/market/tick/${pair}`),
          authorizedFetch<OrderBookPayload>(`/api/market/order-book/${pair}?levels=20`),
          authorizedFetch<RatesPayload>(`/api/market/rates/${pair}?timeframe=${encodeURIComponent(tf)}&count=72`),
        ]);

        if (Array.isArray(ratesPayload?.bars) && ratesPayload.bars.length > 0) {
          setSelectedSymbol(pair);
          setTicksBySymbol({ [pair]: tickPayload ?? {} });
          setOrderBook(orderBookPayload ?? { bids: [], asks: [] });
          setRatesBars(ratesPayload.bars);
          setLiveNotice(`${pair} selected on ${tf}. Previous stream stopped.`);
          setLiveError("");
          return;
        }
        setLiveError(`No chart data returned for ${pair} on ${tf}.`);
      } catch (error) {
        if ((error as Error).message !== "unauthorized") {
          setLiveError(`Failed to load ${pair} from MT5.`);
        }
      }
    },
    [authorizedFetch, setSingleActivePair, timeframe, token],
  );

  const refreshMt5Pairs = useCallback(async () => {
    if (!token || pairRefreshBusy) return;
    setPairRefreshBusy(true);
    try {
      const payload = await authorizedFetch<PairsPayload & { count?: number }>("/api/pairs/refresh", {
        method: "POST",
        body: JSON.stringify({}),
      });
      const active = Array.isArray(payload.active) ? payload.active : [];
      const available = Array.isArray(payload.available) ? payload.available : [];
      setPairsData({ active, available });

      const preferred = selectedSymbol || active[0] || available[0];
      if (preferred) {
        await loadSymbolIntoChart(preferred);
      }
      setLiveNotice(`Loaded ${payload.count ?? available.length} symbols from MT5.`);
      setLiveError("");
    } catch (error) {
      if ((error as Error).message !== "unauthorized") {
        setLiveError("Failed to refresh MT5 symbol list.");
      }
    } finally {
      setPairRefreshBusy(false);
    }
  }, [authorizedFetch, loadSymbolIntoChart, pairRefreshBusy, selectedSymbol, token]);

  useEffect(() => {
    if (!token) return;
    void refreshLiveFeeds();
    if (syncMode !== "live") return;
    const id = window.setInterval(() => {
      void refreshLiveFeeds();
    }, 2000);
    return () => window.clearInterval(id);
  }, [refreshLiveFeeds, syncMode, token]);

  useEffect(() => {
    if (!liveNotice) return;
    const id = window.setTimeout(() => setLiveNotice(""), 3500);
    return () => window.clearTimeout(id);
  }, [liveNotice]);

  useEffect(() => {
    setChartHover(null);
  }, [selectedSymbol, timeframe]);

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAuthLoading(true);
    setAuthError("");
    try {
      const response = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) {
        setAuthError("Invalid credentials.");
        return;
      }
      const payload = (await response.json()) as { token?: string };
      if (!payload.token) {
        setAuthError("Login token missing in response.");
        return;
      }
      setToken(payload.token);
      window.localStorage.setItem(TOKEN_KEY, payload.token);
      setPassword("");
    } catch {
      setAuthError("Unable to reach backend auth endpoint.");
    } finally {
      setAuthLoading(false);
    }
  };

  const handleLogout = async () => {
    if (!token) return;
    try {
      await authorizedFetch<{ ok: boolean }>("/api/auth/logout", { method: "POST", body: JSON.stringify({}) });
    } catch {
      // Ignore logout transport errors; token is still dropped locally.
    }
    window.localStorage.removeItem(TOKEN_KEY);
    setToken("");
    setAlertsPanelOpen(false);
  };

  const callTradingAction = async (path: string, body: Record<string, unknown> = {}, notice?: string) => {
    if (path === "/api/trading/start") {
      if ((statusData.connection?.status ?? "disconnected") !== "connected") {
        setLiveError("Connect MT5 before starting the bot.");
        return;
      }
      if (!Array.isArray(pairsData.active) || pairsData.active.length === 0) {
        setLiveError("Select at least one pair before starting the bot.");
        return;
      }
    }
    try {
      const payload = await authorizedFetch<Record<string, unknown>>(path, { method: "POST", body: JSON.stringify(body) });
      await refreshLiveFeeds();
      if (path === "/api/trading/start") {
        const analysis = typeof payload.analysis === "string" ? payload.analysis : "";
        if (analysis === "triggered") {
          setLiveNotice("Trading started. Analysis triggered immediately.");
        } else if (analysis) {
          setLiveNotice(`Trading started (${analysis}).`);
        } else if (notice) {
          setLiveNotice(notice);
        }
      } else if (notice) {
        setLiveNotice(notice);
      }
      setLiveError("");
    } catch {
      setLiveError("Trading action failed.");
    }
  };

  const connectMt5 = async () => {
    if (!token || connectBusy) return;
    setConnectBusy(true);
    setLiveError("");
    try {
      const latestStatus = await authorizedFetch<StatusPayload>("/api/status");
      if (latestStatus.halted) {
        await authorizedFetch("/api/system/reset-halt", {
          method: "POST",
          body: JSON.stringify({ reason: "connect_button_preflight" }),
        });
        setLiveNotice("System halt cleared. Connecting to MT5...");
      }

      const payload = await authorizedFetch<AccountsPayload>("/api/accounts");
      const rows = Array.isArray(payload.items) ? payload.items.filter((row) => row.id) : [];
      if (rows.length === 0) {
        setLiveError("No saved MT5 account found. Add one first.");
        return;
      }

      const currentAccountId = String(latestStatus.connection?.current_account ?? statusData.connection?.current_account ?? "");
      const selected =
        rows.find((row) => String(row.id) === currentAccountId) ??
        rows[0];

      const accountId = String(selected.id);
      const connectRequest = async () =>
        authorizedFetch("/api/connection/connect", {
          method: "POST",
          body: JSON.stringify({ account_id: accountId }),
        });

      try {
        await connectRequest();
      } catch (error) {
        const reason = (error as Error).message || "";
        if (reason.includes("system_halted:")) {
          await authorizedFetch("/api/system/reset-halt", {
            method: "POST",
            body: JSON.stringify({ reason: "connect_button_recovery" }),
          });
          await connectRequest();
          setLiveNotice("System halt cleared. Reconnecting to MT5...");
        } else {
          throw error;
        }
      }

      const refreshed = await authorizedFetch<PairsPayload & { count?: number }>("/api/pairs/refresh", {
        method: "POST",
        body: JSON.stringify({}),
      });
      const active = Array.isArray(refreshed.active) ? refreshed.active : [];
      const available = Array.isArray(refreshed.available) ? refreshed.available : [];
      setPairsData({ active, available });

      await refreshLiveFeeds();
      const preferred = active[0] || available[0] || selectedSymbol;
      if (preferred) {
        await loadSymbolIntoChart(preferred);
      }
      const label = selected.account_name ?? (selected.login !== undefined ? String(selected.login) : String(selected.id));
      setLiveNotice(`Connected account ${label}. ${refreshed.count ?? available.length} symbols loaded.`);
    } catch (error) {
      const reason = (error as Error).message || "unknown_error";
      if (reason !== "unauthorized") {
        setLiveError(`MT5 connect failed: ${reason}`);
      }
    } finally {
      setConnectBusy(false);
    }
  };

  const disconnectMt5 = async () => {
    if (!token || disconnectBusy) return;
    setDisconnectBusy(true);
    setLiveError("");
    try {
      await authorizedFetch<{ status?: string }>("/api/connection/disconnect", {
        method: "POST",
        body: JSON.stringify({}),
      });
      setTicksBySymbol({});
      setOrderBook({ bids: [], asks: [] });
      setRatesBars([]);
      await refreshLiveFeeds();
      setLiveNotice("MT5 disconnected.");
    } catch (error) {
      const reason = (error as Error).message || "unknown_error";
      if (reason !== "unauthorized") {
        setLiveError(`MT5 disconnect failed: ${reason}`);
      }
    } finally {
      setDisconnectBusy(false);
    }
  };

  const toggleStartupAutoTrading = async () => {
    const current = Boolean(safeConfigData.startup?.auto_start_trading);
    try {
      await authorizedFetch<{ ok: boolean }>("/api/config", {
        method: "PUT",
        body: JSON.stringify({ startup_auto_start_trading: !current }),
      });
      await refreshLiveFeeds();
      setLiveNotice(`Startup auto-trading ${!current ? "enabled" : "disabled"}.`);
      setLiveError("");
    } catch {
      setLiveError("Failed to update startup policy.");
    }
  };

  const activePairs = useMemo(() => (Array.isArray(pairsData.active) ? pairsData.active : []), [pairsData.active]);
  const availablePairs = useMemo(
    () => (Array.isArray(pairsData.available) ? pairsData.available : []),
    [pairsData.available],
  );
  const positions = useMemo(
    () => (Array.isArray(positionsData.positions) ? positionsData.positions : []),
    [positionsData.positions],
  );
  const alerts = useMemo(() => (Array.isArray(logsData.alerts) ? logsData.alerts : []), [logsData.alerts]);
  const alertLevelOptions = useMemo(() => ["ALL", "INFO", "WARN", "ERROR", "CRITICAL"], []);

  const markAlertsSeen = useCallback(() => {
    const latest = alerts[alerts.length - 1]?.timestamp;
    setAlertsLastSeenAt(latest ?? new Date().toISOString());
  }, [alerts]);

  const unreadAlertCount = useMemo(() => {
    const seenMs = alertsLastSeenAt ? Date.parse(alertsLastSeenAt) : Number.NEGATIVE_INFINITY;
    if (!Number.isFinite(seenMs)) return alerts.length;
    return alerts.reduce((count, row) => {
      const stamp = row.timestamp ? Date.parse(row.timestamp) : Number.NaN;
      return Number.isFinite(stamp) && stamp > seenMs ? count + 1 : count;
    }, 0);
  }, [alerts, alertsLastSeenAt]);

  const filteredAlerts = useMemo(() => {
    const q = alertsQuery.trim().toLowerCase();
    return alerts.filter((row) => {
      const level = String(row.level ?? "INFO").toUpperCase();
      if (alertsFilter !== "ALL" && level !== alertsFilter) {
        return false;
      }
      if (!q) {
        return true;
      }
      const haystack = `${row.event ?? ""} ${row.message ?? ""} ${level}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [alerts, alertsFilter, alertsQuery]);

  const toggleAlertsPanel = useCallback(() => {
    setAlertsPanelOpen((prev) => {
      const next = !prev;
      if (next) {
        void refreshLiveFeeds();
        markAlertsSeen();
      }
      return next;
    });
  }, [markAlertsSeen, refreshLiveFeeds]);

  useEffect(() => {
    if (!alertsPanelOpen) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAlertsPanelOpen(false);
      }
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [alertsPanelOpen]);

  useEffect(() => {
    if (alertsPanelOpen) {
      markAlertsSeen();
    }
  }, [alertsPanelOpen, alerts, markAlertsSeen]);

  const togglePair = async (symbol: string) => {
    const pair = String(symbol).toUpperCase();
    const isActive = activePairs.includes(pair);
    try {
      if (!isActive) {
        await loadSymbolIntoChart(pair);
      } else {
        await authorizedFetch<{ active: string[] }>("/api/pairs/active", {
          method: "PUT",
          body: JSON.stringify({ active: [] }),
        });
        setPairsData((prev) => ({ active: [], available: Array.isArray(prev.available) ? prev.available : [] }));
        if (selectedSymbol === pair) {
          setSelectedSymbol("");
          setRatesBars([]);
          setOrderBook({ bids: [], asks: [] });
          setTicksBySymbol({});
        }
      }
      await refreshLiveFeeds();
      setLiveNotice(isActive ? `${pair} removed from active pair.` : `${pair} is now the active pair.`);
    } catch {
      setLiveError(`Failed to update active pair for ${pair}.`);
    }
  };

  const pnlBySymbol = useMemo(() => {
    const map: Record<string, number> = {};
    for (const row of positions) {
      const symbol = String(row.symbol ?? "").toUpperCase();
      if (!symbol) continue;
      const value = Number(row.pnl ?? row.profit ?? 0);
      map[symbol] = (map[symbol] ?? 0) + value;
    }
    return map;
  }, [positions]);

  const selectedTick = selectedSymbol ? ticksBySymbol[selectedSymbol] : undefined;

  const chartModel = useMemo<ChartModel>(() => {
    const viewWidth = 980;
    const viewHeight = 430;
    const axisWidth = 82;
    const padTop = 14;
    const padRight = 8;
    const padLeft = 10;
    const padBottom = 44;
    const plotWidth = viewWidth - axisWidth - padLeft - padRight;
    const plotHeight = viewHeight - padTop - padBottom;
    const plotLeft = padLeft;
    const plotRight = padLeft + plotWidth;
    const plotTop = padTop;
    const plotBottom = padTop + plotHeight;
    const bars = ratesBars.slice(-72);

    if (bars.length === 0) {
      return {
        viewWidth,
        viewHeight,
        axisWidth,
        bodyWidth: 7,
        plotLeft,
        plotRight,
        plotTop,
        plotBottom,
        minPrice: 0,
        maxPrice: 0,
        points: [],
        priceGrid: [],
        timeGrid: [],
        lastPrice: null,
        lastPriceY: null,
        lastDelta: 0,
      };
    }

    const normalized = bars.map((row, idx) => {
      const open = Number(row.open ?? row.close ?? 0);
      const close = Number(row.close ?? open);
      const high = Number(row.high ?? Math.max(open, close));
      const low = Number(row.low ?? Math.min(open, close));
      const raw = row as LooseObject;
      const volume =
        Number(raw.tick_volume ?? raw.real_volume ?? raw.volume ?? Math.max(1, Math.abs(high - low) * 1_000_000));
      return { idx, open, close, high, low, volume, timestamp: row.timestamp };
    });

    const highs = normalized.map((row) => row.high);
    const lows = normalized.map((row) => row.low);
    const maxPriceRaw = Math.max(...highs);
    const minPriceRaw = Math.min(...lows);
    const padPrice = Math.max((maxPriceRaw - minPriceRaw) * 0.08, Math.abs(maxPriceRaw || 1) * 0.00008);
    const maxPrice = maxPriceRaw + padPrice;
    const minPrice = minPriceRaw - padPrice;
    const span = Math.max(0.0000001, maxPrice - minPrice);
    const toY = (price: number) => padTop + ((maxPrice - price) / span) * plotHeight;

    const count = normalized.length;
    const step = count > 1 ? plotWidth / (count - 1) : plotWidth;
    const bodyWidth = Math.max(4, Math.min(12, step * 0.55));
    const maxVolume = Math.max(...normalized.map((row) => row.volume), 1);
    const volBandHeight = plotHeight * 0.23;
    const volBandBase = plotBottom;

    const points: ChartPoint[] = normalized.map((row) => {
      const x = padLeft + row.idx * step;
      const openY = toY(row.open);
      const closeY = toY(row.close);
      const highY = toY(row.high);
      const lowY = toY(row.low);
      const bodyTop = Math.min(openY, closeY);
      const bodyHeight = Math.max(1.2, Math.abs(openY - closeY));
      const volumeHeight = Math.max(1, (row.volume / maxVolume) * volBandHeight);
      return {
        id: `bar-${row.timestamp ?? row.idx}-${row.idx}`,
        x,
        timestamp: row.timestamp,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume,
        openY,
        closeY,
        highY,
        lowY,
        bodyTop,
        bodyHeight,
        volumeTop: volBandBase - volumeHeight,
        volumeHeight,
        bullish: row.close >= row.open,
      };
    });

    const gridRows = 6;
    const priceGrid: ChartGridPrice[] = Array.from({ length: gridRows }, (_, idx) => {
      const ratio = idx / (gridRows - 1);
      return {
        y: padTop + plotHeight * ratio,
        value: maxPrice - span * ratio,
      };
    });

    const gridCols = Math.min(7, count);
    const timeGrid: ChartGridTime[] = Array.from({ length: gridCols }, (_, idx) => {
      const barIdx = Math.round((idx / Math.max(1, gridCols - 1)) * (count - 1));
      const row = normalized[Math.min(count - 1, Math.max(0, barIdx))];
      return {
        x: padLeft + barIdx * step,
        label: formatAxisTime(row?.timestamp),
      };
    });

    const last = normalized[count - 1];
    const previous = normalized[Math.max(0, count - 2)];
    return {
      viewWidth,
      viewHeight,
      axisWidth,
      bodyWidth,
      plotLeft,
      plotRight,
      plotTop,
      plotBottom,
      minPrice,
      maxPrice,
      points,
      priceGrid,
      timeGrid,
      lastPrice: last?.close ?? null,
      lastPriceY: last ? toY(last.close) : null,
      lastDelta: last && previous ? last.close - previous.close : 0,
    };
  }, [ratesBars]);

  const latestBar = useMemo(() => {
    if (ratesBars.length === 0) return null;
    return ratesBars[ratesBars.length - 1] ?? null;
  }, [ratesBars]);

  const hoveredPoint = useMemo(() => {
    if (!chartHover) return null;
    return chartModel.points.find((point) => point.id === chartHover.id) ?? null;
  }, [chartHover, chartModel.points]);

  const hoverPrice = useMemo(() => {
    if (!chartHover || chartModel.maxPrice <= chartModel.minPrice) return null;
    const clampedY = Math.max(chartModel.plotTop, Math.min(chartModel.plotBottom, chartHover.y));
    const ratio = (clampedY - chartModel.plotTop) / Math.max(1, chartModel.plotBottom - chartModel.plotTop);
    return chartModel.maxPrice - ratio * (chartModel.maxPrice - chartModel.minPrice);
  }, [chartHover, chartModel.maxPrice, chartModel.minPrice, chartModel.plotBottom, chartModel.plotTop]);

  const hoverPipSize = selectedSymbol.includes("JPY") ? 0.01 : 0.0001;

  const hoverTooltip = useMemo(() => {
    if (!chartHover || !hoveredPoint) return null;
    const width = 206;
    const height = 116;
    const margin = 10;
    const nearRightEdge = hoveredPoint.x > chartModel.plotRight - width - margin;
    const x = nearRightEdge ? hoveredPoint.x - width - margin : hoveredPoint.x + margin;
    const yRaw = chartHover.y + margin;
    const y = Math.max(
      chartModel.plotTop + margin,
      Math.min(yRaw, chartModel.plotBottom - height - margin),
    );
    return { x, y, width, height };
  }, [chartHover, hoveredPoint, chartModel.plotBottom, chartModel.plotRight, chartModel.plotTop]);

  const handleChartMouseMove = useCallback(
    (event: ReactMouseEvent<SVGSVGElement>) => {
      if (chartModel.points.length === 0 || !chartSvgRef.current) return;
      const rect = chartSvgRef.current.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;

      const x = ((event.clientX - rect.left) / rect.width) * chartModel.viewWidth;
      const y = ((event.clientY - rect.top) / rect.height) * chartModel.viewHeight;
      const clampedY = Math.max(chartModel.plotTop, Math.min(chartModel.plotBottom, y));

      let nearest = chartModel.points[0];
      let best = Math.abs(nearest.x - x);
      for (let idx = 1; idx < chartModel.points.length; idx += 1) {
        const row = chartModel.points[idx];
        const dist = Math.abs(row.x - x);
        if (dist < best) {
          nearest = row;
          best = dist;
        }
      }

      setChartHover({ id: nearest.id, y: clampedY });
    },
    [chartModel.plotBottom, chartModel.plotTop, chartModel.points, chartModel.viewHeight, chartModel.viewWidth],
  );

  const handleChartMouseLeave = useCallback(() => {
    setChartHover(null);
  }, []);

  const availableRows = useMemo(() => {
    const majors = new Set(["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]);
    return availablePairs.filter((symbol) => {
      const key = symbol.toUpperCase();
      const queryMatches = key.includes(pairQuery.trim().toUpperCase());
      if (!queryMatches) return false;
      if (pairFilter === "active") return activePairs.includes(symbol);
      if (pairFilter === "inactive") return !activePairs.includes(symbol);
      if (pairFilter === "majors") return majors.has(key);
      if (pairFilter === "minors") return !majors.has(key);
      return true;
    });
  }, [activePairs, availablePairs, pairFilter, pairQuery]);

  const stripSymbols = useMemo(() => {
    const pair = selectedSymbol || activePairs[0] || "";
    return pair ? [pair] : [];
  }, [activePairs, selectedSymbol]);

  const rawConnection = statusData.connection?.status ?? health.status ?? "offline";
  const showLiveAccount = rawConnection === "connected" && accountData.connected !== false;

  const balance = showLiveAccount ? Number(accountData.balance ?? 0) : 0;
  const equity = showLiveAccount ? Number(accountData.equity ?? 0) : 0;
  const dailyPnl = showLiveAccount ? Number(accountData.daily_pnl ?? safetyData.daily_pnl ?? 0) : 0;
  const drawdownPercent = Number(safetyData.current_drawdown_percent ?? statusData.safety?.current_drawdown_percent ?? 0);
  const equityDeltaPercent = balance > 0 ? ((equity - balance) / balance) * 100.0 : 0;
  const dailyDeltaPercent = balance > 0 ? (dailyPnl / balance) * 100.0 : 0;

  const kpiCards = useMemo<KpiCard[]>(
    () => [
      {
        id: "today",
        title: "TODAY",
        value: dailyPnl,
        change: dailyDeltaPercent,
        metrics: [`Open Positions: ${positions.length}`, `Alerts: ${alerts.length}`],
      },
      {
        id: "equity",
        title: "EQUITY",
        value: equity,
        change: equityDeltaPercent,
        metrics: [`Connection: ${statusData.connection?.status ?? "offline"}`, `Latency: ${latencyMs}ms`],
        requiresLiveAccount: true,
      },
      {
        id: "balance",
        title: "BALANCE",
        value: balance,
        change: -drawdownPercent,
        metrics: [`Drawdown: ${drawdownPercent.toFixed(2)}%`, `Active Pair: ${activePairs[0] ?? "none"}`],
        requiresLiveAccount: true,
      },
    ],
    [
      activePairs,
      alerts.length,
      balance,
      dailyDeltaPercent,
      dailyPnl,
      drawdownPercent,
      equity,
      equityDeltaPercent,
      latencyMs,
      positions.length,
      statusData.connection?.status,
    ],
  );

  const tradingHours = safeConfigData.trading_logic?.entry_rules?.trading_hours;
  const nowUtcHour = isHydrated && /^\d{2}/.test(clock) ? Number(clock.slice(0, 2)) : 0;
  const londonActive = inWindow(nowUtcHour, Number(tradingHours?.london?.start_hour ?? 8), Number(tradingHours?.london?.end_hour ?? 17));
  const nyActive = inWindow(nowUtcHour, Number(tradingHours?.newyork?.start_hour ?? 13), Number(tradingHours?.newyork?.end_hour ?? 22));
  const asianActive = inWindow(nowUtcHour, Number(tradingHours?.asian?.start_hour ?? 0), Number(tradingHours?.asian?.end_hour ?? 9));

  const sessionKey = londonActive && nyActive ? "overlap" : londonActive ? "london" : nyActive ? "newyork" : asianActive ? "asian" : "london";
  const sessionCfg = headerCfg.session_badge.sessions[sessionKey] ?? headerCfg.session_badge.sessions.london;

  const mappedConnection =
    rawConnection === "connected"
      ? "connected"
      : rawConnection === "connecting"
        ? "connecting"
        : rawConnection === "disconnected"
          ? "disconnected"
          : "offline";
  const connectionState = headerCfg.connection_status.states[mappedConnection] ?? headerCfg.connection_status.states.offline;

  const accountLabel =
    accountData.login !== undefined
      ? String(accountData.login)
      : accountData.account_login !== undefined
        ? accountData.account_login
        : "N/A";

  const botRunning = Boolean(statusData.trading?.running);
  const botPaused = Boolean(statusData.trading?.paused);
  const startupAutoTrading = Boolean(safeConfigData.startup?.auto_start_trading);
  const displayClock = isHydrated ? clock : "--:--:--";

  if (!token) {
    return (
      <main
        className="forex-dashboard"
        style={{
          ["--profit-green" as string]: colors.profit_green,
          ["--loss-red" as string]: colors.loss_red,
          ["--warning-yellow" as string]: colors.warning_yellow,
          ["--info-blue" as string]: colors.info_blue,
          ["--background-dark" as string]: colors.background_dark,
          ["--card-bg" as string]: colors.card_background,
          ["--card-bg-alt" as string]: colors.card_background_alt,
          ["--border-color" as string]: colors.border,
          ["--text-primary" as string]: colors.text_primary,
          ["--text-secondary" as string]: colors.text_secondary,
          ["--text-muted" as string]: colors.text_muted,
          ["--chart-bull" as string]: chartColors.candle_bull,
          ["--chart-bear" as string]: chartColors.candle_bear,
        }}
      >
        <header className="dashboard-header">
          <div className="header-left">
            <div className="connection-pill" style={{ borderColor: connectionState.color }}>
              <span>{connectionState.icon}</span>
              <span style={{ color: connectionState.color }}>{connectionState.text}</span>
            </div>
            <div className="header-title-wrap">
              <h1>{config.dashboard_name}</h1>
              <p>{config.description}</p>
            </div>
          </div>
          <div className="clock-chip">UTC {displayClock}</div>
        </header>

        <div className="auth-gate">
          <form className="auth-card" onSubmit={handleLogin}>
            <h2>Dashboard Login</h2>
            <p>Authenticate to stream system status and live market feeds.</p>
            <label>
              Username
              <input value={username} onChange={(event) => setUsername(event.target.value)} required />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                autoComplete="current-password"
              />
            </label>
            {authError ? <div className="error-text">{authError}</div> : null}
            <button className="btn btn-start" type="submit" disabled={authLoading}>
              {authLoading ? "Connecting..." : "Login & Start Live Feed"}
            </button>
          </form>
        </div>
      </main>
    );
  }

  return (
    <main
      className="forex-dashboard"
      style={{
        ["--profit-green" as string]: colors.profit_green,
        ["--loss-red" as string]: colors.loss_red,
        ["--warning-yellow" as string]: colors.warning_yellow,
        ["--info-blue" as string]: colors.info_blue,
        ["--background-dark" as string]: colors.background_dark,
        ["--card-bg" as string]: colors.card_background,
        ["--card-bg-alt" as string]: colors.card_background_alt,
        ["--border-color" as string]: colors.border,
        ["--text-primary" as string]: colors.text_primary,
        ["--text-secondary" as string]: colors.text_secondary,
        ["--text-muted" as string]: colors.text_muted,
        ["--chart-bull" as string]: chartColors.candle_bull,
        ["--chart-bear" as string]: chartColors.candle_bear,
      }}
    >
      <header className="dashboard-header">
        <div className="header-left">
          <div className="connection-pill" style={{ borderColor: connectionState.color }}>
            <span>{connectionState.icon}</span>
            <span style={{ color: connectionState.color }}>{connectionState.text}</span>
          </div>
          <div className="header-title-wrap">
            <h1>{config.dashboard_name}</h1>
            <p>{config.description}</p>
          </div>
        </div>

        <div className="header-right">
          <div className="account-chip">
            <span>Acct {accountLabel}</span>
            <span>{showLiveAccount ? usd(balance) : "--"}</span>
            <span className={showLiveAccount ? (dailyPnl >= 0 ? "chip-good" : "chip-bad") : "muted"}>
              {showLiveAccount ? signed(dailyPnl) : "OFFLINE"}
            </span>
          </div>

          <div className="session-chip" style={{ color: sessionCfg.color, backgroundColor: sessionCfg.bg }}>
            {sessionCfg.label}
          </div>

          <button
            className="icon-chip"
            type="button"
            aria-label="notifications"
            aria-haspopup="dialog"
            aria-expanded={alertsPanelOpen}
            aria-controls="alerts-panel"
            onClick={toggleAlertsPanel}
          >
            {headerCfg.notification_bell.icon}
            <small>{Math.min(99, unreadAlertCount)}</small>
          </button>
          <button
            className="icon-chip"
            type="button"
            aria-label="settings"
            onClick={() => void toggleStartupAutoTrading()}
            title={startupAutoTrading ? "Disable startup auto-trading" : "Enable startup auto-trading"}
          >
            {headerCfg.settings_icon.icon}
          </button>
          <button className="icon-chip" type="button" aria-label="logout" onClick={handleLogout}>
            {headerCfg.user_menu.icon}
          </button>
          <div className="clock-chip">UTC {displayClock}</div>
        </div>
      </header>

      {alertsPanelOpen ? (
        <div className="alerts-overlay" role="presentation" onClick={() => setAlertsPanelOpen(false)}>
          <aside
            id="alerts-panel"
            className="alerts-panel"
            role="dialog"
            aria-label="Notifications and alerts"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="alerts-panel-head">
              <div>
                <h3>Notifications & Alerts</h3>
                <p>{filteredAlerts.length} visible</p>
              </div>
              <div className="alerts-panel-actions">
                <button
                  type="button"
                  className="icon-action"
                  title="Refresh alerts"
                  onClick={() => void refreshLiveFeeds()}
                >
                  ↻
                </button>
                <button
                  type="button"
                  className="icon-action"
                  title="Mark all as read"
                  onClick={() => {
                    markAlertsSeen();
                    setLiveNotice("Alerts marked as read.");
                  }}
                >
                  ✓
                </button>
                <button
                  type="button"
                  className="icon-action"
                  title="Close alerts panel"
                  onClick={() => setAlertsPanelOpen(false)}
                >
                  ✕
                </button>
              </div>
            </div>

            <div className="alerts-toolbar">
              <input
                type="search"
                value={alertsQuery}
                onChange={(event) => setAlertsQuery(event.target.value)}
                placeholder="Search event or message..."
                aria-label="Search alerts"
              />
              <div className="alerts-filters">
                {alertLevelOptions.map((level) => (
                  <button
                    key={level}
                    type="button"
                    className={alertsFilter === level ? "active" : ""}
                    onClick={() => setAlertsFilter(level)}
                  >
                    {level}
                  </button>
                ))}
              </div>
            </div>

            <div className="alerts-list" role="list">
              {filteredAlerts
                .slice()
                .reverse()
                .map((entry, idx) => {
                  const level = String(entry.level ?? "INFO").toUpperCase();
                  const stamp = hhmmss(entry.timestamp);
                  return (
                    <article className={`alerts-item level-${level.toLowerCase()}`} key={`${entry.timestamp ?? "x"}-${idx}`} role="listitem">
                      <div className="alerts-item-meta">
                        <span>{stamp}</span>
                        <strong>{entry.event ?? "event"}</strong>
                        <small>{level}</small>
                      </div>
                      <p>{entry.message ?? "-"}</p>
                    </article>
                  );
                })}
              {filteredAlerts.length === 0 ? <div className="alerts-empty">No alerts match current filter.</div> : null}
            </div>
          </aside>
        </div>
      ) : null}

      {liveError ? <div className="live-error">{liveError}</div> : null}
      {liveNotice ? <div className="live-note">{liveNotice}</div> : null}

      <div className="dashboard-body">
        <section className="main-content">
          <div className="kpi-grid">
            {kpiCards.map((card) => {
              const offlineCard = Boolean(card.requiresLiveAccount && !showLiveAccount);
              const up = card.value >= 0;
              return (
                <article className="kpi-card" key={card.id}>
                  <p className="kpi-title">{card.title}</p>
                  <div className="kpi-value-row">
                    <h2 className={offlineCard ? "muted" : up ? "good" : "bad"}>
                      {offlineCard ? "--" : usd(card.value)}
                    </h2>
                    <span className={offlineCard ? "kpi-change muted" : card.change >= 0 ? "kpi-change good" : "kpi-change bad"}>
                      {offlineCard ? "OFFLINE" : signed(card.change, "%")}
                    </span>
                  </div>
                  <div className="kpi-meta">
                    {card.metrics.map((metric) => (
                      <span key={metric}>{metric}</span>
                    ))}
                  </div>
                </article>
              );
            })}
          </div>

          <article className="chart-card">
            <div className="chart-head">
              <h3>{config.main_content.components.chart_section.title}</h3>
              <div className="chart-tabs">
                {config.main_content.components.chart_section.chart_controls.timeframe_tabs.map((tab: string) => (
                  <button
                    type="button"
                    key={tab}
                    className={timeframe === tab ? "active" : ""}
                    onClick={() => {
                      setTimeframe(tab);
                      if (selectedSymbol) {
                        void loadSymbolIntoChart(selectedSymbol, tab);
                      }
                    }}
                  >
                    {tab}
                  </button>
                ))}
              </div>
            </div>

            <div className="chart-canvas">
              <div className="grid-lines" />
              <svg
                ref={chartSvgRef}
                className="pro-chart"
                viewBox={`0 0 ${chartModel.viewWidth} ${chartModel.viewHeight}`}
                preserveAspectRatio="none"
                role="img"
                aria-label="Live candlestick chart"
                onMouseMove={handleChartMouseMove}
                onMouseLeave={handleChartMouseLeave}
              >
                <defs>
                  <linearGradient id="volumeFade" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="var(--info-blue)" stopOpacity="0.28" />
                    <stop offset="100%" stopColor="var(--info-blue)" stopOpacity="0.04" />
                  </linearGradient>
                </defs>

                <text className="chart-watermark" x="22" y="44">
                  {`${selectedSymbol || "PAIR"} ${timeframe}`}
                </text>

                {chartModel.priceGrid.map((row, idx) => (
                  <g key={`pg-${idx}`}>
                    <line className="chart-grid-h" x1="0" x2={chartModel.viewWidth - chartModel.axisWidth} y1={row.y} y2={row.y} />
                    <text className="chart-price-label" x={chartModel.viewWidth - chartModel.axisWidth + 8} y={row.y + 4}>
                      {row.value.toFixed(5)}
                    </text>
                  </g>
                ))}

                {chartModel.timeGrid.map((row, idx) => (
                  <g key={`tg-${idx}`}>
                    <line className="chart-grid-v" x1={row.x} x2={row.x} y1="0" y2={chartModel.viewHeight - 30} />
                    <text className="chart-time-label" x={row.x} y={chartModel.viewHeight - 10}>
                      {row.label}
                    </text>
                  </g>
                ))}

                {chartModel.points.map((point) => (
                  <rect
                    key={`vol-${point.id}`}
                    className="chart-volume"
                    x={point.x - chartModel.bodyWidth / 2}
                    y={point.volumeTop}
                    width={chartModel.bodyWidth}
                    height={point.volumeHeight}
                  />
                ))}

                {chartModel.points.map((point) => (
                  <line
                    key={`wk-${point.id}`}
                    className={point.bullish ? "chart-wick bull" : "chart-wick bear"}
                    x1={point.x}
                    x2={point.x}
                    y1={point.highY}
                    y2={point.lowY}
                  />
                ))}

                {chartModel.points.map((point) => (
                  <rect
                    key={`bd-${point.id}`}
                    className={point.bullish ? "chart-body bull" : "chart-body bear"}
                    x={point.x - chartModel.bodyWidth / 2}
                    y={point.bodyTop}
                    width={chartModel.bodyWidth}
                    height={point.bodyHeight}
                    rx="1"
                  />
                ))}

                {chartModel.lastPriceY !== null ? (
                  <g>
                    <line
                      className={chartModel.lastDelta >= 0 ? "chart-last-line bull" : "chart-last-line bear"}
                      x1="0"
                      x2={chartModel.viewWidth - chartModel.axisWidth}
                      y1={chartModel.lastPriceY}
                      y2={chartModel.lastPriceY}
                    />
                    <rect
                      className={chartModel.lastDelta >= 0 ? "chart-last-box bull" : "chart-last-box bear"}
                      x={chartModel.viewWidth - chartModel.axisWidth + 4}
                      y={chartModel.lastPriceY - 10}
                      width={chartModel.axisWidth - 8}
                      height="20"
                      rx="4"
                    />
                    <text className="chart-last-label" x={chartModel.viewWidth - chartModel.axisWidth + chartModel.axisWidth / 2} y={chartModel.lastPriceY + 4}>
                      {chartModel.lastPrice?.toFixed(5) ?? "--"}
                    </text>
                  </g>
                ) : null}

                {hoveredPoint && chartHover ? (
                  <g>
                    <line
                      className="chart-cross-v"
                      x1={hoveredPoint.x}
                      x2={hoveredPoint.x}
                      y1={chartModel.plotTop}
                      y2={chartModel.plotBottom}
                    />
                    <line
                      className="chart-cross-h"
                      x1={chartModel.plotLeft}
                      x2={chartModel.plotRight}
                      y1={chartHover.y}
                      y2={chartHover.y}
                    />
                    <circle
                      className={hoveredPoint.bullish ? "chart-cross-dot bull" : "chart-cross-dot bear"}
                      cx={hoveredPoint.x}
                      cy={hoveredPoint.closeY}
                      r="3.2"
                    />
                    {hoverTooltip ? (
                      <g>
                        <rect className="chart-hover-box" x={hoverTooltip.x} y={hoverTooltip.y} width={hoverTooltip.width} height={hoverTooltip.height} rx="8" />
                        <text className="chart-hover-title" x={hoverTooltip.x + 10} y={hoverTooltip.y + 18}>
                          {`${selectedSymbol || "PAIR"} ${timeframe} ${formatTooltipTime(hoveredPoint.timestamp)}`}
                        </text>
                        <text className="chart-hover-line" x={hoverTooltip.x + 10} y={hoverTooltip.y + 40}>
                          {`Open  ${hoveredPoint.open.toFixed(5)}`}
                        </text>
                        <text className="chart-hover-line" x={hoverTooltip.x + 10} y={hoverTooltip.y + 58}>
                          {`High  ${hoveredPoint.high.toFixed(5)}`}
                        </text>
                        <text className="chart-hover-line" x={hoverTooltip.x + 10} y={hoverTooltip.y + 76}>
                          {`Low   ${hoveredPoint.low.toFixed(5)}`}
                        </text>
                        <text className="chart-hover-line" x={hoverTooltip.x + 10} y={hoverTooltip.y + 94}>
                          {`Close ${hoveredPoint.close.toFixed(5)}`}
                        </text>
                        <text className={hoveredPoint.bullish ? "chart-hover-delta bull" : "chart-hover-delta bear"} x={hoverTooltip.x + 116} y={hoverTooltip.y + 94}>
                          {`${(((hoveredPoint.close - hoveredPoint.open) / Math.max(hoverPipSize, 0.00001))).toFixed(1)} pips`}
                        </text>
                      </g>
                    ) : null}
                    {hoverPrice !== null ? (
                      <g>
                        <rect
                          className="chart-hover-price-box"
                          x={chartModel.viewWidth - chartModel.axisWidth + 4}
                          y={chartHover.y - 10}
                          width={chartModel.axisWidth - 8}
                          height="20"
                          rx="4"
                        />
                        <text className="chart-hover-price-label" x={chartModel.viewWidth - chartModel.axisWidth + chartModel.axisWidth / 2} y={chartHover.y + 4}>
                          {hoverPrice.toFixed(5)}
                        </text>
                      </g>
                    ) : null}
                  </g>
                ) : null}
              </svg>
              {chartModel.points.length === 0 ? <div className="chart-empty">No live bars yet. Connect MT5 and active pair feed.</div> : null}
              <div className="chart-float-info">
                <strong>{selectedSymbol || "N/A"} · {timeframe}</strong>
                <small>
                  bid {selectedTick?.bid?.toFixed(5) ?? "--"} | ask {selectedTick?.ask?.toFixed(5) ?? "--"} | spr {selectedTick?.spread_pips?.toFixed(2) ?? "--"}
                </small>
                <small>imbalance {(Number(orderBook.imbalance ?? 0)).toFixed(2)} · mode {syncMode}</small>
                <small>
                  O {latestBar?.open !== undefined ? Number(latestBar.open).toFixed(5) : "--"} ·
                  H {latestBar?.high !== undefined ? Number(latestBar.high).toFixed(5) : "--"} ·
                  L {latestBar?.low !== undefined ? Number(latestBar.low).toFixed(5) : "--"} ·
                  C {latestBar?.close !== undefined ? Number(latestBar.close).toFixed(5) : "--"}
                </small>
              </div>
            </div>

            <div className="trade-strip">
              {stripSymbols.map((symbol) => {
                const tick = ticksBySymbol[symbol];
                const pnl = pnlBySymbol[symbol] ?? 0;
                const active = activePairs.includes(symbol);
                return (
                  <button type="button" className="pair-pill" key={symbol} onClick={() => void loadSymbolIntoChart(symbol)}>
                    <strong>{symbol}</strong>
                    <span>{tick?.bid !== undefined ? `${tick.bid.toFixed(5)} / ${Number(tick.ask ?? 0).toFixed(5)}` : "-- / --"}</span>
                    <span className={pnl >= 0 ? "good" : "bad"}>{signed(pnl)}</span>
                    <span>{active ? "⚡" : "•"}</span>
                  </button>
                );
              })}
            </div>
          </article>
        </section>

        <aside className="right-panel">
          <article className="panel-card">
            <header>
              <h4>⏱️ TIMEFRAME SELECTION</h4>
            </header>
            <div className="stack">
              {config.right_panel.sections[0].components.timeframe_radios.options.map((option: LooseObject) => (
                <label key={option.value} className="radio-row">
                  <input
                    type="radio"
                    name="tf"
                    checked={timeframe === option.value}
                    onChange={() => {
                      setTimeframe(option.value);
                      if (selectedSymbol) {
                        void loadSymbolIntoChart(selectedSymbol, option.value);
                      }
                    }}
                  />
                  {option.label}
                </label>
              ))}
            </div>
            <div className="split-control">
              {config.right_panel.sections[0].components.chart_sync.options.map((option: LooseObject) => (
                <button
                  type="button"
                  key={option.value}
                  className={syncMode === option.value ? "active" : ""}
                  onClick={() => {
                    setSyncMode(option.value);
                    if (option.value === "manual") {
                      void refreshLiveFeeds();
                    }
                  }}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <button type="button" className="manual-refresh-btn" onClick={() => void refreshLiveFeeds()}>
              ↻ REFRESH NOW
            </button>
          </article>

          <article className="panel-card">
            <header>
              <h4>📊 PAIR MANAGEMENT</h4>
            </header>
            <div className="toolbar-row">
              <input
                type="search"
                value={pairQuery}
                onChange={(event) => setPairQuery(event.target.value)}
                placeholder={config.right_panel.sections[1].components.search.placeholder}
              />
              <select value={pairFilter} onChange={(event) => setPairFilter(event.target.value)}>
                {config.right_panel.sections[1].components.filter.options.map((option: LooseObject) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="between">
              <span>{availableRows.length} symbols visible</span>
              <button type="button" className="icon-action" onClick={() => void refreshMt5Pairs()} disabled={pairRefreshBusy}>
                {pairRefreshBusy ? "…" : "↻"}
              </button>
            </div>
            <div className="list-scroll">
              {availableRows.map((symbol) => {
                const tick = ticksBySymbol[symbol];
                const isActive = activePairs.includes(symbol);
                return (
                  <div className="pair-row" key={symbol}>
                    <div>
                      <strong>{symbol}</strong>
                      <small>
                        {tick?.bid !== undefined ? `${tick.bid.toFixed(5)} / ${Number(tick.ask ?? 0).toFixed(5)}` : "-- / --"}
                      </small>
                    </div>
                    <div>
                      <small className="muted">spr {tick?.spread_pips !== undefined ? tick.spread_pips.toFixed(2) : "--"}</small>
                      <small className={isActive ? "good" : "muted"}>{isActive ? "active" : "inactive"}</small>
                    </div>
                    <button type="button" className="icon-action" onClick={() => void loadSymbolIntoChart(symbol)} title="Load chart">
                      👁️
                    </button>
                    <button type="button" className="icon-action" onClick={() => void togglePair(symbol)}>
                      {isActive ? "❌" : "➕"}
                    </button>
                  </div>
                );
              })}
            </div>
          </article>

          <article className="panel-card">
            <header>
              <h4>✅ ACTIVE PAIRS</h4>
            </header>
            <p className="muted">{activePairs.length}/1 active</p>
            <div className="mini-progress">
              <span style={{ width: `${activePairs.length > 0 ? 100 : 0}%` }} />
            </div>
            <div className="stack">
              {activePairs.map((symbol) => (
                <div className="active-row" key={symbol}>
                  <div>
                    <strong>{symbol}</strong>
                    <small>{timeframe}</small>
                  </div>
                  <div className={(pnlBySymbol[symbol] ?? 0) >= 0 ? "good" : "bad"}>{signed(pnlBySymbol[symbol] ?? 0)}</div>
                </div>
              ))}
            </div>
          </article>

          <article className="panel-card">
            <header>
              <h4>🤖 BOT CONTROLS</h4>
            </header>
            <button
              type="button"
              className="btn btn-connect"
              onClick={() => void connectMt5()}
              disabled={connectBusy || disconnectBusy || rawConnection === "connected"}
            >
              {rawConnection === "connected" ? "✅ MT5 CONNECTED" : connectBusy ? "🔄 CONNECTING..." : "🔌 CONNECT MT5"}
            </button>
            <button
              type="button"
              className="btn btn-disconnect"
              onClick={() => void disconnectMt5()}
              disabled={disconnectBusy || connectBusy || rawConnection !== "connected"}
            >
              {disconnectBusy ? "⏳ DISCONNECTING..." : "🔌 DISCONNECT MT5"}
            </button>
            <button
              type="button"
              className="btn btn-start"
              onClick={() => void callTradingAction("/api/trading/start", {}, "Trading started. Analysis engine is live.")}
            >
              ▶️ START BOT
            </button>
            <div className="split-control">
              <button
                type="button"
                className="warning"
                onClick={() => void callTradingAction("/api/trading/pause", {}, "Trading paused.")}
              >
                ⏸️ PAUSE
              </button>
              <button
                type="button"
                className="warning"
                onClick={() => void callTradingAction("/api/trading/resume", {}, "Trading resumed.")}
              >
                ▶️ RESUME
              </button>
            </div>
            <div className="split-control">
              <button
                type="button"
                className="danger"
                onClick={() => void callTradingAction("/api/trading/stop", {}, "Trading stopped.")}
              >
                🛑 STOP
              </button>
              <button
                type="button"
                className="danger"
                onClick={() => void callTradingAction("/api/emergency/close-all", {}, "Close-all requested.")}
              >
                🧹 CLOSE ALL
              </button>
            </div>
            <button
              type="button"
              className="danger emergency-btn"
              onClick={() => void callTradingAction("/api/emergency/stop", { reason: "dashboard_emergency_stop" }, "Emergency stop fired.")}
            >
              🚨 EMERGENCY STOP
            </button>
            <div className="bot-state-row">
              <span className={botRunning && !botPaused ? "good" : "muted"}>running: {String(botRunning)}</span>
              <span className={botPaused ? "warning-text" : "muted"}>paused: {String(botPaused)}</span>
            </div>
            <div className="limit-wrap">
              <div className="between">
                <span>SAFETY BLOCK</span>
                <span>{safetyData.trading_blocked ? "YES" : "NO"}</span>
              </div>
              <div className="between">
                <span>Reason</span>
                <span>{safetyData.reason ?? statusData.safety?.reason ?? "ok"}</span>
              </div>
              <div className="between">
                <span>Startup Auto</span>
                <span>{startupAutoTrading ? "ON" : "OFF"}</span>
              </div>
            </div>
          </article>

          <article className="panel-card">
            <header>
              <h4>📝 EXECUTION LOG</h4>
            </header>
            <div className="list-scroll compact">
              {alerts
                .slice(-20)
                .reverse()
                .map((entry, idx) => (
                  <div className="log-row" key={`${entry.timestamp ?? "x"}-${idx}`}>
                    <small>{hhmmss(entry.timestamp)}</small>
                    <strong>{entry.event ?? "event"}</strong>
                    <small>{entry.level ?? "INFO"}</small>
                    <span className={(entry.level ?? "INFO").toUpperCase() === "ERROR" ? "bad" : "good"}>
                      {entry.message ?? "-"}
                    </span>
                    <span>{(entry.level ?? "INFO").toUpperCase() === "ERROR" ? "❌" : "✅"}</span>
                  </div>
                ))}
            </div>
          </article>

          <article className="panel-card">
            <header>
              <h4>💻 SYSTEM STATUS</h4>
            </header>
            <div className="stack">
              <div className="metric-row">
                <div className="between">
                  <span>CPU LOAD</span>
                  <span>{statusData.system?.load_1m ?? "--"}</span>
                </div>
              </div>
              <div className="metric-row">
                <div className="between">
                  <span>MEM</span>
                  <span>{statusData.system?.memory_mb !== undefined && statusData.system?.memory_mb !== null ? `${statusData.system.memory_mb} MB` : "--"}</span>
                </div>
              </div>
              <div className="metric-row">
                <div className="between">
                  <span>LATENCY</span>
                  <span>{latencyMs} ms</span>
                </div>
              </div>
              <div className="metric-row">
                <div className="between">
                  <span>UPTIME</span>
                  <span>{formatUptime(statusData.system?.uptime_seconds)}</span>
                </div>
              </div>
              <div className="metric-row">
                <div className="between">
                  <span>MODE</span>
                  <span>{statusData.system?.connection_mode ?? health.connection_mode ?? "--"}</span>
                </div>
              </div>
            </div>
          </article>
        </aside>
      </div>
    </main>
  );
}
