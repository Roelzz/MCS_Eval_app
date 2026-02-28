# Copilot Studio Eval Platform

Evaluate your Copilot Studio agents with automated test datasets and LLM-as-judge scoring.

## Quick Start

```bash
uv sync
cp .env.example .env
# Edit .env with your Azure AD + Copilot Studio + Azure OpenAI credentials
uv run reflex init
uv run reflex run
```

Open http://localhost:3000

## Features

- **Automated Azure AD auth** — Direct-to-Engine client handles token acquisition
- **Dataset CRUD** — create/import test datasets via JSON or CSV, supports single-turn, multi-turn, and autonomous eval types
- **Eval runs with 9 DeepEval metrics** — answer relevancy, conversation completeness, knowledge retention, role adherence, task completion, hallucination, toxicity, bias, faithfulness
- **Dashboard** — run overview with score charts and status tracking
- **Per-metric scoring** — each metric scored independently with LLM judge reasoning, color-coded results (green/yellow/red)
- **Run detail page** — expandable result rows with conversation threads, side-by-side expected vs actual output

## Configuration

All config via `.env`:

| Variable | Description | Default |
|---|---|---|
| `AZURE_AD_TENANT_ID` | Azure AD tenant ID | — |
| `AZURE_AD_CLIENT_ID` | App registration client ID | — |
| `AZURE_AD_CLIENT_SECRET` | Client secret (enables automated auth flow) | — |
| `COPILOT_ENVIRONMENT_ID` | Power Platform environment ID | — |
| `COPILOT_AGENT_IDENTIFIER` | Copilot Studio agent schema name | — |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | — |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key | — |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Deployment name | `gpt-5` |
| `AZURE_OPENAI_API_VERSION` | API version | `2024-10-21` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `PORT` | Application port | `2009` |

## App Registration

### Azure Portal setup

1. Go to **Azure Portal → App registrations → New registration**
2. Name it (e.g., `copilot-eval-platform`)
3. Set redirect URI to `http://localhost:3000` (or your deployment URL)
4. Under **Certificates & secrets**, create a new client secret
5. Copy Tenant ID, Client ID, and Client Secret to `.env`

### Enable Direct-to-Engine on your agent

1. Open **Copilot Studio** → select your agent
2. Go to **Settings → Security → Authentication**
3. Enable **Direct-to-Engine** access
4. Note the Environment ID and Agent Schema Name for `.env`

## Available Metrics

| Metric | What it measures | Type |
|---|---|---|
| `answer_relevancy` | Whether the response addresses the user's question | Single-turn |
| `conversation_completeness` | Whether the full conversation resolves the user's need | Conversational |
| `knowledge_retention` | Whether the agent retains context across turns | Conversational |
| `role_adherence` | Whether the agent stays in its defined role | Conversational |
| `task_completion` | Whether the agent completed the requested task | Single-turn |
| `hallucination` | Detects unsupported or fabricated claims | Single-turn |
| `toxicity` | Detects harmful, offensive, or inappropriate content | Single-turn |
| `bias` | Detects gender, race, or political bias | Single-turn |
| `faithfulness` | Verifies factual accuracy against provided context | Single-turn |

## Dataset Formats

### Single-turn (JSON)

```json
[
  {
    "turns": [{"role": "user", "content": "What is the refund policy?"}],
    "expected_output": "Our refund policy allows returns within 30 days.",
    "context": "Optional reference text for faithfulness checking"
  }
]
```

### Multi-turn (JSON)

```json
[
  {
    "turns": [
      {"role": "user", "content": "I want to return an item"},
      {"role": "assistant", "content": "Sure, when did you purchase it?"},
      {"role": "user", "content": "Last week"}
    ],
    "expected_output": "You're within our 30-day return window."
  }
]
```

### Autonomous (JSON)

```json
[
  {
    "goal": "Book a flight from Amsterdam to London for next Friday",
    "expected_output": "Flight booked successfully"
  }
]
```

### CSV format

```csv
user_message,expected_output,context,tags,difficulty
What is the refund policy?,Returns within 30 days,policy doc,basics,easy
```

CSV columns: `user_message` (or `goal` for autonomous), `expected_output`, `context`, `tags`, `difficulty`.

## Development

```bash
uv run pytest              # Run tests
uv run ruff check .        # Lint
uv run ruff format .       # Format
uv run reflex run          # Dev server (hot reload)
```

## Deployment

```bash
podman-compose up --build
```

Runs on port 2009 in production mode.
