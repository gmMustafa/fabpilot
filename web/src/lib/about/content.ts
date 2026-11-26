/**
 * The About page's content, as DATA: updating the page later (a new phase done, a stack
 * change) means editing these lists, not JSX. Keep it in step with CLAUDE.md and PROGRESS.md.
 */

export const PITCH = {
  question: "Why did Line 3 stop last night, and what should I check?",
  summary:
    "FabPilot is an AI maintenance copilot for factories. A maintenance engineer asks a question in plain language; an agent reads the machine data, the alarm log and the maintenance documents, proposes a root cause with evidence and citations, and drafts a work order. Nothing is saved until a person approves it.",
};

export const FLOW = [
  { title: "Pull the data", text: "Sensor readings and the alarm log of the machines involved." },
  {
    title: "Detect anomalies",
    text: "Rolling statistics (z-scores) against each sensor's normal band.",
  },
  {
    title: "Search the knowledge base",
    text: "Manuals, SOPs and past incident reports (hybrid RAG).",
  },
  {
    title: "Propose a root cause",
    text: "With evidence and citations, or “insufficient data” instead of a guess.",
  },
  {
    title: "Draft a work order",
    text: "Actions, parts and priority, based on the cited procedures.",
  },
  {
    title: "Wait for approval",
    text: "A person approves, edits or rejects it before anything is saved.",
  },
] as const;

export type Layer = { name: string; path: string; parts: string[]; link?: string };

/** Top to bottom, as the request travels. `link`: the protocol to the NEXT layer. */
export const ARCHITECTURE: Layer[] = [
  {
    name: "Dashboard",
    path: "web/",
    parts: ["Plant overview", "Sensor charts", "Live agent steps", "Approval queue", "LLM usage"],
    link: "REST + SSE (streaming)",
  },
  {
    name: "API",
    path: "api/",
    parts: ["LLM gateway", "LangGraph incident agent", "RAG retrieval", "Routers + OpenAPI"],
    link: "MCP (tool calls)",
  },
  {
    name: "MCP servers",
    path: "mcp/",
    parts: [
      "sensor-mcp: readings, alarms, anomalies",
      "docs-mcp: document search, incident reports",
    ],
    link: "SQL",
  },
  {
    name: "Database",
    path: "PostgreSQL 16",
    parts: [
      "TimescaleDB: sensor time series",
      "pgvector: document embeddings",
      "Agent checkpoints",
    ],
  },
];

export const SIDE_SYSTEMS = [
  {
    name: "Simulator",
    path: "simulator/",
    text: "Generates sensor data and injects 6 known faults.",
  },
  {
    name: "Evals",
    path: "evals/",
    text: "Scenarios with known root causes, to measure the agent.",
  },
  {
    name: "Groq · OpenRouter",
    path: "external",
    text: "Free LLMs, Groq first: one gateway with routes, retries, a prompt cache and cost logging.",
  },
];

export const STACK: { area: string; items: string[] }[] = [
  {
    area: "Frontend",
    items: [
      "Next.js 16 (App Router)",
      "React 19",
      "TypeScript",
      "Tailwind CSS 4",
      "shadcn/ui",
      "TanStack Query",
      "Recharts",
    ],
  },
  {
    area: "Backend",
    items: ["Python 3.12", "FastAPI", "Pydantic v2", "SQLAlchemy 2 (async)", "Alembic", "uv"],
  },
  {
    area: "AI",
    items: [
      "LangGraph",
      "Groq + OpenRouter (free models)",
      "fastembed (bge-small-en-v1.5, local)",
      "MCP Python SDK (FastMCP)",
    ],
  },
  {
    area: "Data",
    items: ["PostgreSQL 16", "TimescaleDB", "pgvector", "Postgres full-text search"],
  },
  {
    area: "Quality",
    items: ["pytest", "Vitest", "ruff", "mypy", "ESLint", "Prettier", "GitHub Actions"],
  },
  { area: "Planned", items: ["Langfuse (tracing)", "Docker", "k3s + Helm", "Vercel"] },
];

export const PRINCIPLES = [
  {
    title: "Human in the loop",
    text: "The agent pauses before saving; a person approves, edits or rejects the work order.",
  },
  {
    title: "Evidence or nothing",
    text: "Every claim cites a reading, an alarm or a document; without enough data it says so.",
  },
  {
    title: "Sensor faults are not machine faults",
    text: "A drifting sensor must not be blamed on the machine: a deliberate test case.",
  },
  {
    title: "Free and bounded",
    text: "Free models with a fallback list, retries, a prompt cache and a per-run budget.",
  },
  {
    title: "Measured, not assumed",
    text: "Retrieval and agent quality are tracked with evals, not judged by eye.",
  },
  {
    title: "Synthetic data only",
    text: "All machines, readings and documents are generated for this demo.",
  },
];

export type PhaseStatus = "done" | "in-progress" | "planned";

export const ROADMAP: { phase: string; status: PhaseStatus }[] = [
  { phase: "Setup", status: "done" },
  { phase: "Data & simulator", status: "done" },
  { phase: "LLM gateway", status: "done" },
  { phase: "Knowledge base & RAG", status: "done" },
  { phase: "MCP servers", status: "done" },
  { phase: "Incident agent (LangGraph)", status: "done" },
  { phase: "API for the frontend", status: "done" },
  { phase: "Dashboard", status: "done" },
  { phase: "Evals", status: "planned" },
  { phase: "Observability", status: "planned" },
  { phase: "Deployment", status: "planned" },
  { phase: "Portfolio polish", status: "planned" },
];
