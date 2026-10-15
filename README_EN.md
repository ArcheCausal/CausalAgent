[English](README_EN.md) | [简体中文](README.md)

<p align="center">
  <img src="./README/causalagent-mark.png" alt="CausalAgent brand mark" width="160">
</p>

<h1 align="center">
CausalAgent
</h1>

<p align="center">
  <em>Next-generation causal analysis agent</em>
</p>

<p align="center">
    <a href="#">
      <img src="https://img.shields.io/badge/Status-In%20Development-orange?style=flat-square" alt="Status">
    </a>
    <a href="#">
      <img src="https://img.shields.io/badge/Python-3.11+-blue?style=flat-square&logo=python&logoColor=white" alt="Python Version">
    </a>
    <a href="#">
      <img src="https://img.shields.io/badge/Focus-Causal%20Inference-green?style=flat-square" alt="Topic">
    </a>
    <a href="#">
      <img src="https://img.shields.io/badge/Powered%20by-Langgraph-8A2BE2?style=flat-square" alt="Powered By">
    </a>
  </p>

<br>

*CausalAgent provides a workspace for causal analysis on tabular data. Upload data, follow the analysis, and review results through interactive charts and reports.*

> [!IMPORTANT]
> CausalAgent includes a public website, a user workspace, an administrator console, and an isolated RAG evaluation workbench. Analysis results depend on the input data and the assumptions of each method.

## Table of Contents

