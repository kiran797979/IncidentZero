import React, { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { usePolling, MCPMessage } from "./hooks/usePolling";
import "./App.css";

const BACKEND_URL =
  process.env.REACT_APP_BACKEND_URL || "http://localhost:7071/api";

/* ═══════════════════════════════════════════════════════════
   7 SCENARIO METADATA — visuals, metrics, colors per type
   ═══════════════════════════════════════════════════════════ */

interface ScenarioMeta {
  label: string;
  shortLabel: string;
  emoji: string;
  color: string;
  gradient: string;
  description: string;
  severity: string;
  metrics: {
    primary: { key: string; label: string; unit: string; icon: string; warnThreshold: number; critThreshold: number };
    secondary: { key: string; label: string; unit: string; icon: string; warnThreshold: number; critThreshold: number };
    tertiary: { key: string; label: string; unit: string; icon: string; warnThreshold: number; critThreshold: number };
  };
}

const SCENARIO_META: Record<string, ScenarioMeta> = {
  connection_pool_exhaustion: {
    label: "Connection Pool Exhaustion",
    shortLabel: "Pool Leak",
    emoji: "🔌",
    color: "#f97316",
    gradient: "linear-gradient(135deg, #f97316, #ea580c)",
    description: "Database connections leaking — pool nearing exhaustion",
    severity: "P1",
    metrics: {
      primary:   { key: "active_connections",     label: "CONNECTIONS",  unit: "",   icon: "◈", warnThreshold: 50, critThreshold: 80 },
      secondary: { key: "connection_utilization",  label: "POOL UTIL",   unit: "%",  icon: "◉", warnThreshold: 60, critThreshold: 85 },
      tertiary:  { key: "avg_response_time_ms",    label: "RESPONSE",    unit: "ms", icon: "◇", warnThreshold: 200, critThreshold: 500 },
    },
  },
  memory_leak: {
    label: "Memory Leak",
    shortLabel: "Mem Leak",
    emoji: "🧠",
    color: "#a855f7",
    gradient: "linear-gradient(135deg, #a855f7, #7c3aed)",
    description: "Unbounded cache growth consuming heap memory",
    severity: "P1",
    metrics: {
      primary:   { key: "memory_usage_pct",  label: "HEAP USAGE",    unit: "%",  icon: "◈", warnThreshold: 70, critThreshold: 90 },
      secondary: { key: "gc_pause_ms",       label: "GC PAUSE",      unit: "ms", icon: "◉", warnThreshold: 100, critThreshold: 500 },
      tertiary:  { key: "cache_entries",      label: "CACHE SIZE",    unit: "",   icon: "◇", warnThreshold: 500000, critThreshold: 2000000 },
    },
  },
  slow_database_queries: {
    label: "Slow Database Queries",
    shortLabel: "Slow Query",
    emoji: "🐌",
    color: "#eab308",
    gradient: "linear-gradient(135deg, #eab308, #ca8a04)",
    description: "Missing index — full table scans on 2.4M rows",
    severity: "P2",
    metrics: {
      primary:   { key: "query_time_ms",  label: "QUERY TIME",    unit: "ms", icon: "◈", warnThreshold: 1000, critThreshold: 4000 },
      secondary: { key: "db_cpu_pct",     label: "DB CPU",        unit: "%",  icon: "◉", warnThreshold: 60, critThreshold: 90 },
      tertiary:  { key: "rows_scanned",   label: "ROWS SCANNED",  unit: "",   icon: "◇", warnThreshold: 100000, critThreshold: 1000000 },
    },
  },
  external_api_failure: {
    label: "External API Failure",
    shortLabel: "API Down",
    emoji: "🌐",
    color: "#ef4444",
    gradient: "linear-gradient(135deg, #ef4444, #dc2626)",
    description: "Upstream payment gateway returning 503 errors",
    severity: "P1",
    metrics: {
      primary:   { key: "timeout_count",      label: "TIMEOUTS",      unit: "",  icon: "◈", warnThreshold: 10, critThreshold: 50 },
      secondary: { key: "retry_queue_depth",   label: "RETRY QUEUE",   unit: "",  icon: "◉", warnThreshold: 200, critThreshold: 800 },
      tertiary:  { key: "avg_response_time_ms", label: "RESPONSE",     unit: "ms", icon: "◇", warnThreshold: 2000, critThreshold: 7000 },
    },
  },
  cache_failure: {
    label: "Cache Failure (Redis Down)",
    shortLabel: "Cache Down",
    emoji: "💾",
    color: "#06b6d4",
    gradient: "linear-gradient(135deg, #06b6d4, #0891b2)",
    description: "Redis offline — thundering herd hitting database",
    severity: "P1",
    metrics: {
      primary:   { key: "cache_hit_rate",  label: "CACHE HIT",  unit: "%",  icon: "◈", warnThreshold: 50, critThreshold: 10 },
      secondary: { key: "db_load_pct",     label: "DB LOAD",    unit: "%",  icon: "◉", warnThreshold: 60, critThreshold: 90 },
      tertiary:  { key: "db_fallback_count", label: "FALLBACKS",  unit: "",   icon: "◇", warnThreshold: 100, critThreshold: 500 },
    },
  },
  cpu_spike_thread_deadlock: {
    label: "CPU Spike / Thread Deadlock",
    shortLabel: "Deadlock",
    emoji: "🔒",
    color: "#f43f5e",
    gradient: "linear-gradient(135deg, #f43f5e, #e11d48)",
    description: "Lock ordering inversion — 187 threads deadlocked",
    severity: "P0",
    metrics: {
      primary:   { key: "cpu_pct",           label: "CPU",             unit: "%",   icon: "◈", warnThreshold: 70, critThreshold: 95 },
      secondary: { key: "blocked_threads",   label: "BLOCKED",         unit: "",    icon: "◉", warnThreshold: 50, critThreshold: 150 },
      tertiary:  { key: "throughput_rps",    label: "THROUGHPUT",      unit: "RPS", icon: "◇", warnThreshold: 500, critThreshold: 50 },
    },
  },
  disk_io_saturation: {
    label: "Disk I/O Saturation",
    shortLabel: "Disk I/O",
    emoji: "💽",
    color: "#10b981",
    gradient: "linear-gradient(135deg, #10b981, #059669)",
    description: "Sync log flushing saturating disk and blocking event loop",
    severity: "P1",
    metrics: {
      primary:   { key: "disk_util_pct",       label: "DISK UTIL",   unit: "%",  icon: "◈", warnThreshold: 60, critThreshold: 90 },
      secondary: { key: "iowait_pct",          label: "IO WAIT",     unit: "%",  icon: "◉", warnThreshold: 20, critThreshold: 60 },
      tertiary:  { key: "log_queue_depth",     label: "LOG QUEUE",   unit: "",   icon: "◇", warnThreshold: 10000, critThreshold: 40000 },
    },
  },
};

const DEFAULT_SCENARIO = SCENARIO_META.connection_pool_exhaustion;

/* ═══════════════════════════════════════════════════════════
   Agent & Pipeline Constants
   ═══════════════════════════════════════════════════════════ */

const AGENT_STAGES = [
  { key: "DETECTED",   icon: "W", label: "DETECT",   agent: "Watcher",    color: "#00d2ff", emoji: "👁" },
  { key: "TRIAGING",   icon: "T", label: "TRIAGE",   agent: "Triage",     color: "#ffd700", emoji: "🔺" },
  { key: "DIAGNOSING", icon: "D", label: "DIAGNOSE", agent: "Diagnosis",  color: "#00ff88", emoji: "🔍" },
  { key: "DEBATING",   icon: "R", label: "DEBATE",   agent: "Resolution", color: "#ff6b6b", emoji: "⚡" },
  { key: "DEPLOYING",  icon: "X", label: "DEPLOY",   agent: "Deploy",     color: "#a855f7", emoji: "🚀" },
  { key: "COMPLETE",   icon: "P", label: "REPORT",   agent: "Postmortem", color: "#06b6d4", emoji: "📋" },
];

const STAGE_ORDER = [
  "MONITORING", "DETECTED", "TRIAGING", "DIAGNOSING",
  "DEBATING", "DEPLOYING", "RESOLVED", "COMPLETE",
];

const AGENT_COLORS: Record<string, string> = {
  WatcherAgent:       "#00d2ff",
  TriageAgent:        "#ffd700",
  DiagnosisAgent:     "#00ff88",
  ResolutionAgent:    "#ff6b6b",
  DeployAgent:        "#a855f7",
  PostmortemAgent:    "#06b6d4",
  OrchestratorAgent:  "#e94560",
};

const SCENARIO_LIST = [
  { type: "connection_pool_exhaustion", label: "Connection Pool Leak",     emoji: "🔌", severity: "P1" },
  { type: "memory_leak",               label: "Memory Leak",              emoji: "🧠", severity: "P1" },
  { type: "slow_database_queries",     label: "Slow Database Queries",    emoji: "🐌", severity: "P2" },
  { type: "external_api_failure",      label: "External API Failure",     emoji: "🌐", severity: "P1" },
  { type: "cache_failure",             label: "Cache Failure (Redis)",     emoji: "💾", severity: "P1" },
  { type: "cpu_spike_thread_deadlock", label: "Thread Deadlock",          emoji: "🔒", severity: "P0" },
  { type: "disk_io_saturation",        label: "Disk I/O Saturation",      emoji: "💽", severity: "P1" },
];

/* ═══════════════════════════════════════════════════════════
   Sparkline Component
   ═══════════════════════════════════════════════════════════ */

function Sparkline({
  values,
  color,
  width = 80,
  height = 24,
}: {
  values: number[];
  color: string;
  width?: number;
  height?: number;
}) {
  if (values.length < 2) return null;

  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min === 0 ? 1 : max - min;

  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width;
      const y = height - ((v - min) / range) * height;
      return `${x},${y}`;
    })
    .join(" ");

  const fillPoints = `0,${height} ${points} ${width},${height}`;
  const gradientId = `sg-${color.replace("#", "")}`;

  return (
    <svg width={width} height={height} className="iz-sparkline" viewBox={`0 0 ${width} ${height}`}>
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polygon points={fillPoints} fill={`url(#${gradientId})`} />
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      {values.length > 0 && (
        <circle
          cx={width}
          cy={height - ((values[values.length - 1] - min) / range) * height}
          r="2.5"
          fill={color}
          className="iz-sparkline-dot"
        />
      )}
    </svg>
  );
}

