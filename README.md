<div align="center">

# 🚨 IncidentZero

### Autonomous AI SRE Team — Multi-Agent Incident Resolution

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o-412991?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)
[![TypeScript](https://img.shields.io/badge/TypeScript-4.9-3178C6?style=for-the-badge&logo=typescript&logoColor=white)](https://typescriptlang.org)

**Six AI agents collaborate in real time to detect, diagnose, debate, fix, deploy, and document production incidents — all with zero human intervention.**

[Quick Start](#-quick-start) · [Architecture](#-architecture) · [How It Works](#-how-it-works) · [Dashboard](#-dashboard-features) · [API Reference](#-api-reference)

</div>

---

## 📖 Overview

**IncidentZero** is an autonomous site reliability engineering (SRE) system that demonstrates how a team of specialized AI agents can collaboratively resolve production incidents end-to-end. A chaos-injectable target application simulates real production failures (connection pool leaks), and a cinematic React dashboard provides full visibility into the multi-agent workflow as it unfolds.

### ✨ Key Features

- **🤖 6 Specialized AI Agents** — Each with a distinct role in the incident lifecycle
- **⚔️ Devil's Advocate Debate** — ResolutionAgent challenges DiagnosisAgent's findings before generating fixes
- **📡 Real-Time Dashboard** — WebSocket + REST polling with WebGL particle effects
- **🔧 Autonomous Fix & Deploy** — Generates code fixes, applies them, and creates GitHub PRs
- **📋 Auto-Generated Postmortems** — Comprehensive incident reports with timeline, root cause, and recommendations
- **🎯 Chaos Engineering** — Injectable connection pool leak for realistic failure simulation
- **🧠 Multi-LLM Support** — Azure OpenAI, OpenAI, or fully functional mock fallback (no API keys required)
- **📊 MCP Message Protocol** — Structured inter-agent communication with full observability

---

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  FRONTEND (React 19)                     │
│  Real-time dashboard · WebSocket + API polling · WebGL   │
└────────────────────────┬─────────────────────────────────┘
                         │ REST + WebSocket
┌────────────────────────┴─────────────────────────────────┐
│              BACKEND (FastAPI · Port 8091)                │
│                                                          │
│  OrchestratorAgent ──── state machine & coordination     │
│   ├─ WatcherAgent        continuous health monitoring    │
│   ├─ TriageAgent         severity classification (P0–P3) │
│   ├─ DiagnosisAgent      root cause analysis + evidence  │
│   ├─ ResolutionAgent     devil's advocate debate + fix   │
│   ├─ DeployAgent         apply fix + GitHub PR creation  │
│   └─ PostmortemAgent     comprehensive incident report   │
│                                                          │
│  MCP Bus ──── inter-agent message routing + WS broadcast │
│  LLM Service ──── Azure OpenAI / OpenAI / Mock fallback │
└────────────────────────┬─────────────────────────────────┘
                         │ httpx (async)
┌────────────────────────┴─────────────────────────────────┐
│           TARGET APP (FastAPI · Port 8000)                │
│     Task CRUD · Connection pool · Chaos endpoints        │
└──────────────────────────────────────────────────────────┘
```

---

## ⚙ How It Works

### Incident Lifecycle

The **OrchestratorAgent** drives every incident through a strict state machine:

```
DETECTED → TRIAGING → DIAGNOSING → DEBATING → RESOLVING → DEPLOYING → RESOLVED
```

| Step | Agent | Action |
|------|-------|--------|
| 1 | 👁 **WatcherAgent** | Polls `/health` and `/metrics` every 5s, detecting anomalies like error-rate spikes or connection pool exhaustion. Requires 2 consecutive anomalies before alerting. |
| 2 | 🎛 **OrchestratorAgent** | Creates incident (`INC-YYYYMMDD-HHMMSS`), advances state machine, coordinates all agents. |
| 3 | 🔺 **TriageAgent** | Classifies severity (P0–P3), determines blast radius, evaluates auto-resolve eligibility via LLM. |
| 4 | 🔍 **DiagnosisAgent** | Collects live evidence (health, metrics, chaos status, synthetic requests) and performs root cause analysis. |
| 5 | ⚡ **ResolutionAgent** | Plays devil's advocate — challenges the diagnosis in structured debate (up to 2 rounds, 10s timeout per round), then generates a minimal, safe code fix with rollback plan. |
| 6 | 🚀 **DeployAgent** | Applies the fix to the target app, verifies health recovery (up to 5 retries), optionally opens a GitHub PR. |
| 7 | 📋 **PostmortemAgent** | Produces a full incident report: timeline, root cause, debate highlights, impact, lessons learned, and prevention recommendations. |

> **Demo pacing:** In the Azure backend `run_incident` flow, each major stage is intentionally delayed by ~2 seconds so the dashboard timeline animates step-by-step (Detection → Triage → Diagnosis → Debate → Resolution → Deploy → Postmortem).

### ⚔️ The Debate Protocol

IncidentZero's key innovation is the **structured adversarial debate** between ResolutionAgent and DiagnosisAgent, ensuring diagnostic rigor before any fix is applied:

```
DiagnosisAgent                     ResolutionAgent
      │                                  │
      ├── ANALYSIS (root cause) ────────→│
      │                                  │
      │←── CHALLENGE (questions +        │
      │     critique + alt hypotheses) ──┤
      │                                  │
      ├── EVIDENCE (DEFEND with proof    │
      │    or REVISE with new analysis) →│
      │                                  │
      │←── CONSENSUS (acceptance) ───────┤
      │                                  │
      ▼                                  ▼
          → Fix generation proceeds
```

- **ResolutionAgent** evaluates the diagnosis and issues a **CHALLENGE** with specific questions and alternative hypotheses
- **DiagnosisAgent** responds with **EVIDENCE** — either **DEFEND** (with additional proof) or **REVISE** (with updated analysis)
- After up to **2 debate rounds**, ResolutionAgent reaches **CONSENSUS** and proceeds to generate the fix
- All debate messages are **broadcast in real time** to the frontend via WebSocket

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|------------|
| **Frontend** | React 19, TypeScript 4.9, CSS3 (dark theme) |
| **UI Effects** | OGL (WebGL particles), Motion/Framer Motion (animations), Recharts (sparklines), Lucide (icons) |
| **Backend** | Python 3.10+, FastAPI 0.104, Uvicorn 0.24, httpx 0.27 |
| **AI / LLM** | OpenAI SDK 1.12 — Azure OpenAI / OpenAI / Mock fallback |
| **Communication** | WebSocket (real-time push), REST API (reliable polling fallback), MCP message protocol |
| **Target App** | FastAPI (chaos-injectable task manager with simulated connection pool) |
| **CI/CD** | Optional GitHub integration for automated PR creation |

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- (Optional) OpenAI API key or Azure OpenAI credentials — the system **works fully with mock LLM responses**

### 1. Clone the Repository

```bash
git clone https://github.com/kiran797979/IncidentZero.git
cd IncidentZero
```

### 2. Start the Target App

```bash
cd target-app
pip install -r requirements.txt
python app.py
```

> Runs on `http://localhost:8000`

### 3. Start the Backend

```bash
cd backend
pip install -r requirements.txt
python main.py
```

> Runs on `http://localhost:8091`

### 4. Start the Frontend

```bash
cd frontend
npm install

# Point frontend at backend (Windows)
set REACT_APP_BACKEND_URL=http://localhost:8091
set REACT_APP_WS_URL=ws://localhost:8091/ws
npm start
```

> **Linux / macOS:** Use `export` instead of `set`.
>
> Dashboard opens at `http://localhost:3000`

### 5. Trigger an Incident

Click the **🔴 INJECT FAILURE** button on the dashboard, or:

```bash
curl -X POST http://localhost:8091/api/inject
```

This activates a connection pool leak in the target app. Within seconds, the WatcherAgent detects the anomaly and the full autonomous resolution pipeline kicks in — triage → diagnosis → debate → fix → deploy → postmortem.

---

## 🖥 Dashboard Features

The React frontend provides a cinematic, real-time view of the entire incident lifecycle:

| Feature | Description |
|---------|-------------|
| **WebGL Particle Background** | 200 animated OGL particles with mouse interaction, sine-wave motion, and dynamic colors |
| **LIVE / OFFLINE Badge** | Dual-signal status (WebSocket + API polling with 3s debounce to prevent flicker) |
| **Incident Status Badge** | Current pipeline stage with color-coded state transitions |
| **Agent Pipeline** | Step-by-step visualization of all 6 agents with active/completed indicators |
| **Metrics Panel** | Error rate, active connections, response time, and message count with animated CountUp and sparklines |
| **MTTR Timer** | Live elapsed-time counter during active incidents |
| **Timeline Tab** | Reverse-chronological MCP message feed with agent icons and color-coded channels |
| **Debate Tab** | Full devil's advocate exchange — challenges, evidence bullets, and consensus with confidence scores |
| **Postmortem Tab** | Formatted incident report with executive summary, root cause, impact, and recommendations |
| **DecryptedText** | Animated text scramble/reveal effect for page titles |
| **StarBorder Button** | Glowing animated border on the INJECT FAILURE button |
| **Celebration Animation** | Confetti-style particle burst on incident resolution |
| **Flash Effect** | Screen flash on incident detection |

> **Reliability:** Data is sourced from both WebSocket (real-time push) and REST API polling (fallback every 3s during incidents, 10s idle) to ensure no messages are lost during WebSocket reconnection cycles.

---

## ☁️ Azure Backend Deploy

To publish the Azure Functions backend after backend changes:

```bash
cd azure-backend
func azure functionapp publish incidentzero-backend
```

> Ensure your local Python version matches the Azure Function App runtime version to avoid runtime dependency issues.

---

## 🤖 Agent Details

### 👁 WatcherAgent — Continuous Monitor
- Polls `/health` and `/metrics` every 5 seconds via async HTTP
- Runs synthetic GET requests to measure real error rates and latency
- Detects anomalies: error rate spikes, connection pool exhaustion, response time degradation
- Requires **2 consecutive anomalies** before triggering an alert (prevents false positives)
- Publishes on `monitoring.status` channel

### 🔺 TriageAgent — Severity Classifier
- Classifies severity: **P0** (critical outage) → **P1** (high) → **P2** (medium) → **P3** (low)
- Determines blast radius percentage and affected endpoints
- Uses LLM for evaluation with local fallback classification
- Publishes on `incident.triage` channel

### 🔍 DiagnosisAgent — Root Cause Analyst
- Gathers live evidence from target app: `/health`, `/metrics`, `/chaos/status`, synthetic requests
- Performs local analysis: connection pool trends, error rate correlation, chaos status
- Uses LLM for root cause categorization (category, mechanism, component)
- **Responds to debate challenges** with fresh evidence and updated analysis
- Publishes on `incident.diagnosis` and `incident.debate` channels

### ⚡ ResolutionAgent — Devil's Advocate & Fix Generator
- Critically evaluates diagnosis before generating any fix
- Issues **CHALLENGE** messages with questions, critique, and alternative hypotheses
- Waits up to 10 seconds for DiagnosisAgent's response per round
- After debate conclusion (max 2 rounds), generates minimal, safe code fix
- Produces unified diff, rollback plan, validation steps, and risk assessment
- Publishes on `incident.debate` and `incident.resolution` channels

### 🚀 DeployAgent — Deployment & PR Creation
- Calls target app `/chaos/fix` endpoint to apply the fix
- Verifies health recovery with retries (max 5 attempts, 2s delay between checks)
- Creates GitHub PR with fix details (when `GITHUB_TOKEN` is configured)
- Publishes on `incident.deployment` channel

### 📋 PostmortemAgent — Report Generator
- Generates comprehensive markdown postmortems via LLM (with fallback)
- Report sections: executive summary, incident overview, timeline, root cause, debate highlights, resolution details, impact assessment, lessons learned, prevention recommendations, agents involved
- Publishes on `incident.postmortem` channel

---

## 📡 MCP Message Protocol

All inter-agent communication uses the **Model Context Protocol (MCP)** message format:

```json
{
  "message_id": "a1b2c3d4",
  "incident_id": "INC-20260310-120000",
  "sender": "DiagnosisAgent",
  "recipient": "OrchestratorAgent",
  "message_type": "diagnosis_complete",
  "channel": "incident.diagnosis",
  "payload": { "root_cause": "...", "mechanism": "..." },
  "confidence": 0.88,
  "evidence": ["error_rate_spike_to_45%", "pool_utilization_95%"],
  "parent_message_id": null,
  "timestamp": "2026-03-10T12:00:00Z"
}
```

### Message Channels

| Channel | Source Agent | Purpose |
|---------|-------------|---------|
| `monitoring.status` | WatcherAgent | Real-time metrics (error rate, connections, response time) |
| `incident.detection` | WatcherAgent | Anomaly alert with detection details |
| `incident.triage` | TriageAgent | Severity classification (P0–P3) and blast radius |
| `incident.diagnosis` | DiagnosisAgent | Root cause analysis and evidence |
| `incident.debate` | Resolution / Diagnosis | Challenge, evidence, and consensus messages |
| `incident.resolution` | ResolutionAgent | Code fix proposal with diff and rollback plan |
| `incident.deployment` | DeployAgent | Deployment result, health verification, PR URL |
| `incident.postmortem` | PostmortemAgent | Final comprehensive incident report |
| `system.status` | OrchestratorAgent | System-level status updates |

### Message Types

`alert` · `analysis` · `proposal` · `challenge` · `evidence` · `consensus` · `action` · `status`

---

## 📚 API Reference

### Backend API (Port 8091)

#### System & Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | System info and agent list |
| `GET` | `/api/health` | Health check (incident count, WS connections, target URL) |
| `GET` | `/api/status` | Full system status with target-app health proxy |
| `WebSocket` | `/ws` | Real-time MCP message stream (30s keepalive ping) |

#### Incident Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/inject` | Inject chaos failure into target app |
| `POST` | `/api/fix` | Manually trigger fix on target app |
| `GET` | `/api/incidents` | List all active + resolved incidents |
| `GET` | `/api/incidents/{id}` | Single incident details |
| `GET` | `/api/incidents/{id}/messages` | All MCP messages for an incident |
| `GET` | `/api/incidents/{id}/debate` | Debate messages for an incident |
| `GET` | `/api/messages` | Last 50 MCP messages (debug) |

#### Target App Proxies

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/target/health` | Proxy to target app `/health` |
| `GET` | `/api/target/metrics` | Proxy to target app `/metrics` |

### Target App API (Port 8000)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health status (healthy/degraded based on pool utilization) |
| `GET` | `/metrics` | Pool utilization %, task count, bug status |
| `GET` | `/tasks` | List all tasks |
| `POST` | `/tasks` | Create a new task (uses connection pool) |
| `DELETE` | `/tasks/{id}` | Delete a task |
| `POST` | `/chaos/inject` | Activate the connection pool leak bug |
| `POST` | `/chaos/fix` | Deactivate bug and reset connection pool |
| `GET` | `/chaos/status` | Current bug state and pool utilization |
| `POST` | `/chaos/generate-load` | Generate 10 synthetic requests to exhaust pool faster |

---

## 🐛 The Target App Bug

The target app (`target-app/app.py`) is a task manager backed by a simulated connection pool of **20 connections**. When chaos is injected:

```python
# When BUG_INJECTED = True:
if random.random() < 0.3:   # only 30% chance to release
    pool.release(conn)
# → 70% of connections leak, pool exhausts rapidly
```

**What happens:**
1. **70% of requests** fail to release their connection back to the pool
2. The pool rapidly exhausts → `/health` reports `degraded` status
3. New requests fail with connection timeout errors
4. Error rate spikes and response times degrade significantly

This simulates a real-world connection pool leak — the class of production bug that IncidentZero autonomously detects, diagnoses, debates, fixes, and documents.

---

## 🔐 Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TARGET_APP_URL` | Target application base URL | `http://localhost:8000` |
| `PORT` | Backend server port | `8091` |
| `POLLING_INTERVAL_SECONDS` | Watcher polling frequency (seconds) | `5` |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | — |
| `AZURE_OPENAI_KEY` | Azure OpenAI API key | — |
| `AZURE_OPENAI_DEPLOYMENT` | Azure OpenAI deployment name | `gpt-4o` |
| `AZURE_OPENAI_API_VERSION` | Azure OpenAI API version | `2024-08-06` |
| `OPENAI_API_KEY` | OpenAI API key (fallback provider) | — |
| `GITHUB_TOKEN` | GitHub Personal Access Token for PR creation | — |
| `GITHUB_REPO` | GitHub repository (`owner/name`) | — |
| `GITHUB_REPO_OWNER` | GitHub repository owner (alternative) | — |
| `GITHUB_REPO_NAME` | GitHub repository name (alternative) | `incidentzero` |
| `REACT_APP_BACKEND_URL` | Frontend: backend REST API URL | `http://localhost:8080` |
| `REACT_APP_WS_URL` | Frontend: backend WebSocket URL | `ws://localhost:8080/ws` |

> **Important:** Set `REACT_APP_BACKEND_URL=http://localhost:8091` and `REACT_APP_WS_URL=ws://localhost:8091/ws` when running the frontend, since the backend defaults to port **8091**.

**LLM Provider Auto-Detection:** Azure OpenAI → OpenAI → Mock fallback. **No API keys are required** — the mock provider generates realistic responses for the full pipeline including debate.

---

## 🧪 Running Tests

```bash
cd backend

# Unit tests (no target app required)
python test_llm.py     # LLM service & mock provider tests
python test_mcp.py     # MCP protocol serialization & channel tests

# Integration test (start target app on port 8000 first)
python test_agents.py  # Full incident lifecycle: detect → resolve
```

---

## 📁 Project Structure

```
IncidentZero/
├── backend/
│   ├── main.py                 # FastAPI server, REST API, WebSocket endpoint
│   ├── config.py               # Environment configuration & LLM provider detection
│   ├── agents/
│   │   ├── base_agent.py       # Abstract base class (MCP message publishing)
│   │   ├── orchestrator.py     # Master coordinator & incident state machine
│   │   ├── watcher.py          # Continuous health monitor & anomaly detection
│   │   ├── triage.py           # Severity classification (P0–P3)
│   │   ├── diagnosis.py        # Root cause analysis & evidence collection
│   │   ├── resolution.py       # Devil's advocate debate + code fix generation
│   │   ├── deploy.py           # Fix deployment + GitHub PR creation
│   │   └── postmortem.py       # Comprehensive incident report generator
│   ├── mcp/
│   │   ├── protocol.py         # MCPMessage dataclass, MessageType enum, serialization
│   │   └── channel.py          # Message bus routing, subscriptions & WS broadcast
│   ├── services/
│   │   └── llm.py              # LLM provider abstraction (Azure/OpenAI/Mock)
│   ├── test_agents.py          # Integration test: full incident lifecycle
│   ├── test_llm.py             # LLM service unit tests
│   ├── test_mcp.py             # MCP protocol unit tests
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx             # Main dashboard: metrics, pipeline, timeline, debate, postmortem
│   │   ├── App.css             # Dark theme styling, animations & responsive layout
│   │   ├── index.tsx           # React entry point
│   │   ├── index.css           # Global styles
│   │   ├── components/
│   │   │   ├── Particles.tsx   # WebGL particle background (OGL)
│   │   │   ├── DecryptedText.tsx  # Animated text scramble/reveal effect
│   │   │   ├── CountUp.tsx     # Animated number counter with spring physics
│   │   │   └── StarBorder.tsx  # Glowing animated border effect
│   │   ├── hooks/
│   │   │   └── useWebSocket.ts # WebSocket hook with auto-reconnect & exponential backoff
│   │   └── types/
│   │       └── ogl.d.ts        # OGL type declarations
│   ├── public/
│   │   └── index.html
│   ├── package.json
│   └── tsconfig.json
├── target-app/
│   ├── app.py                  # Task manager API with injectable connection pool bug
│   └── requirements.txt
├── docs/                       # Documentation assets
├── LICENSE                     # MIT License
└── README.md
```

---

## 📦 Dependencies

### Backend

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.104.1 | Async web framework |
| uvicorn | 0.24.0 | ASGI server |
| websockets | 12.0 | WebSocket support |
| openai | 1.12.0 | LLM provider SDK |
| httpx | 0.27.0 | Async HTTP client |
| python-dotenv | 1.0.0 | Environment configuration |

### Frontend

| Package | Version | Purpose |
|---------|---------|---------|
| react | ^19.2.4 | UI framework |
| typescript | ^4.9.5 | Type safety |
| recharts | ^3.8.0 | Metric sparklines |
| lucide-react | ^0.577.0 | Agent & UI icons |
| motion | ^12.35.2 | Animations (DecryptedText, CountUp) |
| ogl | ^1.0.11 | WebGL particle rendering |

### Target App

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.104.1 | Chaos-injectable API |
| uvicorn | 0.24.0 | ASGI server |
| httpx | 0.27.0 | HTTP client |

---

## 🤝 Contributing

Contributions are welcome! Feel free to:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

**© 2026 B M KIRAN**

---

<div align="center">

**Built with ❤️ for the future of autonomous SRE**

*IncidentZero — Because the best incident response is the one that happens before you wake up.*

</div>
