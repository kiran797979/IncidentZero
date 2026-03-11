<div align="center">

# 🖥 IncidentZero — Frontend

### Real-Time Cinematic Incident Dashboard

[![React](https://img.shields.io/badge/React-19.2-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-4.9-3178C6?style=for-the-badge&logo=typescript&logoColor=white)](https://typescriptlang.org)
[![WebGL](https://img.shields.io/badge/WebGL-OGL-orange?style=for-the-badge)](https://github.com/oframe/ogl)
[![Motion](https://img.shields.io/badge/Motion-12.35-ff69b4?style=for-the-badge)](https://motion.dev)

**A dark-themed, WebGL-powered React dashboard that provides full real-time observability into IncidentZero's autonomous multi-agent incident resolution pipeline.**

</div>

---

## Overview

This is the frontend module of [IncidentZero](../README.md) — an autonomous AI SRE system. The dashboard connects to the backend via **WebSocket** and **REST API polling** to visualize every step of the incident lifecycle in real time: detection, triage, diagnosis, adversarial debate, resolution, deployment, and postmortem.

---

## Quick Start

```bash
# Install dependencies
npm install

# Set backend connection (Windows)
set REACT_APP_BACKEND_URL=http://localhost:8091
set REACT_APP_WS_URL=ws://localhost:8091/ws

# Linux / macOS
export REACT_APP_BACKEND_URL=http://localhost:8091
export REACT_APP_WS_URL=ws://localhost:8091/ws

# Start dev server
npm start
```

> Opens at `http://localhost:3000`. Requires the backend running on port **8091**.

---

## Architecture

```
WebSocket (ws://backend/ws)
    ↓
useWebSocket Hook (auto-reconnect, exponential backoff, keepalive)
    ↓
MCPMessage Stream (structured inter-agent messages)
    ↓
App.tsx (state management + derived data via useMemo)
    ├── Splash Screen (animated rocket launch → fade)
    ├── Status Bar (LIVE/OFFLINE badge, incident status, MTTR timer)
    ├── Metrics Panel (error rate, connections, response time, message count)
    │   ├── CountUp.tsx (animated numbers with spring physics)
    │   └── Sparkline (inline SVG charts with gradient fill)
    ├── Agent Pipeline (6-stage visual progress indicator)
    ├── Tabbed Content
    │   ├── Timeline Tab (reverse-chronological MCP message feed)
    │   ├── Debate Tab (challenge → evidence → consensus flow)
    │   └── Postmortem Tab (formatted incident report)
    ├── INJECT FAILURE Button (StarBorder glow effect)
    └── Background Layer
        └── Particles.tsx (WebGL 3D particle system)
```

### Data Flow

The dashboard uses **dual-signal reliability**:

1. **WebSocket** — Real-time push of MCP messages as agents communicate
2. **REST API Polling** — Fallback every 3s during incidents (10s idle) to catch messages during reconnection

Messages are filtered by channel (`monitoring.status`, `incident.debate`, `incident.postmortem`, etc.) and routed to the appropriate UI sections.

---

## Features

| Feature | Description |
|---------|-------------|
| **WebGL Particle Background** | 200 animated 3D particles (OGL) with mouse interaction, sine-wave motion, and dynamic colors |
| **Splash Screen** | Animated rocket launch on app startup (3.4s animation → fade) |
| **LIVE / OFFLINE Badge** | Dual-signal WebSocket + REST status with 3s debounce to prevent flicker |
| **Incident Status Badge** | Color-coded pipeline stage: MONITORING → DETECTED → TRIAGING → DIAGNOSING → DEBATING → DEPLOYING → RESOLVED |
| **Agent Pipeline** | 6-step visual progress: Watcher (👁) → Triage (🔺) → Diagnosis (🔍) → Resolution (⚡) → Deploy (🚀) → Postmortem (📋) |
| **Metrics Panel** | Animated counters + sparklines for error rate, active connections, response time, message count |
| **MTTR Timer** | Live elapsed-time counter from incident start to resolution |
| **Timeline Tab** | All MCP messages in reverse-chronological order with agent-colored icons |
| **Debate Tab** | CHALLENGE → EVIDENCE → CONSENSUS flow with confidence scores and evidence bullets |
| **Postmortem Tab** | Full incident report: executive summary, root cause, debate highlights, impact, recommendations |
| **INJECT FAILURE Button** | StarBorder glow effect, loading/active/ready states |
| **Celebration Animation** | Confetti-style particle burst on incident resolution |
| **Flash Effect** | Screen flash on incident detection |
| **DecryptedText** | Animated text scramble/reveal effect for titles |

---

## Components

### `App.tsx` — Main Dashboard

The root component managing all state, derived data, and UI layout.

**State:**
- `injecting` / `bugActive` — chaos injection status
- `elapsedTime` / `incidentStartTime` — MTTR tracking
- `activeTab` — `"timeline"` | `"debate"` | `"postmortem"`
- `splashPhase` — `"launching"` | `"fading"` | `"done"`
- `errorHistory` / `connHistory` / `respHistory` / `msgHistory` — metric arrays for sparklines

**Derived Data (useMemo):**
- `latestStatus` — latest `monitoring.status` message (real-time metrics)
- `incidentMessages` — all non-system MCP messages
- `debateMessages` — `incident.debate` channel messages
- `postmortem` — latest `incident.postmortem` payload
- `currentIncident` — latest `incident.detection` message
- `incidentStatus` — derived pipeline stage

### `Particles.tsx` — WebGL Background

3D particle system built with [OGL](https://github.com/oframe/ogl):
- Custom vertex & fragment shaders
- Configurable: `particleCount`, `colors`, `speed`, hover interaction, rotation
- Responsive canvas resizing
- Sine-wave motion in 3D space with camera rotation

### `CountUp.tsx` — Animated Numbers

Spring-physics number counter using Motion's `useMotionValue` + `useSpring`:
- Props: `to`, `from`, `direction`, `delay`, `duration`, `separator`, `decimals`, `suffix`
- Triggers on element visibility (Intersection Observer)
- Callbacks: `onStart`, `onEnd`

### `DecryptedText.tsx` — Text Reveal Effect

Character-by-character text decryption animation:
- Props: `speed`, `maxIterations`, `revealDirection` (`start`/`end`/`center`), `sequential`
- Trigger modes: `view`, `hover`, or `both`
- Uses original characters as encryption pool

### `StarBorder.tsx` — Glowing Border

Polymorphic border component with animated star/sparkle gradient:
- Generic component supporting any HTML element type
- Props: `color`, `speed`, `as` (element type)
- Used on the INJECT FAILURE button

### `Sparkline` — Inline SVG Charts

Built into `App.tsx` — renders recent metric history as mini line charts:
- SVG gradient fill + polyline stroke
- Glowing dot on latest value
- Dynamic min/max scaling with division-by-zero protection

---

## Hooks

### `useWebSocket(url)` — WebSocket Connection Manager

Custom React hook for real-time MCP message streaming:

```typescript
const { messages, connected, send, clearMessages } = useWebSocket(wsUrl);
```

**Features:**
- **Auto-reconnect** — exponential backoff (1.5× multiplier, max 30s delay, 50 max attempts)
- **Keep-alive** — 25-second ping interval to prevent connection drops
- **Message validation** — requires `sender`, `channel`, `message_type` fields
- **Server ping filtering** — ignores keepalive pings and pong responses
- **Cleanup on unmount** — `mountedRef` flag prevents state updates after unmount

**MCPMessage Interface:**
```typescript
interface MCPMessage {
  message_id: string;
  incident_id: string;
  sender: string;
  recipient: string;
  message_type: string;
  channel: string;
  payload: any;
  confidence: number;
  evidence: string[];
  parent_message_id: string | null;
  timestamp: string;
}
```

---

## Design System

### Dark Ops Theme (`App.css`)

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#060a14` | Page background |
| `--bg2` | `#0b1120` | Card backgrounds |
| `--bg3` | `#111827` | Elevated surfaces |
| `--text` | `#f0f2f7` | Primary text |
| `--text2` | `#94a3b8` | Secondary text |
| `--text3` | `#4b5672` | Tertiary text |
| `--red` | `#ff4757` | Errors, alerts |
| `--green` | `#00ff88` | Success, healthy |
| `--blue` | `#00d2ff` | Info, Watcher |
| `--yellow` | `#ffa502` | Warnings, Triage |
| `--purple` | `#a855f7` | Deploy |
| `--cyan` | `#06b6d4` | Postmortem |
| `--pink` | `#e94560` | Orchestrator |

**Glass Morphism:** `rgba(255,255,255, 0.03–0.09)` backgrounds with `blur(20px–40px)` backdrop filters.

**Typography:** Inter (sans-serif) + JetBrains Mono (monospace).

**Agent Colors:**

| Agent | Color | Hex |
|-------|-------|-----|
| WatcherAgent | Cyan | `#00d2ff` |
| TriageAgent | Gold | `#ffd700` |
| DiagnosisAgent | Green | `#00ff88` |
| ResolutionAgent | Red | `#ff6b6b` |
| DeployAgent | Purple | `#a855f7` |
| PostmortemAgent | Cyan | `#06b6d4` |
| OrchestratorAgent | Pink | `#e94560` |

---

## Project Structure

```
frontend/
├── src/
│   ├── App.tsx                 # Main dashboard (state, layout, tabs, metrics)
│   ├── App.css                 # Dark ops theme, glass morphism, animations
│   ├── index.tsx               # React entry point
│   ├── index.css               # Global reset + dark baseline
│   ├── react-app-env.d.ts      # CRA type declarations
│   ├── components/
│   │   ├── Particles.tsx       # WebGL 3D particle background (OGL)
│   │   ├── Particles.css       # Particle canvas styling
│   │   ├── DecryptedText.tsx   # Text scramble/reveal animation (Motion)
│   │   ├── CountUp.tsx         # Spring-physics number counter (Motion)
│   │   ├── StarBorder.tsx      # Animated glowing border effect
│   │   └── StarBorder.css      # StarBorder styling
│   ├── hooks/
│   │   └── useWebSocket.ts     # WebSocket hook (auto-reconnect, backoff, keepalive)
│   └── types/
│       └── ogl.d.ts            # OGL module type declarations
├── public/
│   ├── index.html              # HTML shell
│   ├── manifest.json           # PWA manifest
│   └── robots.txt
├── package.json
├── package-lock.json
├── tsconfig.json               # TypeScript config (strict, ES5 target, react-jsx)
└── README.md
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REACT_APP_BACKEND_URL` | Backend REST API URL | `http://localhost:8080` |
| `REACT_APP_WS_URL` | Backend WebSocket URL | `ws://localhost:8080/ws` |

> **Important:** The backend runs on port **8091** by default, so set both variables accordingly.

---

## Scripts

| Command | Description |
|---------|-------------|
| `npm start` | Start dev server on `http://localhost:3000` |
| `npm run build` | Production build to `build/` (minified, optimized) |
| `npm test` | Run tests in interactive watch mode |
| `npm run eject` | Eject from CRA (one-way, not recommended) |

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| react | ^19.2.4 | UI framework |
| react-dom | ^19.2.4 | DOM renderer |
| typescript | ^4.9.5 | Static type checking |
| recharts | ^3.8.0 | Metric sparkline charts |
| lucide-react | ^0.577.0 | Agent icons & UI icons |
| motion | ^12.35.2 | Animations (CountUp, DecryptedText) |
| ogl | ^1.0.11 | WebGL 3D particle rendering |
| react-scripts | 5.0.1 | Build toolchain (CRA) |

---

## License

Part of [IncidentZero](../README.md) — **MIT License** © 2026 B M KIRAN