/* ═══════════════════════════════════════════════════════════
   Severity Badge Component
   ═══════════════════════════════════════════════════════════ */

function SeverityBadge({ severity }: { severity: string }) {
  const s = (severity || "").toUpperCase();
  const colorMap: Record<string, { bg: string; fg: string; border: string }> = {
    P0: { bg: "#7f1d1d", fg: "#fca5a5", border: "#ef4444" },
    P1: { bg: "#78350f", fg: "#fcd34d", border: "#f59e0b" },
    P2: { bg: "#1e3a5f", fg: "#93c5fd", border: "#3b82f6" },
    P3: { bg: "#1a2e1a", fg: "#86efac", border: "#22c55e" },
  };
  const c = colorMap[s] || colorMap.P1;
  return (
    <span
      className="iz-severity-badge"
      style={{ background: c.bg, color: c.fg, border: `1px solid ${c.border}` }}
    >
      {s}
    </span>
  );
}

/* ═══════════════════════════════════════════════════════════
   Helper Functions
   ═══════════════════════════════════════════════════════════ */

function safeStr(val: any, fallback: string = "—"): string {
  if (val === null || val === undefined || val === "") return fallback;
  return String(val);
}

function safeNum(val: any, fallback: number = 0): number {
  if (val === null || val === undefined || val === "") return fallback;
  const n = Number(val);
  return isNaN(n) ? fallback : n;
}

/** Deep search messages for a metric value (searches from newest to oldest). */
function extractMetric(messages: MCPMessage[], key: string): number | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const p = messages[i].payload;
    if (!p) continue;

    // Direct payload key
    if (key in p && p[key] !== null && p[key] !== undefined) return safeNum(p[key]);

    // Nested in scenario_symptoms
    if (p.scenario_symptoms && key in p.scenario_symptoms)
      return safeNum(p.scenario_symptoms[key]);

    // Nested in data
    if (p.data && key in p.data) return safeNum(p.data[key]);

    // Nested in metrics
    if (p.metrics && key in p.metrics) return safeNum(p.metrics[key]);

    // Nested in components (from target /metrics)
    const components = ["connection_pool", "memory", "database", "external_api", "cache", "threads", "disk_io"];
    for (const comp of components) {
      if (p[comp] && typeof p[comp] === "object" && key in p[comp])
        return safeNum(p[comp][key]);
    }
  }
  return null;
}

/** Detect incident type from messages. */
function detectIncidentType(messages: MCPMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const p = messages[i].payload;
    if (p?.incident_type && typeof p.incident_type === "string") return p.incident_type;
    if (p?.scenario && typeof p.scenario === "string") return p.scenario;
    if (p?.data?.incident_type) return p.data.incident_type;
  }
  return "";
}

/* ═══════════════════════════════════════════════════════════
   Main App Component
   ═══════════════════════════════════════════════════════════ */

