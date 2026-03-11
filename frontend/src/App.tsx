import React, { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { useWebSocket, MCPMessage } from "./hooks/useWebSocket";
import "./App.css";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "http://localhost:8080";
const WS_URL = process.env.REACT_APP_WS_URL || "ws://localhost:8080/ws";

/* ═══════════════════════════════════════════════════════════
   Constants
   ═══════════════════════════════════════════════════════════ */
const AGENT_STAGES = [
  { key: "DETECTED", icon: "W", label: "DETECT", agent: "Watcher", color: "#00d2ff", emoji: "👁" },
  { key: "TRIAGING", icon: "T", label: "TRIAGE", agent: "Triage", color: "#ffd700", emoji: "🔺" },
  { key: "DIAGNOSING", icon: "D", label: "DIAGNOSE", agent: "Diagnosis", color: "#00ff88", emoji: "🔍" },
  { key: "DEBATING", icon: "R", label: "DEBATE", agent: "Resolution", color: "#ff6b6b", emoji: "⚡" },
  { key: "DEPLOYING", icon: "X", label: "DEPLOY", agent: "Deploy", color: "#a855f7", emoji: "🚀" },
  { key: "COMPLETE", icon: "P", label: "REPORT", agent: "Postmortem", color: "#06b6d4", emoji: "📋" },
];

const STAGE_ORDER = ["MONITORING", "DETECTED", "TRIAGING", "DIAGNOSING", "DEBATING", "DEPLOYING", "RESOLVED", "COMPLETE"];

const AGENT_COLORS: Record<string, string> = {
  WatcherAgent: "#00d2ff",
  TriageAgent: "#ffd700",
  DiagnosisAgent: "#00ff88",
  ResolutionAgent: "#ff6b6b",
  DeployAgent: "#a855f7",
  PostmortemAgent: "#06b6d4",
  OrchestratorAgent: "#e94560",
};

/* ═══════════════════════════════════════════════════════════
   Sparkline — tiny inline chart from recent values
   ═══════════════════════════════════════════════════════════ */
function Sparkline({ values, color, width = 80, height = 24 }: { values: number[]; color: string; width?: number; height?: number }) {
  if (values.length < 2) return null;

  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min === 0 ? 1 : max - min; // Prevent division by zero

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
      
      {/* Glowing dot on the latest point */}
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
   Main App
   ═══════════════════════════════════════════════════════════ */
function App() {
  const { messages, connected } = useWebSocket(WS_URL);
  
  // ─── State ──────────────────────────────────────────────
  const [injecting, setInjecting] = useState(false);
  const [bugActive, setBugActive] = useState(false);
  const [elapsedTime, setElapsedTime] = useState(0);
  const [incidentStartTime, setIncidentStartTime] = useState<number | null>(null);
  const [showPostmortem, setShowPostmortem] = useState(false);
  const [activeTab, setActiveTab] = useState<"timeline" | "debate" | "postmortem">("timeline");
  const [splashPhase, setSplashPhase] = useState<"launching" | "fading" | "done">("launching");
  const [showFlash, setShowFlash] = useState(false);
  const [showCelebration, setShowCelebration] = useState(false);

  // ─── Metric History State ───────────────────────────────
  const [errorHistory, setErrorHistory] = useState<number[]>([0]);
  const [connHistory, setConnHistory] = useState<number[]>([0]);
  const [respHistory, setRespHistory] = useState<number[]>([0]);
  const [msgHistory, setMsgHistory] = useState<number[]>([0]);

  const timelineRef = useRef<HTMLDivElement>(null);
  const prevIncidentCount = useRef(0);

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

  // ─── Derived Data ───────────────────────────────────────
  const latestStatus = useMemo(() => {
    const s = messages.filter((m) => m.channel === "monitoring.status");
    return s.length > 0 ? s[s.length - 1].payload : null;
  }, [messages]);

  const incidentMessages = useMemo(() => 
    messages.filter((m) => m.channel !== "monitoring.status" && m.channel !== "system.status"),
    [messages]
  );

  const debateMessages = useMemo(() => 
    messages.filter((m) => m.channel === "incident.debate"),
    [messages]
  );

  const postmortem = useMemo(() => {
    const pm = messages.filter((m) => m.channel === "incident.postmortem");
    return pm.length > 0 ? pm[pm.length - 1].payload : null;
  }, [messages]);

  const currentIncident = useMemo(() => {
    const inc = messages.filter((m) => m.channel === "incident.detection");
    return inc.length > 0 ? inc[inc.length - 1] : null;
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

  // ─── Metrics Calculation ────────────────────────────────
  const errorRate = (latestStatus?.error_rate || 0) * 100;
  const connActive = latestStatus?.active_connections || 0;
  const connMax = latestStatus?.max_connections || 20;
  const connPercent = connMax > 0 ? (connActive / connMax) * 100 : 0;
  const responseTime = latestStatus?.avg_response_time_ms || 0;

  useEffect(() => {
    setErrorHistory((h) => [...h.slice(-19), errorRate]);
    setConnHistory((h) => [...h.slice(-19), connPercent]);
    setRespHistory((h) => [...h.slice(-19), responseTime]);
    setMsgHistory((h) => [...h.slice(-19), incidentMessages.length]);
  }, [errorRate, connPercent, responseTime, incidentMessages.length]);

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
  const injectBug = useCallback(async () => {
    setInjecting(true);
    setBugActive(true);
    setIncidentStartTime(Date.now());
    setElapsedTime(0);
    setShowPostmortem(false);
    setShowCelebration(false);
    setActiveTab("timeline");
    setErrorHistory([0]);
    setConnHistory([0]);
    setRespHistory([0]);
    setMsgHistory([0]);

    try {
      await fetch(`${BACKEND_URL}/api/inject`, { method: "POST" });
      
      // Poll a few times to see if the target health check drops (expected failure behavior)
      for (let i = 0; i < 3; i++) {
        try { 
          const res = await fetch(`${BACKEND_URL}/api/target/health`); 
          if (!res.ok) break; // System has degraded as expected!
        } catch {
          break; // Network error means it's likely down
        }
        await new Promise((r) => setTimeout(r, 500));
      }
    } catch (e) { 
      console.error("Inject failed:", e); 
      setBugActive(false); // Revert UI if the inject failed entirely
    } finally {
      setInjecting(false);
    }
  }, []);

  // ─── Helpers ────────────────────────────────────────────
  const formatTime = (ts: string) => {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts; // Fallback if string is not a valid date
    return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };

  const formatElapsed = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return (m > 0 ? `${m}m ` : "") + `${sec}s`;
  };

  const getMessageSummary = (msg: MCPMessage): string => {
    const p = msg.payload;
    switch (msg.channel) {
      case "incident.detection": return `INCIDENT DETECTED — Error rate: ${((p?.data?.error_rate || 0) * 100).toFixed(1)}%`;
      case "incident.orchestration": return (p?.action || "Action triggered").replace(/_/g, " ");
      case "incident.triage": return `Severity: ${p?.severity || "?"} — ${p?.classification || "?"} — Blast: ${p?.blast_radius_pct || "?"}%`;
      case "incident.diagnosis": {
        const rc = p?.root_cause;
        return rc && typeof rc === "object" ? `Root cause: ${rc.detail || rc.mechanism || "identified"}` : "Root cause analysis complete";
      }
      case "incident.debate": {
        const ev = p?.evaluation;
        if (msg.message_type === "challenge") return `CHALLENGE: ${(ev?.reasoning || "Challenging...").slice(0, 180)}`;
        if (msg.message_type === "consensus") return `CONSENSUS: ${(ev?.reasoning || "Agents agree").slice(0, 180)}`;
        return `${(msg.message_type || "").toUpperCase()}: ${JSON.stringify(p).slice(0, 120)}`;
      }
      case "incident.resolution": return `Fix: ${p?.fix?.description || "Code fix ready"} (Risk: ${p?.fix?.risk_level || "?"})`;
      case "incident.deployment": return `Deploy: ${p?.status || "?"} — Health: ${p?.health_check || "?"}${p?.pr_url?.startsWith("http") ? " — PR created" : ""}`;
      case "incident.postmortem": return `Postmortem generated (${p?.total_messages || "?"} messages)`;
      default: return JSON.stringify(p).slice(0, 100);
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
    return "#ff4757";
  };

  const getStatusEmoji = () => {
    const map: Record<string, string> = {
      MONITORING: "🟢", DETECTED: "🔴", TRIAGING: "🟡", DIAGNOSING: "🔍",
      DEBATING: "⚔️", DEPLOYING: "🚀", RESOLVED: "✅", COMPLETE: "🏆",
    };
    return map[incidentStatus] || "⬡";
  };

  // Helper to safely map markdown to primitive JSX
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

      {/* ═══════════════════════════════════════════════════
          INCIDENT FLASH (red pulse on detection)
          ═══════════════════════════════════════════════════ */}
      {showFlash && <div className="iz-flash" />}

      {/* ═══════════════════════════════════════════════════
          CELEBRATION (on resolution complete)
          ═══════════════════════════════════════════════════ */}
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
              <div className="iz-celebrate-sub">Autonomous fix deployed in {formatElapsed(elapsedTime)}</div>
            </div>
            <span className="iz-celebrate-emoji">🏆</span>
          </div>
        </div>
      )}

      {/* ── Background ───────────────────────────────────── */}
      <div className="iz-bg">
        <div className="iz-bg-grid" />
        <div className="iz-bg-glow iz-bg-glow-1" />
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
              <div className="iz-timer-fill" style={{ width: `${Math.min(100, (elapsedTime / 120) * 100)}%` }} />
            </div>
          </div>
        )}

        <div className="iz-header-controls">
          <div className="iz-badges">
            <div className="iz-badge" style={{ borderColor: `${getStatusColor()}44`, color: getStatusColor() }}>
              <span className="iz-badge-dot" style={{ background: getStatusColor(), boxShadow: `0 0 8px ${getStatusColor()}` }} />
              <span className="iz-badge-emoji">{getStatusEmoji()}</span>
              {incidentStatus}
            </div>
            <div className={`iz-badge ${connected ? "iz-badge-live" : "iz-badge-off"}`}>
              <span className={`iz-badge-dot ${connected ? "iz-dot-live" : "iz-dot-off"}`} />
              {connected ? "LIVE" : "OFFLINE"}
            </div>
          </div>
          <button
            className={`iz-inject ${bugActive ? "iz-inject-active" : injecting ? "iz-inject-loading" : "iz-inject-ready"}`}
            onClick={injectBug}
            disabled={injecting || bugActive}
            aria-label={injecting ? "Injecting failure..." : bugActive ? "Agents currently active" : "Inject a failure"}
          >
            <span className="iz-inject-glow" />
            <span className="iz-inject-text">
              {injecting ? "⏳ INJECTING..." : bugActive ? "🔴 AGENTS ACTIVE" : "⚡ INJECT FAILURE"}
            </span>
          </button>
        </div>
      </header>

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
                boxShadow: isCurrent ? `0 0 8px ${stage.color}` : "none"
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
            
            return (
              <React.Fragment key={stage.key}>
                <div className={cls}>
                  <div className="iz-stage-node" style={{
                    borderColor: isDone ? "#00ff88" : isActive ? stage.color : "#1e2748",
                    boxShadow: isActive ? `0 0 20px ${stage.color}44` : isDone ? "0 0 12px #00ff8833" : "none"
                  }}>
                    {isDone ? (
                      <span className="iz-stage-check">✓</span>
                    ) : (
                      <span className="iz-stage-letter" style={{ color: isActive ? stage.color : "#5a6380" }}>{stage.icon}</span>
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

        {/* ── Metrics ──────────────────────────────────────── */}
        <div className="iz-metrics">
          {[
            {
              label: "ERROR RATE", value: errorRate.toFixed(1), unit: "%",
              sub: errorRate > 10 ? "⚠ CRITICAL" : errorRate > 1 ? "⚠ Elevated" : "✓ Nominal",
              pct: Math.min(100, errorRate),
              color: errorRate > 10 ? "#ff4757" : errorRate > 1 ? "#ffa502" : "#00ff88",
              history: errorHistory, icon: "◉",
            },
            {
              label: "CONNECTIONS", value: String(connActive), unit: `/${connMax}`,
              sub: connPercent > 75 ? "⚠ Pool exhaustion" : "Pool Usage",
              pct: connPercent,
              color: connPercent > 75 ? "#ff4757" : connPercent > 50 ? "#ffa502" : "#00d2ff",
              history: connHistory, icon: "◈",
            },
            {
              label: "RESPONSE", value: responseTime.toFixed(0), unit: "ms",
              sub: responseTime > 500 ? "⚠ Degraded" : "P99 Latency",
              pct: Math.min(100, (responseTime / 1000) * 100),
              color: responseTime > 500 ? "#ff4757" : "#00ff88",
              history: respHistory, icon: "◇",
            },
            {
              label: "MESSAGES", value: String(incidentMessages.length), unit: "msgs",
              sub: `${debateMessages.length} debate round${debateMessages.length !== 1 ? "s" : ""}`,
              pct: Math.min(100, (incidentMessages.length / 20) * 100),
              color: "#a855f7",
              history: msgHistory, icon: "◆",
            },
          ].map((m, i) => (
            <div key={i} className={`iz-metric ${m.pct > 75 && i < 3 ? "iz-metric-alert" : ""}`}>
              <div className="iz-metric-bar" style={{
                height: `${Math.max(4, m.pct)}%`,
                background: `linear-gradient(to top, ${m.color}22, ${m.color}66)`
              }} />
              <div className="iz-metric-top">
                <span className="iz-metric-icon" style={{ color: m.color }}>{m.icon}</span>
                <span className="iz-metric-label">{m.label}</span>
              </div>
              <div className="iz-metric-num">
                <span className="iz-metric-val" style={{ color: m.color }}>{m.value}</span>
                <span className="iz-metric-unit">{m.unit}</span>
              </div>
              <div className="iz-metric-spark">
                <Sparkline values={m.history} color={m.color} />
              </div>
              <div className="iz-metric-sub" style={{ color: m.sub.startsWith("⚠") ? m.color : undefined }}>
                {m.sub}
              </div>
            </div>
          ))}
        </div>

        {/* ── Tabs ─────────────────────────────────────────── */}
        <div className="iz-tabs">
          {[
            { id: "timeline" as const, label: "Activity", count: incidentMessages.length, icon: "◉" },
            { id: "debate" as const, label: "Agent Debate", count: debateMessages.length, icon: "⚔" },
            { id: "postmortem" as const, label: "Postmortem", count: postmortem ? 1 : 0, icon: "◧" },
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

          {/* TIMELINE */}
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
                  <div className="iz-empty-sub">Inject a failure to activate the AI agent team</div>
                  <div className="iz-empty-chips">
                    {AGENT_STAGES.map((s) => (
                      <span key={s.key} className="iz-chip" style={{ borderColor: `${s.color}44`, color: s.color }}>
                        {s.emoji} {s.agent}
                      </span>
                    ))}
                  </div>
                </div>
              ) : (
                [...incidentMessages].reverse().map((msg, i) => {
                  const color = AGENT_COLORS[msg.sender] || "#888";
                  return (
                    <div key={`${msg.message_id}-${i}`} className={`iz-tl-item iz-tl-${msg.message_type}`} style={{ borderLeftColor: color }}>
                      <div className="iz-tl-head">
                        <span className="iz-tl-avatar" style={{ background: `${color}22`, color: color, borderColor: `${color}44` }}>
                          {getAgentInitial(msg.sender)}
                        </span>
                        <span className="iz-tl-agent" style={{ color }}>{msg.sender.replace("Agent", "")}</span>
                        <span className="iz-tl-type">{(msg.message_type || "").toUpperCase()}</span>
                        <span className="iz-tl-ch">{msg.channel.split(".").pop()}</span>
                        <span className="iz-tl-time">{formatTime(msg.timestamp)}</span>
                        {msg.confidence > 0 && (
                          <span className="iz-tl-conf" style={{
                            color: msg.confidence > 0.8 ? "#00ff88" : msg.confidence > 0.5 ? "#ffa502" : "#ff4757"
                          }}>
                            {(msg.confidence * 100).toFixed(0)}%
                          </span>
                        )}
                      </div>
                      <div className="iz-tl-body">{getMessageSummary(msg)}</div>
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

          {/* DEBATE */}
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
                  <div className="iz-debate-header">
                    <span className="iz-debate-header-icon">⚔</span>
                    <span className="iz-debate-header-text">
                      {debateMessages.some((m) => m.message_type === "consensus")
                        ? "Consensus Reached — Agents Aligned"
                        : `Debate In Progress — ${debateMessages.length} Exchange${debateMessages.length !== 1 ? "s" : ""}`}
                    </span>
                    {debateMessages.some((m) => m.message_type === "consensus") && (
                      <span className="iz-debate-header-badge">✓ RESOLVED</span>
                    )}
                  </div>
                  {debateMessages.map((msg, i) => {
                    const isCh = msg.message_type === "challenge";
                    const isCo = msg.message_type === "consensus";
                    const ev = msg.payload?.evaluation;
                    const senderColor = AGENT_COLORS[msg.sender] || "#888";

                    return (
                      <div key={`${msg.message_id}-${i}`} className={`iz-db-msg ${isCh ? "iz-db-challenge" : isCo ? "iz-db-consensus" : "iz-db-evidence"}`}>
                        <div className="iz-db-head">
                          <span className="iz-db-round">R{msg.payload?.debate_round || i + 1}</span>
                          <span className="iz-db-avatar" style={{
                            background: `${senderColor}22`,
                            color: senderColor,
                            borderColor: `${senderColor}44`,
                          }}>
                            {getAgentInitial(msg.sender)}
                          </span>
                          <span className="iz-db-sender" style={{ color: senderColor }}>
                            {msg.sender.replace("Agent", "")}
                          </span>
                          <span className={`iz-db-badge ${isCh ? "iz-db-badge-ch" : isCo ? "iz-db-badge-co" : "iz-db-badge-ev"}`}>
                            {isCh ? "⚡ CHALLENGE" : isCo ? "🤝 CONSENSUS" : "📊 EVIDENCE"}
                          </span>
                          <span className="iz-db-time">{formatTime(msg.timestamp)}</span>
                        </div>
                        <div className="iz-db-body">
                          {isCh
                            ? (ev?.reasoning || JSON.stringify(msg.payload).slice(0, 250))
                            : isCo
                              ? (ev?.reasoning || "Agents reached consensus")
                              : (msg.payload?.response || JSON.stringify(msg.payload).slice(0, 250))}
                        </div>
                        {isCh && ev?.challenge_question && (
                          <div className="iz-db-q">
                            <span className="iz-db-q-label">CHALLENGE QUESTION</span>
                            {ev.challenge_question}
                          </div>
                        )}
                        {ev?.confidence_in_diagnosis !== undefined && (
                          <div className="iz-db-conf">
                            <span>Confidence</span>
                            <div className="iz-db-conf-track">
                              <div className="iz-db-conf-fill" style={{
                                width: `${ev.confidence_in_diagnosis * 100}%`,
                                background: ev.confidence_in_diagnosis > 0.7 ? "#00ff88" : "#ffa502"
                              }} />
                            </div>
                            <span style={{ color: ev.confidence_in_diagnosis > 0.7 ? "#00ff88" : "#ffa502" }}>
                              {(ev.confidence_in_diagnosis * 100).toFixed(0)}%
                            </span>
                          </div>
                        )}
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

          {/* POSTMORTEM */}
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
                      <span>Professional report generated via GPT-4o</span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="iz-pm-content">
                  <div className="iz-pm-head">
                    <div className="iz-pm-head-left">
                      <span className="iz-pm-badge">✓ AUTO-GENERATED</span>
                      <span className="iz-pm-badge iz-pm-badge-ai">🤖 GPT-4o</span>
                    </div>
                    {elapsedTime > 0 && (
                      <div className="iz-pm-head-right">
                        <span className="iz-pm-time-label">Time to Resolution</span>
                        <span className="iz-pm-time">{formatElapsed(elapsedTime)}</span>
                      </div>
                    )}
                  </div>
                  <div className="iz-pm-body">
                    {(postmortem.report_markdown || postmortem.report || "Generating...")
                      .split("\n")
                      .map((line: string, i: number) => renderMarkdownLine(line, i))}
                  </div>
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
        <span>Azure OpenAI GPT-4o · Microsoft Foundry · MCP Protocol</span>
        <span className="iz-footer-brand">Microsoft AI Dev Days Hackathon 2026</span>
        <span>6 Agents · 1 Mission · Zero Incidents</span>
      </footer>
    </div>
  );
}

export default App;