- [Table of Contents](#table-of-contents)
- [What is CausalAgent](#what-is-causalagent)
- [Why CausalAgent](#why-causalagent)
- [Tech Stack](#tech-stack)
- [User Features](#user-features)
  - [User Showcase](#user-showcase)
  - [Agent Runtime](#agent-runtime)
  - [Deep Agent](#deep-agent)
  - [Core Capabilities](#core-capabilities)
- [Quick Start](#quick-start)
  - [Service URLs](#service-urls)
  - [Minimum Configuration](#minimum-configuration)
  - [Docker Deployment](#docker-deployment)
- [Administration and Development](#administration-and-development)
  - [Administrator Console](#administrator-console)
  - [RAG Workbench](#rag-workbench)
  - [Observability](#observability)
  - [User Frontend (Vue)](#user-frontend-vue)
  - [Backend Tests](#backend-tests)
  - [Windows Deployment](#windows-deployment)
- [Technical Documentation](#technical-documentation)
- [Contributing](#contributing)
- [Project Structure](#project-structure)
- [Star History](#star-history)

## What is CausalAgent

**A causal analysis application for tabular data.** A LangGraph parent graph coordinates preprocessing, the Deep Agent subgraph, tool calls, and report generation; an independent worker executes analysis jobs.

The workspace helps users explore causal structures in their data. Results depend on data quality and method assumptions, and do not replace research design, domain knowledge, or experimental validation.

CausalAgent can:

- Inspect and profile datasets.
- Run supported methods for causal structure discovery.
- Apply post-processing and quality checks to available results.
- Present results through interactive causal graphs and structured reports.

## Why CausalAgent

| Feature | Description |
| :--- | :--- |
| **Agent-driven** | A LangGraph parent graph automatically routes analysis nodes, tool stages, and subgraphs. |
| **Deep Agent analysis** | After pre-processing, a Deep Agent subgraph plans algorithm calls, retrieval tools, and result selection under a fixed budget. |
| **Dynamic causal graphs** | Instead of static images, CausalAgent renders interactive network graphs. You can drag nodes, zoom, and click to inspect details. |
| **MCP-based architecture** | Uses **Model Context Protocol (MCP)** to decouple core logic from tools, making it easy to extend new algorithms. |
| **RAG enhanced** | A domain-specific knowledge base for causal inference is integrated to ensure the generated reports are rigorous and well grounded. |

## Tech Stack

| Category | Components |
| :--- | :--- |
| **Agent and models** | LangGraph, Deep Agents, LangChain, MCP, OpenAI-compatible Chat / Embedding APIs |
| **RAG and search** | ChromaDB, BM25S, Ragas, SearXNG |
| **Backend and data** | Flask, MySQL, PostgreSQL, Alembic |
| **Frontend** | Vue 3, TypeScript, Pinia, Vite, vis-network, WebView2 |
| **Observability** | Grafana Alloy, Loki, Grafana |
| **Runtime and release** | Docker Compose, GitHub Actions |

## User Features

### User Showcase
The public website provides product information, documentation, and release notes. The user workspace is where users start analyses and review conversations and reports.

<p align="center">
  <img src="./README/website-home-2026-10.png" alt="CausalAgent public website home page" width="850">
</p>
<p align="center">
  <img src="./README/user-workspace-home-2026-10.png" alt="User workspace home page" width="850">
</p>
<p align="center">
  <img src="./README/user-analysis-report-2026-10.png" alt="Analysis report and charts in the user workspace" width="850">
</p>

### Agent Runtime

The parent graph orchestrates pre-processing, the Deep Agent subgraph, finalization checks, and the report node; an independent worker executes every job.

```mermaid
graph TD;
    User((User)) --> UI[Vue Chat App / Windows Client]
    UI --> API[Flask API / Analysis Job]
    API --> Worker[Job Worker / Slot]
    Worker --> Graph[LangGraph Parent Graph]
    Graph --> DeepAgent[Deep Agent Subgraph]
    DeepAgent --> MCP[MCP: PC / DirectLiNGAM / CDFM]
    DeepAgent --> RAG[RAG Knowledge Base]
    DeepAgent --> Search[Web Search / SearXNG]
    Graph --> Report[Post-processing / Report]
    Worker --> Events[(MySQL Job / SSE Events)]
    Graph <--> Checkpoint[(PostgreSQL Checkpoint)]
    DeepAgent <--> Memory[(PostgreSQL Long-term Memory)]
    Events --> UI
```

Users can enable Web Search per analysis job. If RAG or Web Search is temporarily unavailable, the corresponding subgraph returns a controlled degradation result so that the main analysis can continue.

### Deep Agent

After pre-processing, a Deep Agent subgraph decides which algorithms and retrieval tools to call and how to select among the results.

- **Explicit state boundary**: The parent graph projects only the question, data profile, analysis parameters, and required input messages into the subgraph. The subgraph returns only algorithm results, the tool-call ledger, RAG and Web Search evidence, structured decisions, and report summaries. Full messages, internal fields, and intermediate plans never flow back into the parent state.
- **Separate execution identity**: Parent and child graph share the PostgreSQL checkpointer but use different threads and namespaces, plus an execution scope built from the job, attempt, lease, and frozen input identity. A scope mismatch restarts from the minimal parent input instead of reusing earlier artifacts.
- **Tool orchestration**: Models see LangChain function tools generated from the local `AlgorithmSpec` registry. PC, DirectLiNGAM, and CDFM are enabled by default. Algorithm calls inside one model response are layered by `requires`/`produces`: independent calls run in parallel under per-job concurrency and tool-budget limits. The runtime never calls a remote `list_tools()` to extend the model tool set.
- **Long-term memory**: User preferences and research background live in a PostgreSQL Store that is separate from job checkpoints. The model may only write `/memories/preferences.md` and `/memories/research_background.md`; every other write is denied, and the cleanup worker removes the namespace when the user is deleted.
- **Bounded budget**: Model calls, tool calls, recursion depth, tool-node timeout, and per-job parallel tools all have fixed upper bounds; exceeding them follows a controlled failure path.
- **Finalization check**: The finalization gate calls no model. It verifies that references in the final decision exist, that algorithm results match the tool-call ledger, and that provenance belongs to the current job, attempt, lease, and frozen input. The first failed check returns the exact violated reference rule to the subgraph for one revision; a second failure degrades to a report based only on verified inputs, without publishing unverified selections.
- **Cancellation**: When a job heartbeat is lost or a cancel arrives, the worker stops waiting locally and sends a signed cancel over a dedicated control lane, which terminates only the targeted algorithm call.

### Core Capabilities

The overall pipeline of CausalAgent can be summarized as:

**Upload data → Pre-process & data health check → Causal structure learning → Post-processing & quality enhancement → Report & visualization.**

#### Pre-processing

- **Data overview**: Count rows, columns, and field names to quickly summarize table structure.
- **Column-level profiling & type inference**: Detect missing rate, unique values, constant columns; infer continuous, categorical, time-like, ID-like columns, and rate their suitability for causal analysis.
- **Quality diagnosis**: Summarize overall missingness, mark high-missing or constant columns, and identify problematic fields.
- **Visual summaries**: Histograms, boxplots, correlation heatmaps, etc., to reveal outliers and collinearity.

#### Causal Analysis (MCP)

- **Pluggable algorithm framework**: Through MCP, causal discovery and estimation algorithms are wrapped as "tools" and can be swapped or extended without changing the Agent logic.
- **Currently supported**:
  - PC algorithm for causal structure learning based on conditional independence tests.
  - DirectLiNGAM for linear, non-Gaussian, acyclic continuous data under its explicit model assumptions.
  - CDFM, a pretrained causal discovery foundation model, for zero-shot causal graph inference on continuous numeric tabular data in a single call that returns one candidate directed graph. The same checkpoint covers linear, nonlinear, heteroscedastic, ordinal or discrete, measurement-error, and latent-confounding mechanisms, so it is a better fit than assumption-specific tools when the mechanism is unknown or clearly nonlinear. Only an optional `threshold` is exposed to the model; when it is omitted the service uses the model's own calibrator to fix the decision boundary, while the checkpoint path, device, and standardization policy are fixed by the algorithm service. Missing values follow the NaN/±Inf mask rules and categorical variables are not accepted. The output contains edges and directions only, without weights or confidence values: directions can change with the threshold, evidence for direction is weak in non-identifiable regimes, and two opposing edges may indicate a latent confounder; the result is this model inference only, without any accuracy or production-capability claim.
- **Kept but disabled by default**: The OLC implementation (specification, adapter, runner, and algorithm code) remains in the repository but is not part of the default tool surface.
- **Planned**:
  - FCI and other algorithms with latent confounders.
  - Causal effect estimation (ATE/CATE) and counterfactual analysis.

#### Knowledge Base (RAG)

- **Runtime release**: The repository provides a version-controlled active release. Production queries load index and embedding identity from its manifest and require a matching Embedding API configuration.
- **Hybrid retrieval**: ChromaDB dense retrieval and BM25S sparse retrieval support multimodal PDF, text, table, and image ingestion.
- **Knowledge sources**: Books and papers on causal inference (PDF/TXT), covering graphical models, intervention analysis, IV, panel causal analysis, etc.
- **Typical abilities**:
  - Retrieve relevant theory when generating reports to provide academic background.
  - Explain fundamental concepts for beginners (e.g., "What is a confounder?").

If the active release, Embedding API, or worker readiness check is invalid, the Agent marks RAG unavailable and degrades safely. The standalone RAG workbench manages source ingestion, staged indexes, evaluation, and controlled release publishing.

#### Web Search

The optional Web Search subgraph uses SearXNG to aggregate academic results from arXiv, Crossref, and OpenAlex. It exposes a bounded set of references in reports and degrades without aborting the main analysis when planning, search, or parsing fails.

#### Post-processing

- **Cycle detection and repair**: Check whether the learned graph violates the DAG assumption; if cycles appear, LLM-assisted suggestions are used to break edges.
- **Edge evaluation and confidence analysis**: Evaluate strength or confidence of each edge, mark suspicious ones, and provide adjustment suggestions.
- **Business constraints integration**: Allow domain priors such as "A cannot be caused by B" to refine the final graph.

#### Report Generation

- **Automatic structured reports**: Generate reports with sections like background, data overview, methods, findings, conclusions, and limitations.
- **Structured report rendering**: The model produces sections, Markdown text, and resource references only, while chart data, causal graph models, sources, and evidence are injected and validated by the backend. The chat app renders sections, charts, and causal graphs by block type and shows a controlled notice for unknown references instead of breaking the page.
- **Interactive causal graph**: Use frontend components such as vis-network to render a graph that supports drag, zoom and click.

## Quick Start

### Service URLs

After the default development Compose stack starts, use these entry points:

| Function | URL | Purpose |
| --- | --- | --- |
| Website | [http://127.0.0.1:5001/](http://127.0.0.1:5001/) | Product pages, public docs, changelog, and sign-in/sign-up entries |
| Sign-in | [http://127.0.0.1:5001/auth/sign-in](http://127.0.0.1:5001/auth/sign-in) | Shared sign-in entry that returns each account to its permitted area |
| User workspace | [http://127.0.0.1:5001/dashboard](http://127.0.0.1:5001/dashboard) | Upload data, start analyses, and view reports after signing in |
| RAG workbench | [http://127.0.0.1:5001/rag-eval](http://127.0.0.1:5001/rag-eval) | Source ingestion, staged indexes, evaluation, and release management; administrators only |
| Admin console | [http://127.0.0.1:5001/admin/database](http://127.0.0.1:5001/admin/database) | Default entry for authenticated administrators |
| Grafana | [http://127.0.0.1:3000/grafana/](http://127.0.0.1:3000/grafana/) | Log search and dashboards |

The website, the user workspace, the RAG workbench, and the admin console are four separately built frontends. The RAG workbench is an isolated build, evaluation, and release workspace and requires the `rag_eval.access` permission; it is not the same as RAG queries inside the normal chat flow.

### Minimum Configuration

Copy [`.env.example`](.env.example), then configure the capabilities you plan to enable. Never commit real passwords or API keys:

- Base services: `SECRET_KEY`, the MySQL accounts, and `CHECKPOINT_POSTGRES_PASSWORD`.
- Chat model: `API_KEY`, `BASE_URL`, and `MODEL`.
- Deep Agent: `DEEP_AGENT_MODEL`, `DEEP_AGENT_BASE_URL`, `DEEP_AGENT_API_KEY`, and `DEEP_AGENT_CONTEXT_WINDOW_TOKENS`; the context window must be set explicitly, because there is no silent fallback.
- Algorithm service: `CAUSAL_MCP_SERVICE_TOKEN` and `CAUSAL_MCP_SIGNING_KEY_CURRENT`, plus `CAUSAL_MCP_SIGNING_KEY_PREVIOUS` during key rotation; `CDFM_MODEL_PATH` selects the CDFM checkpoint.
- RAG queries: `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, and `EMBEDDING_MODEL`; these must match the active release manifest.
- Log UI: `GRAFANA_ADMIN_PASSWORD`.
- Web Search: Compose defaults to `WEB_SEARCH_PROVIDER=searxng` and its internal `SEARXNG_URL`.

See [`Document/development/setup.md`](Document/development/setup.md) and [`Document/development/deployment.md`](Document/development/deployment.md) for the full configuration and runtime boundaries.

### Docker Deployment

The multi-stage `Dockerfile` builds the ordinary-user Vue app and the administrator Vue app with Node 24 first, then produces a Python runtime image that ships only the runtime and static artifacts.

1. Clone the repository:

   ```bash
   git clone https://github.com/ArcheCausal/CausalAgent
   cd CausalAgent
   ```

2. Create and edit the environment file:

   ```bash
   cp .env.example .env
   ```

3. Start the services:

   ```bash
   docker compose up -d
   ```

   The one-shot `db-bootstrap` service initializes the MySQL schema through
   Alembic and initializes the LangGraph PostgreSQL checkpoint schema before
   the application services start.

> [!IMPORTANT]
> The repository includes a formal active RAG release, but the deployment must provide the matching Embedding API configuration. If readiness fails, the worker still starts and safely marks RAG unavailable.

## Administration and Development

The preceding sections focus on end users and first-time setup. This section collects administrator and developer entry points.

### Administrator Console

<p align="center">
  <img src="./README/admin-database-dashboard-2026-10.png" alt="Administrator database dashboard main panel" width="850">
</p>

The administrator console covers users, sessions, jobs, files, database status, collection settings, and audit workflows. Complete security, API, deployment, and test boundaries are indexed in [`Document/admin/`](Document/admin/README.md).

### RAG Workbench

<p align="center">
  <img src="./README/rag-workbench-2026-10.png" alt="Isolated knowledge-source workbench" width="850">
</p>

The isolated workbench supports knowledge-source ingestion, staged index construction, retrieval and Ragas evaluation, and controlled release publishing. Access requires the rag_eval.access permission. See [Document/architecture/rag-evaluation.md](Document/architecture/rag-evaluation.md) for its workflow and release boundaries.

### Observability

```text
app / worker / monitor / MCP / RAG worker
    -> structured JSON logs
    -> Grafana Alloy
    -> Loki
    -> Grafana dashboards
```

Runtime events are controlled by the event catalog and correlated with request, job, session, and worker-slot fields. Raw prompts, file contents, API keys, tokens, and cookies must not enter logs. See [`Document/development/observability.md`](Document/development/observability.md) for event, noise-control, and privacy rules.

### Frontends (Vue)

Four standalone Vue 3 + TypeScript projects run on the same origin and none of them belongs to a root npm workspace. Flask serves each build output from its own path prefix: the website in `website-frontend/` (`/site-assets/`), the user workspace in `chat-frontend/` (`/dashboard-assets/`), the RAG workbench in `app/rag_eval/frontend/` (`/rag-eval/`), and the admin console in `admin-frontend/` (`/admin/`). When a build output is missing, that frontend's page entry returns 503 with a request ID instead of falling back to another frontend or serving partial assets.

Start the local development servers from the repository root with a single command; the script opens one window per selected frontend and runs Vite there:

```powershell
.\scripts\dev_frontends.ps1                          # all four frontends
.\scripts\dev_frontends.ps1 -Frontends website,chat  # website and chat only
.\scripts\dev_frontends.ps1 -Frontends rag -Install  # run npm ci first
```

The script uses PowerShell and npm only. `-WhatIf` prints the planned operations, `-Install` runs `npm ci` first, and single-frontend work can still use `npm ci` and `npm run dev` inside that directory. Ports, development URLs, and skip conditions are documented in [`Document/development/setup.md`](Document/development/setup.md).

`npm run dev` only starts Vite on port 5174 and proxies API calls to `http://127.0.0.1:5001`. Setting `CHAT_VITE_DEV_SERVER_URL=http://127.0.0.1:5174` makes Flask redirect `/` to Vite while keeping the `next` query parameter. Quality gates and the production build run `npm run lint`, `npm run typecheck`, `npm run test:unit`, `npm run test:components`, `npm run test:e2e:mock`, and `npm run build`, or all of them through `npm run check`. Mock E2E uses simulated APIs only and is not evidence of real Flask, worker, or model acceptance.

See [`Document/development/chat-frontend.md`](Document/development/chat-frontend.md) for the detailed state constraints.

### Backend Tests

The one-shot unit-test service runs the supported backend unit scope without MySQL or network access:

```bash
docker compose -f docker-compose.test.yml build unit-test
docker compose -f docker-compose.test.yml run --rm unit-test
```

Integration, database, browser, Web Search, observability, and RAG validation are separate evidence layers; see [`tests/README.md`](tests/README.md) and [`Document/development/testing.md`](Document/development/testing.md).

### Windows Deployment

The MVP Windows client is a WebView2 shell. It loads the same deployed CausalAgent web page as a browser; it does not package Flask, MySQL, workers, models, the knowledge base, or a second frontend. The server must already provide the HTTPS page, same-origin Cookie Session, API, SSE, and file endpoints.

1. Install **CPython 3.12** and the Microsoft Edge WebView2 Runtime.
2. Create the independent desktop environment:

   ```bash
   python -m venv .venv-desktop
   .venv-desktop\Scripts\python.exe -m pip install -r windows-client\requirements-desktop.txt
   ```

3. Check the desktop prerequisites:

   ```bash
   .venv-desktop\Scripts\python.exe Run_causal.py --check-environment
   ```

4. Start the existing Flask backend, then start the desktop shell. Development mode defaults to `http://127.0.0.1:5001/` and also permits `http://localhost:5001/`:

   ```bash
   .venv-desktop\Scripts\python.exe Run_causal.py --url http://127.0.0.1:5001/
   ```

The URL precedence is command-line `--url`, then `CAUSALAGENT_DESKTOP_URL`, then the development default. A frozen release package embeds its HTTPS origin, forces Edge Chromium, and disables debug/devtools. See [`windows-client/README.md`](windows-client/README.md) for packaging and smoke validation.

## Technical Documentation

The root README provides the project overview and common entry points. Current technical facts are organized through [`Document/README.md`](Document/README.md):

- System, Agent, and RAG architecture: [`Document/architecture/`](Document/architecture/overview.md)
- User and RAG APIs: [`Document/api/`](Document/api/conventions.md)
- Databases, migrations, and checkpoints: [`Document/database/`](Document/database/overview.md)
- Setup, testing, deployment, and observability: [`Document/development/`](Document/development/setup.md)
- Administrator module: [`Document/admin/`](Document/admin/README.md)

## Contributing

Contributions via Issues and Pull Requests are welcome.

1. Fork the repository.
2. Create a branch from `develop`, for example `feat(rag)/cache`.
3. Use `keyword(function):description` for commit and Pull Request titles.
4. Open the Pull Request against `develop`; only `develop` opens merge requests into `main`.
5. Wait for the Python syntax, light-test, and pull-request-policy checks before merging.

Supported keywords include `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`, and `revert`.

## Project Structure

```text
.
├── CausalAgent.py              # Flask web entrypoint
├── Run_causal.py               # Windows WebView2 entrypoint
├── app/                        # Web, auth, jobs, admin, and RAG workbench
│   ├── agent/                  # Job API, SSE, and worker runtime
│   └── rag_eval/               # Isolated ingestion, evaluation, and release
├── Agent/                      # LangGraph, causal tools, and knowledge base
│   ├── causal_agent/           # Parent graph, nodes, routing, and subgraphs
│   ├── deep_agent/             # Deep Agent subgraph, memory, and finalization
│   ├── deep_agent_tools/       # Algorithm specs, adapters, tools, and events
│   ├── CausalAgentMCP/         # MCP causal algorithm server
│   └── knowledge_base/         # RAG runtime, multimodal indexes, and evaluation
├── Database/                   # MySQL, PostgreSQL, migrations, and monitoring
├── observability/              # Structured logging, event catalog, and Alloy
├── searxng/                    # Web Search configuration and initialization
├── website-frontend/           # Vue website frontend (public pages and auth)
├── chat-frontend/              # Vue ordinary-user app frontend (/dashboard)
├── admin-frontend/             # Vue administrator frontend (/admin)
├── windows-client/             # Windows client, build, and smoke tests
├── config/                     # Application and RAG path configuration
├── deploy/                     # Staging and production resources
├── scripts/                    # Development, release, acceptance, and diagnostics
├── Document/                   # Current technical documentation
├── tests/                      # Unit, integration, E2E, and smoke tests
├── docker-compose*.yml         # Development, test, staging, and production stacks
└── .github/workflows/          # CI and Windows release workflows
```

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=ArcheCausal/CausalAgent&type=Date)](https://star-history.com/#ArcheCausal/CausalAgent&Date)