function App() {
  const { messages, connected, clearMessages } = usePolling(BACKEND_URL, 2000);

  // ─── Core State ─────────────────────────────────────────
  const [injecting, setInjecting] = useState(false);
  const [bugActive, setBugActive] = useState(false);
  const [elapsedTime, setElapsedTime] = useState(0);
  const [incidentStartTime, setIncidentStartTime] = useState<number | null>(null);
  const [showPostmortem, setShowPostmortem] = useState(false);
  const [activeTab, setActiveTab] = useState<"timeline" | "debate" | "postmortem">("timeline");
  const [splashPhase, setSplashPhase] = useState<"launching" | "fading" | "done">("launching");
  const [showFlash, setShowFlash] = useState(false);
  const [showCelebration, setShowCelebration] = useState(false);

  // ─── Scenario State ─────────────────────────────────────
  const [incidentType, setIncidentType] = useState<string>("");
  const [showScenarioSelector, setShowScenarioSelector] = useState(false);

  // ─── Metric History ─────────────────────────────────────
  const [errorHistory, setErrorHistory] = useState<number[]>([0]);
  const [primaryHistory, setPrimaryHistory] = useState<number[]>([0]);
  const [secondaryHistory, setSecondaryHistory] = useState<number[]>([0]);
  const [msgHistory, setMsgHistory] = useState<number[]>([0]);

  const timelineRef = useRef<HTMLDivElement>(null);
  const prevIncidentCount = useRef(0);
  const selectorRef = useRef<HTMLDivElement>(null);

  // ─── Derived: Scenario Metadata ─────────────────────────
  const scenarioMeta = useMemo(
    () => SCENARIO_META[incidentType] || DEFAULT_SCENARIO,
    [incidentType],
  );

  // ─── Detect incident type from messages ─────────────────
  useEffect(() => {
    const detected = detectIncidentType(messages);
    if (detected && detected !== incidentType) {
      setIncidentType(detected);
    }
  }, [messages, incidentType]);

  // ─── Splash & Timers ────────────────────────────────────
  useEffect(() => {
    const t1 = setTimeout(() => setSplashPhase("fading"), 3400);
    const t2 = setTimeout(() => setSplashPhase("done"), 4200);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, []);

  useEffect(() => {
    if (!incidentStartTime) return;
    const interval = setInterval(() => {
      setElapsedTime(Math.floor((Date.now() - incidentStartTime) / 1000));
    }, 100);
    return () => clearInterval(interval);
  }, [incidentStartTime]);

  useEffect(() => {
    if (timelineRef.current) timelineRef.current.scrollTop = 0;
  }, [messages]);

  // Close scenario selector on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (selectorRef.current && !selectorRef.current.contains(e.target as Node)) {
        setShowScenarioSelector(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // ─── Derived Data ───────────────────────────────────────
  const latestStatus = useMemo(() => {
    const s = messages.filter((m) => m.channel === "monitoring.status");
    return s.length > 0 ? s[s.length - 1].payload : null;
  }, [messages]);

  const incidentMessages = useMemo(
    () => messages.filter((m) => m.channel !== "monitoring.status" && m.channel !== "system.status"),
    [messages],
  );

  const debateMessages = useMemo(
    () => messages.filter((m) => m.channel === "incident.debate"),
    [messages],
  );

  const postmortem = useMemo(() => {
    const pm = messages.filter((m) => m.channel === "incident.postmortem");
    return pm.length > 0 ? pm[pm.length - 1].payload : null;
  }, [messages]);

  const currentIncident = useMemo(() => {
    const inc = messages.filter((m) => m.channel === "incident.detection");
    return inc.length > 0 ? inc[inc.length - 1] : null;
  }, [messages]);

  const triageData = useMemo(() => {
    const t = messages.filter((m) => m.channel === "incident.triage");
    return t.length > 0 ? t[t.length - 1].payload : null;
  }, [messages]);

  const incidentStatus = useMemo(() => {
    const has = (ch: string) => messages.some((m) => m.channel === ch);
    if (has("incident.postmortem")) return "COMPLETE";
    if (has("incident.deployment")) return "RESOLVED";
    if (has("incident.resolution")) return "DEPLOYING";
    if (has("incident.debate")) return "DEBATING";
    if (has("incident.diagnosis")) return "DIAGNOSING";
    if (has("incident.triage")) return "TRIAGING";
    if (currentIncident) return "DETECTED";
    return "MONITORING";
  }, [messages, currentIncident]);

  const activeAgents = useMemo(() => {
    const agents = new Set<string>();
    incidentMessages.forEach((m) => agents.add(m.sender));
    return agents;
  }, [incidentMessages]);

  const currentStageIndex = STAGE_ORDER.indexOf(incidentStatus);

  // ─── Metrics: Scenario-Aware ────────────────────────────
  const errorRate = safeNum(latestStatus?.error_rate, 0) * 100;

  const primaryVal = useMemo(() => {
    const fromStatus = latestStatus ? safeNum(latestStatus[scenarioMeta.metrics.primary.key]) : null;
    if (fromStatus !== null && fromStatus !== 0) return fromStatus;
    const fromAll = extractMetric(messages, scenarioMeta.metrics.primary.key);
    return fromAll !== null ? fromAll : 0;
  }, [latestStatus, messages, scenarioMeta]);

  const secondaryVal = useMemo(() => {
    const fromStatus = latestStatus ? safeNum(latestStatus[scenarioMeta.metrics.secondary.key]) : null;
    if (fromStatus !== null && fromStatus !== 0) return fromStatus;
    const fromAll = extractMetric(messages, scenarioMeta.metrics.secondary.key);
    return fromAll !== null ? fromAll : 0;
  }, [latestStatus, messages, scenarioMeta]);

  // Normalize: if a value <= 1 and unit is %, multiply by 100
  const normalizePct = (val: number, unit: string) => {
    if (unit === "%" && val > 0 && val <= 1) return val * 100;
    return val;
  };

  const primaryDisplay = normalizePct(primaryVal, scenarioMeta.metrics.primary.unit);
  const secondaryDisplay = normalizePct(secondaryVal, scenarioMeta.metrics.secondary.unit);

  // ─── Update metric histories ────────────────────────────
  useEffect(() => {
    setErrorHistory((h) => [...h.slice(-19), errorRate]);
    setPrimaryHistory((h) => [...h.slice(-19), primaryDisplay]);
    setSecondaryHistory((h) => [...h.slice(-19), secondaryDisplay]);
    setMsgHistory((h) => [...h.slice(-19), incidentMessages.length]);
  }, [errorRate, primaryDisplay, secondaryDisplay, incidentMessages.length]);

  // ─── Triggers & Auto-Switches ───────────────────────────
  useEffect(() => {
    const incidents = messages.filter((m) => m.channel === "incident.detection");
    if (incidents.length > prevIncidentCount.current) {
      setShowFlash(true);
      setTimeout(() => setShowFlash(false), 1500);
    }
    prevIncidentCount.current = incidents.length;
  }, [messages]);

  useEffect(() => {
    if (incidentStatus === "COMPLETE") {
      setShowCelebration(true);
      setTimeout(() => setShowCelebration(false), 5000);
    }
    if (incidentStatus === "COMPLETE" || incidentStatus === "RESOLVED") {
      setBugActive(false);
    }
  }, [incidentStatus]);

  useEffect(() => {
    if (debateMessages.length > 0 && activeTab === "timeline") setActiveTab("debate");
    if (postmortem) setShowPostmortem(true);
  }, [debateMessages.length, postmortem, activeTab]);

  // ─── Actions ────────────────────────────────────────────
  const injectBug = useCallback(
    async (specificScenario?: string) => {
      if (injecting || bugActive) return;

      setInjecting(true);
      setBugActive(true);
      setIncidentStartTime(Date.now());
      setShowScenarioSelector(false);
      setActiveTab("timeline");
      clearMessages();

      // Reset histories
      setErrorHistory([0]);
      setPrimaryHistory([0]);
      setSecondaryHistory([0]);
      setMsgHistory([0]);

      // Pre-set the type if user picked one
      if (specificScenario) {
        setIncidentType(specificScenario);
      } else {
        setIncidentType("");
      }

      try {
        const body = specificScenario
          ? JSON.stringify({ scenario: specificScenario })
          : undefined;

        const resp = await fetch(BACKEND_URL + "/run-incident", {
          method: "POST",
          headers: specificScenario
            ? { "Content-Type": "application/json" }
            : undefined,
          body,
        });

        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          console.error("Failed to trigger incident:", data.error || resp.statusText);
        } else {
          const data = await resp.json();
          console.log("Incident triggered:", data);
        }
      } catch (e) {
        console.error("Error triggering incident:", e);
      }

      setTimeout(() => setInjecting(false), 5000);
    },
    [injecting, bugActive, clearMessages],
  );

  const resetDashboard = useCallback(() => {
    clearMessages();
    setBugActive(false);
    setInjecting(false);
    setIncidentStartTime(null);
    setElapsedTime(0);
    setIncidentType("");
    setActiveTab("timeline");
    setShowPostmortem(false);
    setShowCelebration(false);
    setErrorHistory([0]);
    setPrimaryHistory([0]);
    setSecondaryHistory([0]);
    setMsgHistory([0]);

    fetch(BACKEND_URL + "/reset", { method: "POST" }).catch(() => {});
  }, [clearMessages]);

  // ─── Helpers ────────────────────────────────────────────
  const formatTime = (ts: string) => {
    try {
      const d = new Date(ts);
      if (isNaN(d.getTime())) return ts;
      return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch { return ts; }
  };

  const formatElapsed = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return (m > 0 ? `${m}m ` : "") + `${sec}s`;
  };

  const getMessageSummary = (msg: MCPMessage): string => {
    const p = msg.payload || {};

    switch (msg.channel) {
      case "incident.detection": {
        const er = safeNum(p?.data?.error_rate, 0);
        const type = p?.incident_type || incidentType;
        const meta = SCENARIO_META[type];
        const prefix = meta ? `${meta.emoji} ${meta.shortLabel}` : "INCIDENT";
        return `${prefix} DETECTED — Error rate: ${(er * 100).toFixed(1)}%`;
      }
      case "incident.orchestration":
        return safeStr(p?.action, "Action triggered").replace(/_/g, " ");
      case "incident.triage":
        return `Severity: ${safeStr(p?.severity, "?")} — ${safeStr(p?.classification, "?")} — Blast: ${safeStr(p?.blast_radius_pct, "?")}%`;
      case "incident.diagnosis": {
        const rc = p?.root_cause;
        if (rc && typeof rc === "object") {
          return `Root cause: ${safeStr(rc.detail || rc.mechanism, "identified")}`;
        }
        return "Root cause analysis complete";
      }
      case "incident.debate": {
        const ev = p?.evaluation;
        if (msg.message_type === "challenge") {
          return `⚡ CHALLENGE: ${safeStr(ev?.reasoning || p?.challenge_reason, "Challenging diagnosis...").slice(0, 200)}`;
        }
        if (msg.message_type === "consensus") {
          return `🤝 CONSENSUS: ${safeStr(ev?.reasoning, "Agents aligned").slice(0, 200)}`;
        }
        if (msg.message_type === "evidence" || p?.response_type) {
          return `📊 ${safeStr(p?.response_type, "EVIDENCE")}: ${safeStr(p?.response, "Evidence presented").slice(0, 200)}`;
        }
        return `${safeStr(msg.message_type, "").toUpperCase()}: ${JSON.stringify(p).slice(0, 150)}`;
      }
      case "incident.resolution":
        return `Fix: ${safeStr(p?.fix?.description, "Code fix ready")} (Risk: ${safeStr(p?.fix?.risk_level, "?")})`;
      case "incident.deployment": {
        const st = safeStr(p?.status, "?");
        const hc = safeStr(p?.health_check, "?");
        const hasPr = p?.pr_url?.startsWith?.("http");
        return `Deploy: ${st} — Health: ${hc}${hasPr ? " — PR created" : ""}`;
      }
      case "incident.postmortem":
        return `📋 Postmortem generated (${safeStr(p?.total_messages, "?")} messages, ${safeStr(p?.debate_rounds, "?")} debate rounds)`;
      case "incident.stage":
        return `Stage → ${safeStr(p?.stage, "?")}`;
      default:
        return JSON.stringify(p).slice(0, 120);
    }
  };

  const getAgentInitial = (sender: string): string => {
    const map: Record<string, string> = {
      WatcherAgent: "W", TriageAgent: "T", DiagnosisAgent: "D",
      ResolutionAgent: "R", DeployAgent: "X", PostmortemAgent: "P", OrchestratorAgent: "O",
    };
    return map[sender] || "?";
  };

  const getStatusColor = () => {
    if (incidentStatus === "MONITORING" || incidentStatus === "COMPLETE") return "#00ff88";
    if (incidentStatus === "RESOLVED") return "#00d2ff";
    return scenarioMeta.color || "#ff4757";
  };

  const getStatusEmoji = () => {
    const map: Record<string, string> = {
      MONITORING: "🟢", DETECTED: "🔴", TRIAGING: "🟡", DIAGNOSING: "🔍",
      DEBATING: "⚔️", DEPLOYING: "🚀", RESOLVED: "✅", COMPLETE: "🏆",
    };
    return map[incidentStatus] || "⬡";
  };

  // Simple markdown renderer
  const renderMarkdownLine = (line: string, i: number) => {
    if (line.startsWith("# ")) return <h1 key={i}>{line.slice(2)}</h1>;
    if (line.startsWith("## ")) return <h2 key={i}>{line.slice(3)}</h2>;
    if (line.startsWith("### ")) return <h3 key={i}>{line.slice(4)}</h3>;
    if (line.startsWith("- ") || line.startsWith("* ")) return <li key={i}>{line.slice(2)}</li>;
    if (line.startsWith("|")) {
      const cells = line.split("|").filter((c) => c.trim());
      if (cells.every((c) => c.trim().match(/^[-:]+$/))) return null;
      return (
        <div key={i} className="iz-pm-row">
          {cells.map((c, j) => <span key={j} className="iz-pm-cell">{c.trim()}</span>)}
        </div>
      );
    }
    if (line.startsWith("---")) return <hr key={i} />;
    if (!line.trim()) return <br key={i} />;
    return <p key={i}>{line}</p>;
  };

  // ─── Compute metric colors based on scenario thresholds ──
  const getMetricColor = (
    val: number,
    cfg: ScenarioMeta["metrics"]["primary"],
    isInverse: boolean = false,
  ): string => {
    if (isInverse) {
      // Lower = worse (e.g., cache hit rate, throughput)
      if (val < cfg.critThreshold) return "#ff4757";
      if (val < cfg.warnThreshold) return "#ffa502";
      return "#00ff88";
    }
    // Higher = worse (default)
    if (val > cfg.critThreshold) return "#ff4757";
    if (val > cfg.warnThreshold) return "#ffa502";
    return "#00ff88";
  };

  const isInverseMetric = (key: string) =>
    key === "cache_hit_rate" || key === "throughput_rps";

  // ═══════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════

  return (
    <div className="iz">
      {/* ═══════════════════════════════════════════════════
          ROCKET SPLASH SCREEN
          ═══════════════════════════════════════════════════ */}
      {splashPhase !== "done" && (
        <div className={`splash ${splashPhase === "fading" ? "splash-exit" : ""}`}>
          <div className="splash-stars">
            {Array.from({ length: 60 }).map((_, i) => (
              <div key={i} className="splash-star" style={{
                left: `${Math.random() * 100}%`, top: `${Math.random() * 100}%`,
                width: `${Math.random() * 2.5 + 1}px`, height: `${Math.random() * 2.5 + 1}px`,
                animationDelay: `${Math.random() * 3}s`, animationDuration: `${1.5 + Math.random() * 2}s`,
              }} />
            ))}
          </div>
          <div className="splash-speed-lines">
            {Array.from({ length: 18 }).map((_, i) => (
              <div key={i} className="splash-speed-line" style={{
                left: `${10 + Math.random() * 80}%`,
                animationDelay: `${1.2 + Math.random() * 1.5}s`,
                animationDuration: `${0.4 + Math.random() * 0.5}s`,
                opacity: 0.3 + Math.random() * 0.5,
              }} />
            ))}
          </div>
          <div className="splash-rocket-wrap">
            <div className="splash-rocket">
              <div className="splash-nose" />
              <div className="splash-body"><div className="splash-window" /><div className="splash-stripe" /></div>
              <div className="splash-fin splash-fin-l" />
              <div className="splash-fin splash-fin-r" />
              <div className="splash-exhaust">
                <div className="splash-flame splash-flame-1" />
                <div className="splash-flame splash-flame-2" />
                <div className="splash-flame splash-flame-3" />
              </div>
              <div className="splash-particles">
                {Array.from({ length: 20 }).map((_, i) => (
                  <div key={i} className="splash-particle" style={{
                    left: `${-10 + Math.random() * 20}px`,
                    animationDelay: `${Math.random() * 0.8}s`,
                    animationDuration: `${0.5 + Math.random() * 0.8}s`,
                    width: `${2 + Math.random() * 4}px`, height: `${2 + Math.random() * 4}px`,
                  }} />
                ))}
              </div>
              <div className="splash-smoke-wrap">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i} className="splash-smoke" style={{ animationDelay: `${i * 0.15}s`, left: `${-8 + Math.random() * 16}px` }} />
                ))}
              </div>
            </div>
          </div>
          <div className="splash-brand">
            <div className="splash-brand-name">
              <span className="splash-inc">INCIDENT</span>
              <span className="splash-zero">ZERO</span>
            </div>
            <div className="splash-tagline">AUTONOMOUS AI SRE TEAM</div>
            <div className="splash-loading">
              <div className="splash-loading-track"><div className="splash-loading-fill" /></div>
              <span className="splash-loading-text">INITIALIZING AGENTS</span>
            </div>
          </div>
          <div className="splash-ground-glow" />
        </div>
      )}

      {/* Flash & Celebration */}
      {showFlash && <div className="iz-flash" />}
      {showCelebration && (
        <div className="iz-celebrate">
          <div className="iz-celebrate-particles">
            {Array.from({ length: 40 }).map((_, i) => (
              <div key={i} className="iz-confetti" style={{
                left: `${Math.random() * 100}%`,
                animationDelay: `${Math.random() * 0.5}s`,
                animationDuration: `${2 + Math.random() * 2}s`,
                background: ["#00ff88", "#00d2ff", "#a855f7", "#ffd700", "#e94560", "#06b6d4"][i % 6],
                width: `${4 + Math.random() * 6}px`,
                height: `${4 + Math.random() * 6}px`,
                borderRadius: Math.random() > 0.5 ? "50%" : "2px",
              }} />
            ))}
          </div>
          <div className="iz-celebrate-banner">
            <span className="iz-celebrate-emoji">🏆</span>
            <div className="iz-celebrate-text">
              <div className="iz-celebrate-title">INCIDENT RESOLVED</div>
              <div className="iz-celebrate-sub">
                {scenarioMeta.emoji} {scenarioMeta.shortLabel} — Autonomous fix in {formatElapsed(elapsedTime)}
              </div>
            </div>
            <span className="iz-celebrate-emoji">🏆</span>
          </div>
        </div>
      )}

      {/* Background */}
      <div className="iz-bg">
        <div className="iz-bg-grid" />
        <div className="iz-bg-glow iz-bg-glow-1" style={incidentType ? { background: `${scenarioMeta.color}08` } : undefined} />
        <div className="iz-bg-glow iz-bg-glow-2" />
        <div className="iz-bg-glow iz-bg-glow-3" />
        <div className="iz-bg-scan" />
      </div>

      {/* ═══════════════════════════════════════════════════
          HEADER
          ═══════════════════════════════════════════════════ */}
      <header className="iz-header">
        <div className="iz-header-brand">
          <div className="iz-logo">
            <div className="iz-logo-ring"><div className="iz-logo-ring-inner" /></div>
            <span className="iz-logo-icon">⬡</span>
          </div>
          <div className="iz-brand-text">
            <div className="iz-brand-name">
              <span className="iz-brand-incident">INCIDENT</span>
              <span className="iz-brand-zero">ZERO</span>
            </div>
            <div className="iz-brand-sub">AUTONOMOUS AI SRE TEAM</div>
          </div>
        </div>

        {bugActive && incidentStartTime && (
          <div className="iz-timer">
            <div className="iz-timer-label">MTTR</div>
            <div className="iz-timer-value">{formatElapsed(elapsedTime)}</div>
            <div className="iz-timer-track">
              <div className="iz-timer-fill" style={{
                width: `${Math.min(100, (elapsedTime / 120) * 100)}%`,
                background: scenarioMeta.color,
              }} />
            </div>
          </div>
        )}

        <div className="iz-header-controls">
          <div className="iz-badges">
            {/* Status Badge */}
            <div className="iz-badge" style={{ borderColor: `${getStatusColor()}44`, color: getStatusColor() }}>
              <span className="iz-badge-dot" style={{ background: getStatusColor(), boxShadow: `0 0 8px ${getStatusColor()}` }} />
              <span className="iz-badge-emoji">{getStatusEmoji()}</span>
              {incidentStatus}
            </div>

            {/* Severity Badge (from triage) */}
            {triageData?.severity && (
              <SeverityBadge severity={triageData.severity} />
            )}

            {/* Scenario Badge */}
            {incidentType && bugActive && (
              <div className="iz-badge" style={{ borderColor: `${scenarioMeta.color}44`, color: scenarioMeta.color }}>
                <span className="iz-badge-emoji">{scenarioMeta.emoji}</span>
                {scenarioMeta.shortLabel}
              </div>
            )}

            {/* Connection Badge */}
            <div className={`iz-badge ${connected ? "iz-badge-live" : "iz-badge-off"}`}>
              <span className={`iz-badge-dot ${connected ? "iz-dot-live" : "iz-dot-off"}`} />
              {connected ? "LIVE" : "OFFLINE"}
            </div>
          </div>

          {/* ── Inject Button with Dropdown ── */}
          <div className="iz-inject-group" ref={selectorRef}>
            <button
              className={`iz-inject ${bugActive ? "iz-inject-active" : injecting ? "iz-inject-loading" : "iz-inject-ready"}`}
              onClick={() => injectBug()}
              disabled={injecting || bugActive}
              aria-label={injecting ? "Injecting..." : bugActive ? "Agents active" : "Inject a random failure"}
            >
              <span className="iz-inject-glow" />
              <span className="iz-inject-text">
                {injecting ? "⏳ INJECTING..." : bugActive ? `${scenarioMeta.emoji} AGENTS ACTIVE` : "⚡ INJECT FAILURE"}
              </span>
            </button>

            {/* Dropdown toggle */}
            {!bugActive && !injecting && (
              <button
                className="iz-inject-dropdown"
                onClick={() => setShowScenarioSelector(!showScenarioSelector)}
                title="Choose specific failure scenario"
              >
                <span style={{ fontSize: 10 }}>▼</span>
              </button>
            )}

            {/* Scenario Selector Dropdown */}
            {showScenarioSelector && !bugActive && !injecting && (
              <div className="iz-scenario-selector">
                <div className="iz-scenario-selector-header">Choose Failure Scenario</div>
                {SCENARIO_LIST.map((s) => {
                  const meta = SCENARIO_META[s.type];
                  return (
                    <button
                      key={s.type}
                      className="iz-scenario-option"
                      onClick={() => injectBug(s.type)}
                    >
                      <span className="iz-scenario-option-emoji">{s.emoji}</span>
                      <span className="iz-scenario-option-text">
                        <strong>{s.label}</strong>
                        <small>{meta?.description || ""}</small>
                      </span>
                      <SeverityBadge severity={s.severity} />
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Reset button (after completion) */}
          {incidentStatus === "COMPLETE" && (
            <button className="iz-reset-btn" onClick={resetDashboard}>
              🔄 NEW INCIDENT
            </button>
          )}
        </div>
      </header>

      {/* ═══════════════════════════════════════════════════
          SCENARIO BANNER (shows active scenario type)
          ═══════════════════════════════════════════════════ */}
      {incidentType && bugActive && (
        <div className="iz-scenario-banner" style={{ background: scenarioMeta.gradient }}>
          <div className="iz-scenario-banner-left">
            <span className="iz-scenario-banner-emoji">{scenarioMeta.emoji}</span>
            <span className="iz-scenario-banner-label">{scenarioMeta.label}</span>
          </div>
          <div className="iz-scenario-banner-right">
            {triageData?.blast_radius_pct != null && (
              <span className="iz-scenario-banner-stat">
                💥 Blast Radius: {triageData.blast_radius_pct}%
              </span>
            )}
            <span className="iz-scenario-banner-desc">{scenarioMeta.description}</span>
          </div>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          AGENT STATUS BAR
          ═══════════════════════════════════════════════════ */}
      <div className="iz-agents-bar">
        {AGENT_STAGES.map((stage) => {
          const agentName = `${stage.agent}Agent`;
          const isActive = activeAgents.has(agentName);
          const isCurrent = stage.key === incidentStatus;

          return (
            <div key={stage.key} className={`iz-agent-chip ${isCurrent ? "iz-agent-chip-active" : isActive ? "iz-agent-chip-done" : ""}`}>
              <span className="iz-agent-chip-emoji">{stage.emoji}</span>
              <span className="iz-agent-chip-name">{stage.agent}</span>
              <span className="iz-agent-chip-dot" style={{
                background: isCurrent ? stage.color : isActive ? "#00ff88" : "#1e2748",
                boxShadow: isCurrent ? `0 0 8px ${stage.color}` : "none",
              }}>
                {isCurrent && <span className="iz-agent-chip-pulse" style={{ borderColor: stage.color }} />}
              </span>
            </div>
          );
        })}
      </div>

      {/* ═══════════════════════════════════════════════════
          PIPELINE
          ═══════════════════════════════════════════════════ */}
      <div className="iz-pipeline-wrap">
        <div className="iz-pipeline">
          {AGENT_STAGES.map((stage, idx) => {
            const si = STAGE_ORDER.indexOf(stage.key);
            const isActive = stage.key === incidentStatus;
            const isDone = currentStageIndex > si;
            const cls = `iz-stage ${isActive ? "iz-stage-active" : isDone ? "iz-stage-done" : "iz-stage-wait"}`;
            const nodeColor = isDone ? "#00ff88" : isActive ? stage.color : "#1e2748";

            return (
              <React.Fragment key={stage.key}>
                <div className={cls}>
                  <div className="iz-stage-node" style={{
                    borderColor: nodeColor,
                    boxShadow: isActive ? `0 0 20px ${stage.color}44` : isDone ? "0 0 12px #00ff8833" : "none",
                  }}>
                    {isDone ? (
                      <span className="iz-stage-check">✓</span>
                    ) : (
                      <span className="iz-stage-letter" style={{ color: isActive ? stage.color : "#5a6380" }}>
                        {stage.icon}
                      </span>
                    )}
                    {isActive && <span className="iz-stage-ping" style={{ borderColor: stage.color }} />}
                  </div>
                  <div className="iz-stage-name" style={{ color: isActive ? stage.color : isDone ? "#00ff88" : "#5a6380" }}>
                    {stage.label}
                  </div>
                  <div className="iz-stage-agent">{stage.agent}</div>
                </div>
                {idx < AGENT_STAGES.length - 1 && (
                  <div className={`iz-pipe ${isDone ? "iz-pipe-done" : isActive ? "iz-pipe-active" : ""}`}>
                    <div className="iz-pipe-line" />
                    {(isDone || isActive) && <div className="iz-pipe-flow" />}
                  </div>
                )}
              </React.Fragment>
            );
          })}
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════
          CONTENT
          ═══════════════════════════════════════════════════ */}
      <main className="iz-main">

        {/* ── Metrics: Scenario-Aware ─────────────────────── */}
        <div className="iz-metrics">
          {/* Card 1: Error Rate (always shown) */}
          <div className={`iz-metric ${errorRate > 10 ? "iz-metric-alert" : ""}`}>
            <div className="iz-metric-bar" style={{
              height: `${Math.max(4, Math.min(100, errorRate))}%`,
              background: `linear-gradient(to top, ${errorRate > 10 ? "#ff4757" : errorRate > 1 ? "#ffa502" : "#00ff88"}22, ${errorRate > 10 ? "#ff4757" : "#00ff88"}66)`,
            }} />
            <div className="iz-metric-top">
              <span className="iz-metric-icon" style={{ color: errorRate > 10 ? "#ff4757" : errorRate > 1 ? "#ffa502" : "#00ff88" }}>◉</span>
              <span className="iz-metric-label">ERROR RATE</span>
            </div>
            <div className="iz-metric-num">
              <span className="iz-metric-val" style={{ color: errorRate > 10 ? "#ff4757" : errorRate > 1 ? "#ffa502" : "#00ff88" }}>
                {errorRate.toFixed(1)}
              </span>
              <span className="iz-metric-unit">%</span>
            </div>
            <div className="iz-metric-spark"><Sparkline values={errorHistory} color={errorRate > 10 ? "#ff4757" : "#00ff88"} /></div>
            <div className="iz-metric-sub" style={{ color: errorRate > 10 ? "#ff4757" : errorRate > 1 ? "#ffa502" : undefined }}>
              {errorRate > 10 ? "⚠ CRITICAL" : errorRate > 1 ? "⚠ Elevated" : "✓ Nominal"}
            </div>
          </div>

          {/* Card 2: Scenario Primary Metric */}
          {(() => {
            const cfg = scenarioMeta.metrics.primary;
            const inv = isInverseMetric(cfg.key);
            const color = getMetricColor(primaryDisplay, cfg, inv);
            const pct = cfg.unit === "%"
              ? Math.min(100, primaryDisplay)
              : Math.min(100, (primaryDisplay / (cfg.critThreshold * 1.2)) * 100);
            const isCrit = inv ? primaryDisplay < cfg.critThreshold : primaryDisplay > cfg.critThreshold;

            return (
              <div className={`iz-metric ${isCrit ? "iz-metric-alert" : ""}`}>
                <div className="iz-metric-bar" style={{
                  height: `${Math.max(4, pct)}%`,
                  background: `linear-gradient(to top, ${color}22, ${color}66)`,
                }} />
                <div className="iz-metric-top">
                  <span className="iz-metric-icon" style={{ color }}>{cfg.icon}</span>
                  <span className="iz-metric-label">{cfg.label}</span>
                </div>
                <div className="iz-metric-num">
                  <span className="iz-metric-val" style={{ color }}>
                    {primaryDisplay >= 10000 ? `${(primaryDisplay / 1000).toFixed(0)}K` : primaryDisplay < 10 ? primaryDisplay.toFixed(1) : primaryDisplay.toFixed(0)}
                  </span>
                  <span className="iz-metric-unit">{cfg.unit}</span>
                </div>
                <div className="iz-metric-spark"><Sparkline values={primaryHistory} color={color} /></div>
                <div className="iz-metric-sub" style={{ color: isCrit ? color : undefined }}>
                  {isCrit ? `⚠ ${inv ? "LOW" : "HIGH"}` : "✓ OK"}
                </div>
              </div>
            );
          })()}

          {/* Card 3: Scenario Secondary Metric */}
          {(() => {
            const cfg = scenarioMeta.metrics.secondary;
            const inv = isInverseMetric(cfg.key);
            const color = getMetricColor(secondaryDisplay, cfg, inv);
            const pct = cfg.unit === "%"
              ? Math.min(100, secondaryDisplay)
              : Math.min(100, (secondaryDisplay / (cfg.critThreshold * 1.2)) * 100);
            const isCrit = inv ? secondaryDisplay < cfg.critThreshold : secondaryDisplay > cfg.critThreshold;

            return (
              <div className={`iz-metric ${isCrit ? "iz-metric-alert" : ""}`}>
                <div className="iz-metric-bar" style={{
                  height: `${Math.max(4, pct)}%`,
                  background: `linear-gradient(to top, ${color}22, ${color}66)`,
                }} />
                <div className="iz-metric-top">
                  <span className="iz-metric-icon" style={{ color }}>{cfg.icon}</span>
                  <span className="iz-metric-label">{cfg.label}</span>
                </div>
                <div className="iz-metric-num">
                  <span className="iz-metric-val" style={{ color }}>
                    {secondaryDisplay >= 10000 ? `${(secondaryDisplay / 1000).toFixed(0)}K` : secondaryDisplay < 10 ? secondaryDisplay.toFixed(1) : secondaryDisplay.toFixed(0)}
                  </span>
                  <span className="iz-metric-unit">{cfg.unit}</span>
                </div>
                <div className="iz-metric-spark"><Sparkline values={secondaryHistory} color={color} /></div>
                <div className="iz-metric-sub" style={{ color: isCrit ? color : undefined }}>
                  {isCrit ? `⚠ ${inv ? "LOW" : "HIGH"}` : "✓ OK"}
                </div>
              </div>
            );
          })()}

          {/* Card 4: Messages */}
          <div className="iz-metric">
            <div className="iz-metric-bar" style={{
              height: `${Math.max(4, Math.min(100, (incidentMessages.length / 20) * 100))}%`,
              background: "linear-gradient(to top, #a855f722, #a855f766)",
            }} />
            <div className="iz-metric-top">
              <span className="iz-metric-icon" style={{ color: "#a855f7" }}>◆</span>
              <span className="iz-metric-label">MESSAGES</span>
            </div>
            <div className="iz-metric-num">
              <span className="iz-metric-val" style={{ color: "#a855f7" }}>{incidentMessages.length}</span>
              <span className="iz-metric-unit">msgs</span>
            </div>
            <div className="iz-metric-spark"><Sparkline values={msgHistory} color="#a855f7" /></div>
            <div className="iz-metric-sub">
              {debateMessages.length} debate{debateMessages.length !== 1 ? "s" : ""}
            </div>
          </div>
        </div>

        {/* ── Tabs ─────────────────────────────────────────── */}
        <div className="iz-tabs">
          {[
            { id: "timeline" as const, label: "Activity",     count: incidentMessages.length, icon: "◉" },
                        { id: "debate" as const,   label: "Agent Debate", count: debateMessages.length,   icon: "⚔" },
            { id: "postmortem" as const, label: "Postmortem", count: postmortem ? 1 : 0,      icon: "◧" },
          ].map((tab) => (
            <button
              key={tab.id}
              className={`iz-tab ${activeTab === tab.id ? "iz-tab-on" : ""} ${tab.id === "postmortem" && showPostmortem ? "iz-tab-new" : ""} ${tab.id === "debate" && debateMessages.length > 0 ? "iz-tab-hot" : ""}`}
              onClick={() => { setActiveTab(tab.id); if (tab.id === "postmortem") setShowPostmortem(false); }}
            >
              <span className="iz-tab-icon">{tab.icon}</span>
              <span>{tab.label}</span>
              {tab.count > 0 && (
                <span className={`iz-tab-count ${tab.id === "debate" ? "iz-tab-count-debate" : tab.id === "postmortem" ? "iz-tab-count-pm" : ""}`}>
                  {tab.id === "postmortem" && showPostmortem ? "NEW" : tab.count}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* ── Tab Content ──────────────────────────────────── */}
        <div className="iz-panel">

          {/* ═══════════════════════════════════════════════
              TIMELINE TAB
              ═══════════════════════════════════════════════ */}
          {activeTab === "timeline" && (
            <div className="iz-timeline" ref={timelineRef}>
              {incidentMessages.length === 0 ? (
                <div className="iz-empty">
                  <div className="iz-empty-orb">
                    <div className="iz-empty-orb-ring" />
                    <div className="iz-empty-orb-ring iz-empty-orb-ring-2" />
                    <span className="iz-empty-orb-icon">◎</span>
                  </div>
                  <div className="iz-empty-title">Watching Production</div>
                  <div className="iz-empty-sub">
                    Inject a failure to activate the AI agent team
                  </div>
                  <div className="iz-empty-chips">
                    {AGENT_STAGES.map((s) => (
                      <span key={s.key} className="iz-chip" style={{ borderColor: `${s.color}44`, color: s.color }}>
                        {s.emoji} {s.agent}
                      </span>
                    ))}
                  </div>
                  {/* Scenario preview when idle */}
                  <div className="iz-empty-scenarios">
                    <div className="iz-empty-scenarios-title">7 Injectable Scenarios</div>
                    <div className="iz-empty-scenarios-grid">
                      {SCENARIO_LIST.map((s) => {
                        const meta = SCENARIO_META[s.type];
                        return (
                          <div key={s.type} className="iz-empty-scenario-card" style={{ borderColor: `${meta?.color || "#333"}33` }}>
                            <span className="iz-empty-scenario-emoji">{s.emoji}</span>
                            <span className="iz-empty-scenario-name">{s.label}</span>
                            <SeverityBadge severity={s.severity} />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              ) : (
                [...incidentMessages].reverse().map((msg, i) => {
                  const color = AGENT_COLORS[msg.sender] || "#888";
                  const msgType = safeStr(msg.message_type, "status");

                  return (
                    <div
                      key={`${msg.message_id}-${i}`}
                      className={`iz-tl-item iz-tl-${msgType}`}
                      style={{ borderLeftColor: color }}
                    >
                      <div className="iz-tl-head">
                        <span
                          className="iz-tl-avatar"
                          style={{ background: `${color}22`, color: color, borderColor: `${color}44` }}
                        >
                          {getAgentInitial(msg.sender)}
                        </span>
                        <span className="iz-tl-agent" style={{ color }}>
                          {(msg.sender || "Unknown").replace("Agent", "")}
                        </span>
                        <span className="iz-tl-type">{msgType.toUpperCase()}</span>
                        <span className="iz-tl-ch">
                          {(msg.channel || "").split(".").pop() || "system"}
                        </span>
                        <span className="iz-tl-time">{formatTime(msg.timestamp)}</span>
                        {msg.confidence > 0 && (
                          <span
                            className="iz-tl-conf"
                            style={{
                              color: msg.confidence > 0.8 ? "#00ff88" : msg.confidence > 0.5 ? "#ffa502" : "#ff4757",
                            }}
                          >
                            {(msg.confidence * 100).toFixed(0)}%
                          </span>
                        )}
                      </div>
                      <div className="iz-tl-body">{getMessageSummary(msg)}</div>

                      {/* Evidence tags */}
                      {msg.evidence && msg.evidence.length > 0 && (
                        <div className="iz-tl-evidence">
                          {msg.evidence.slice(0, 5).map((e, j) => (
                            <span key={j} className="iz-tl-evidence-tag">{e}</span>
                          ))}
                        </div>
                      )}

                      {/* PR link */}
                      {msg.channel === "incident.deployment" && msg.payload?.pr_url?.startsWith("http") && (
                        <a href={msg.payload.pr_url} target="_blank" rel="noopener noreferrer" className="iz-tl-link">
                          <span className="iz-tl-link-icon">↗</span> View Pull Request on GitHub
                        </a>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          )}

          {/* ═══════════════════════════════════════════════
              DEBATE TAB
              ═══════════════════════════════════════════════ */}
          {activeTab === "debate" && (
            <div className="iz-debate">
              {debateMessages.length === 0 ? (
                <div className="iz-empty">
                  <div className="iz-empty-orb">
                    <div className="iz-empty-orb-ring iz-ring-red" />
                    <span className="iz-empty-orb-icon">⚔</span>
                  </div>
                  <div className="iz-empty-title">Agent Debate Arena</div>
                  <div className="iz-empty-sub">Agents will challenge each other's analysis here</div>
                  <div className="iz-debate-vs">
                    <div className="iz-vs-card">
                      <span className="iz-vs-emoji">🔍</span>
                      <span className="iz-vs-agent" style={{ color: "#00ff88" }}>Diagnosis</span>
                      <span className="iz-vs-role">Defender</span>
                    </div>
                    <span className="iz-vs-x">VS</span>
                    <div className="iz-vs-card">
                      <span className="iz-vs-emoji">⚡</span>
                      <span className="iz-vs-agent" style={{ color: "#ff6b6b" }}>Resolution</span>
                      <span className="iz-vs-role">Challenger</span>
                    </div>
                  </div>
                </div>
              ) : (
                <>
                  {/* Debate Header with Scenario Context */}
                  <div className="iz-debate-header">
                    <span className="iz-debate-header-icon">⚔</span>
                    <span className="iz-debate-header-text">
                      {debateMessages.some((m) => m.message_type === "consensus")
                        ? "Consensus Reached — Agents Aligned"
                        : `Debate In Progress — ${debateMessages.length} Exchange${debateMessages.length !== 1 ? "s" : ""}`}
                    </span>
                    {incidentType && (
                      <span
                        className="iz-debate-header-scenario"
                        style={{ color: scenarioMeta.color }}
                      >
                        {scenarioMeta.emoji} {scenarioMeta.shortLabel}
                      </span>
                    )}
                    {debateMessages.some((m) => m.message_type === "consensus") && (
                      <span className="iz-debate-header-badge">✓ RESOLVED</span>
                    )}
                  </div>

                  {debateMessages.map((msg, i) => {
                    const isCh = msg.message_type === "challenge";
                    const isCo = msg.message_type === "consensus";
                    const isEv = msg.message_type === "evidence";
                    const ev = msg.payload?.evaluation;
                    const senderColor = AGENT_COLORS[msg.sender] || "#888";

                    // Extract reasoning from various payload shapes
                    const reasoning = safeStr(
                      ev?.reasoning ||
                      msg.payload?.response ||
                      msg.payload?.reasoning ||
                      msg.payload?.challenge_reason ||
                      (isCh ? "Challenging the diagnosis..." : isCo ? "Agents reached consensus" : "Presenting evidence"),
                      "Processing...",
                    );

                    const challengeQuestion = safeStr(
                      ev?.challenge_question || msg.payload?.challenge_question,
                      "",
                    );

                    const additionalEvidence: string[] =
                      msg.payload?.additional_evidence ||
                      msg.payload?.evidence_analysis ||
                      msg.evidence ||
                      [];

                    const confidence = safeNum(
                      ev?.confidence_in_diagnosis ?? msg.payload?.confidence ?? msg.confidence,
                      0,
                    );

                    const responseType = safeStr(msg.payload?.response_type, "");
                    const debateRound = safeNum(msg.payload?.debate_round, i + 1);

                    return (
                      <div
                        key={`${msg.message_id}-${i}`}
                        className={`iz-db-msg ${isCh ? "iz-db-challenge" : isCo ? "iz-db-consensus" : "iz-db-evidence"}`}
                      >
                        <div className="iz-db-head">
                          <span className="iz-db-round">R{debateRound}</span>
                          <span
                            className="iz-db-avatar"
                            style={{
                              background: `${senderColor}22`,
                              color: senderColor,
                              borderColor: `${senderColor}44`,
                            }}
                          >
                            {getAgentInitial(msg.sender)}
                          </span>
                          <span className="iz-db-sender" style={{ color: senderColor }}>
                            {(msg.sender || "Unknown").replace("Agent", "")}
                          </span>
                          <span
                            className={`iz-db-badge ${isCh ? "iz-db-badge-ch" : isCo ? "iz-db-badge-co" : "iz-db-badge-ev"}`}
                          >
                            {isCh
                              ? "⚡ CHALLENGE"
                              : isCo
                                ? "🤝 CONSENSUS"
                                : responseType === "DEFEND"
                                  ? "🛡 DEFEND"
                                  : "📊 EVIDENCE"}
                          </span>
                          <span className="iz-db-time">{formatTime(msg.timestamp)}</span>
                        </div>

                        <div className="iz-db-body">{reasoning}</div>

                        {/* Challenge question */}
                        {isCh && challengeQuestion && (
                          <div className="iz-db-q">
                            <span className="iz-db-q-label">CHALLENGE QUESTION</span>
                            {challengeQuestion}
                          </div>
                        )}

                        {/* Alternative hypothesis */}
                        {isCh && ev?.alternative_hypothesis && (
                          <div className="iz-db-alt">
                            <span className="iz-db-alt-label">ALTERNATIVE HYPOTHESIS</span>
                            {safeStr(ev.alternative_hypothesis, "").replace(/_/g, " ")}
                          </div>
                        )}

                        {/* Evidence bullets */}
                        {additionalEvidence.length > 0 && (
                          <div className="iz-db-evidence-list">
                            <span className="iz-db-evidence-label">
                              {isEv || responseType ? "SUPPORTING EVIDENCE" : "EVIDENCE"}
                            </span>
                            <ul>
                              {additionalEvidence.map((e, j) => (
                                <li key={j}>{safeStr(e, "—")}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {/* Confidence bar */}
                        {confidence > 0 && (
                          <div className="iz-db-conf">
                            <span>Confidence</span>
                            <div className="iz-db-conf-track">
                              <div
                                className="iz-db-conf-fill"
                                style={{
                                  width: `${confidence * 100}%`,
                                  background: confidence > 0.7 ? "#00ff88" : confidence > 0.5 ? "#ffa502" : "#ff4757",
                                }}
                              />
                            </div>
                            <span
                              style={{
                                color: confidence > 0.7 ? "#00ff88" : confidence > 0.5 ? "#ffa502" : "#ff4757",
                              }}
                            >
                              {(confidence * 100).toFixed(0)}%
                            </span>
                          </div>
                        )}

                        {/* Debate concluded notice */}
                        {msg.payload?.debate_concluded && (
                          <div className="iz-db-done">✓ Debate concluded — proceeding to resolution</div>
                        )}
                      </div>
                    );
                  })}
                </>
              )}
            </div>
          )}

          {/* ═══════════════════════════════════════════════
              POSTMORTEM TAB
              ═══════════════════════════════════════════════ */}
          {activeTab === "postmortem" && (
            <div className="iz-pm">
              {!postmortem ? (
                <div className="iz-empty">
                  <div className="iz-empty-orb">
                    <div className="iz-empty-orb-ring iz-ring-cyan" />
                    <span className="iz-empty-orb-icon">◧</span>
                  </div>
                  <div className="iz-empty-title">Postmortem Report</div>
                  <div className="iz-empty-sub">Auto-generated after incident resolution</div>
                  <div className="iz-empty-steps">
                    <div className="iz-empty-step">
                      <span className="iz-empty-step-num">1</span>
                      <span>Incident detected &amp; resolved</span>
                    </div>
                    <div className="iz-empty-step">
                      <span className="iz-empty-step-num">2</span>
                      <span>PostmortemAgent analyzes all messages</span>
                    </div>
                    <div className="iz-empty-step">
                      <span className="iz-empty-step-num">3</span>
                      <span>Professional report generated via LLM</span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="iz-pm-content">
                  {/* Postmortem Header */}
                  <div className="iz-pm-head">
                    <div className="iz-pm-head-left">
                      <span className="iz-pm-badge">✓ AUTO-GENERATED</span>
                      <span className="iz-pm-badge iz-pm-badge-ai">🤖 AI-POWERED</span>
                      {incidentType && (
                        <span
                          className="iz-pm-badge"
                          style={{ background: `${scenarioMeta.color}22`, color: scenarioMeta.color, borderColor: `${scenarioMeta.color}44` }}
                        >
                          {scenarioMeta.emoji} {scenarioMeta.shortLabel}
                        </span>
                      )}
                    </div>
                    {elapsedTime > 0 && (
                      <div className="iz-pm-head-right">
                        <span className="iz-pm-time-label">Time to Resolution</span>
                        <span className="iz-pm-time">{formatElapsed(elapsedTime)}</span>
                      </div>
                    )}
                  </div>

                  {/* Postmortem Meta Stats */}
                  <div className="iz-pm-meta-bar">
                    {postmortem.incident_type && (
                      <div className="iz-pm-meta-item" style={{ color: scenarioMeta.color }}>
                        <span>{scenarioMeta.emoji}</span>
                        <span>{scenarioMeta.label}</span>
                      </div>
                    )}
                    {postmortem.resolution_time_seconds && (
                      <div className="iz-pm-meta-item">
                        <span>⏱</span>
                        <span>{postmortem.resolution_time_seconds}s MTTR</span>
                      </div>
                    )}
                    {postmortem.debate_rounds && (
                      <div className="iz-pm-meta-item">
                        <span>⚔</span>
                        <span>{postmortem.debate_rounds} debate round{postmortem.debate_rounds !== 1 ? "s" : ""}</span>
                      </div>
                    )}
                    {postmortem.total_messages && (
                      <div className="iz-pm-meta-item">
                        <span>💬</span>
                        <span>{postmortem.total_messages} messages</span>
                      </div>
                    )}
                    {postmortem.agents_involved && (
                      <div className="iz-pm-meta-item">
                        <span>🤖</span>
                        <span>{postmortem.agents_involved.length} agents</span>
                      </div>
                    )}
                  </div>

                  {/* Postmortem Markdown Body */}
                  <div className="iz-pm-body">
                    {safeStr(postmortem.report_markdown || postmortem.report, "Generating report...")
                      .split("\n")
                      .map((line: string, i: number) => renderMarkdownLine(line, i))}
                  </div>

                  {/* PR Link */}
                  {postmortem.pr_url && postmortem.pr_url.startsWith("http") && (
                    <a
                      href={postmortem.pr_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="iz-pm-pr-link"
                    >
                      <span>↗</span> View GitHub Pull Request
                    </a>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </main>

      {/* ═══════════════════════════════════════════════════
          FOOTER
          ═══════════════════════════════════════════════════ */}
      <footer className="iz-footer">
        <span>Azure OpenAI GPT-4o · MCP Protocol · 7 Scenarios</span>
        <span className="iz-footer-brand">IncidentZero — Autonomous AI SRE</span>
        <span>6 Agents · {SCENARIO_LIST.length} Failures · Zero Incidents</span>
      </footer>
    </div>
  );
}

export default App;