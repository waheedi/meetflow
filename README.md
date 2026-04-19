# MeetFlow Dev Alignment Sim
![Screenshot](/meetflow.png)
## Prerequisites

- Python 3.11+
- OpenAI API key 

## Setup

1. Clone or extract this project.
2. From the project root, create a virtual environment and install dependencies:

```bash
cd ./meetflow
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Configure environment variables:

```bash
cp .env.example .env
# then edit .env and set OPENAI_API_KEY or use it in the cmd line below
```

4. Start the app:

```bash
OPENAI_API_KEY="sk-.." uvicorn app.main:app --reload --port 8000
```

5. Open [http://localhost:8000](http://localhost:8000)

## Usage

1. Enter either:
   - a local repository path, or
   - a git URL (for example `https://github.com/waheedi/AUDI-DECODE-SECRET-PIN`), or
   - an archive URL/path (for example `https://host/repo.tar.gz`).
2. Click **Start Session**.
3. Ask the team a question (architecture, risks, plan, timeline, estimates, etc.).
4. Watch agent-by-agent discussion, facilitator convergence, and cumulative cost updates.
5. Intervene anytime with additional operator messages.

## What Is MeetFlow

MeetFlow is an engineering alignment simulator that helps teams make better technical and product decisions before execution starts.

It acts as a decision-support layer across three phases:

- Before meeting: creates pre-read context, risks, and decision options from codebase evidence.
- During meeting: keeps discussion focused on decisions and trade-offs instead of rediscovery.
- After meeting: turns outcomes into owned actions (owner + due date + follow-ups).

MeetFlow is the renamed continuation of the original TechFlow codebase (which started as job-interview homework case).

The app models seven AI personas (Sarah, Kai, Tamer, Lara, Jonas, Belal, Michael) and runs a sequential group discussion grounded in real files from:
- local folders
- git repository URLs
- archive sources (`.zip`, `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tar.xz`)

A human operator can intervene at any point through the chat.

## Features

- 7 mandatory personas with distinct behavior and persistent state
- Sequential, discussion-style interaction (agents react to prior messages)
- Codebase analysis from any local repository path
- Shared repository tree context for all agents on each turn
- Agent-driven context expansion by path request (`REQUEST_CONTEXT: path/...`) with automatic follow-up
- File/function grounded output with explicit references
- Human-in-the-loop chat during discussion
- Visible thinking/loading states per agent
- Running token + cumulative dollar cost counter
- API retry/backoff/error handling
- Per-session queue + lock to avoid race conditions and state corruption
- Source resolver with cache, git clone support, and safe archive extraction limits

## Tech Stack

- Backend: FastAPI (Python)
- Frontend: Plain HTML/CSS/JavaScript (served by FastAPI)
- LLM provider: OpenAI
- Models:
  - Agent discussion: `gpt-5.3-codex`
  - Facilitator synthesis: `gpt-5`
* for lowering the cost per token we can use gpt-4o-mini or gpt-5-mini.

## Configuration

Environment variables are defined in `.env.example`.

Important values:

- `LOG_LEVEL` (`INFO` default, set `DEBUG` for raw LLM payload troubleshooting)
- `LLM_MODEL_AGENT` (default `gpt-5.3-codex`)
- `LLM_MODEL_SYNTHESIS` (default `gpt-5`)
- `LLM_AGENT_MAX_OUTPUT_TOKENS` (default `8192`)
- `LLM_SYNTHESIS_MAX_OUTPUT_TOKENS` (default `8192`)
- `OPENAI_API_KEY`
- `OPENAI_PRICING_DOCS_URL` (startup pricing source; default OpenAI pricing docs URL)
- Startup pricing preload parses docs pricing page for configured models (`LLM_MODEL_AGENT`, `LLM_MODEL_SYNTHESIS`)
- `LLM_MAX_RETRIES`, `REQUEST_TIMEOUT_SECONDS` for robustness
- `RESOLVER_CACHE_DIR` for downloaded/cloned source caching
- `RESOLVER_MAX_DOWNLOAD_BYTES`, `RESOLVER_MAX_EXTRACT_BYTES`, `RESOLVER_MAX_EXTRACT_FILES` for extraction safety limits

## API Endpoints

Once the app is running, use the built-in OpenAPI docs:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI schema JSON: `http://localhost:8000/openapi.json`

If you run on a different port, replace `8000` accordingly.

## Running Tests

Run the unit test suite from project root:

```bash
python3 -m unittest discover -s tests -v
```

To verify live model pricing lookup against OpenAI (requires `OPENAI_API_KEY` in shell):

```bash
python3 -m unittest tests.test_live_pricing -v
```

## Notes on Reliability

- LLM calls use retries with exponential backoff for transient provider failures.
- Failures are surfaced as structured chat errors without app crashes.
- Conversation state is protected by per-session lock + serialized input queue.
- Archive extraction blocks unsafe paths/symlinks and enforces size/file-count limits.
- Pricing is preloaded at startup from the OpenAI pricing docs page and cached in memory for active models.
- If docs parsing fails for a model, runtime fallback pricing keeps cost counters non-zero.

## Debugging LLM Output

For model-response debugging, run with:

```bash
LOG_LEVEL=DEBUG uvicorn app.main:app --reload --port 8000
```

Then trigger one turn and capture lines containing:
- `LLM raw response`
- `LLM payload missing message`
- `Fallback message injected`

## Optional Mock Mode

For offline UI testing only:

- Set `MOCK_LLM=true` in `.env`.
- The simulator will emit synthetic responses with zero cost.
- For real evaluation, keep `MOCK_LLM=false` and use a valid API key.

## Todos
- Make persona prompt editable on real-time runs via clicking on names and modal style editor
