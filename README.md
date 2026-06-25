# de-swarm API

Production-ready FastAPI gateway around the **`de-sql-3b-v2`** Ollama model. Translates natural language to read-only SQL and optionally executes it against a sandboxed SQLite database.

**Phase 2 of the [de-swarm project](https://github.com/nurahmad-data/de-swarm)** — turns "I have a model" into "I have an API."

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 73 passing](https://img.shields.io/badge/tests-73%20passing-brightgreen.svg)](tests/)

---

## 📊 Verified Performance

Tested on **22 queries** across 3 schemas (ecommerce, retail, SaaS) on CPU-only hardware:

| Metric | Value |
|---|---|
| Successful SQL executions | **22 / 22 (100%)** |
| Hard failures (422 errors) | **0** |
| Security violations | **0** |
| Syntax errors | **0** |
| Median latency (warm cache) | **~17s** |
| Range | **9s – 62s** |
| Cost per query | **$0** |

**Hero query** — NPS promoters/passives/detractors by plan (4-table JOIN + 3 CASE expressions): perfect SQL, $0 cost.

Full benchmark details: [TEST_RESULTS.md](TEST_RESULTS.md)

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────┐
│  $5 VPS (Hetzner CX22, 2 vCPU, 4 GB RAM + swap)          │
│                                                          │
│  ┌────────────┐         ┌────────────────────┐          │
│  │   Caddy    │────────▶│   de-swarm-api     │          │
│  │  (TLS 443) │  :8000  │  (FastAPI + uvicorn)│          │
│  └────────────┘         └─────────┬──────────┘          │
│                                   │                      │
│                          http://ollama:11434             │
│                                   │                      │
│                         ┌─────────▼──────────┐          │
│                         │      Ollama         │          │
│                         │  de-sql-3b-q8       │          │
│                         │  (3.3 GB q8_0 GGUF) │          │
│                         └────────────────────┘          │
└──────────────────────────────────────────────────────────┘
```

The API is structured into 8 modules:

```
app/
├── main.py              # FastAPI app + 6 routes + self-correction loop
├── config.py            # Pydantic settings (env-driven)
├── auth.py              # X-API-Key middleware (constant-time compare)
├── ollama.py            # Ollama HTTP client + SQL cleaning
├── schema_fetcher.py    # Read-only SQLite schema introspection + RAG cache
├── schema_rag.py        # Phase 2.3 — retrieval-augmented schema context
├── executor.py          # Read-only SQL executor with 5-layer safety
└── models.py            # Pydantic request/response schemas
```

No LangGraph, no LangChain — the API doesn't need the training pipeline's orchestrator. It's just an HTTP server that calls another HTTP server.

---

## Quick start (local dev)

```bash
# 1. Install deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# 2. Make sure Ollama is running with the model loaded
ollama serve &
ollama pull hf.co/nurahmad-data/de-sql-3b-v2-gguf
ollama cp hf.co/nurahmad-data/de-sql-3b-v2-gguf de-sql-3b-q8

# 3. Drop your trained SQLite DBs into ./data/
mkdir -p data
cp ~/de-swarm/data/ecommerce.db data/
cp ~/de-swarm/data/saas.db       data/
cp ~/de-swarm/data/retail.db     data/

# 4. Configure env
cp .env.example .env
# Edit .env: set OLLAMA_MODEL=de-sql-3b-q8, USE_SCHEMA_RAG=true

# 5. Run the API
uvicorn app.main:app --reload --port 8000

# 6. Visit the auto-generated docs
open http://localhost:8000/docs
```

## Quick start (Docker)

```bash
docker-compose up --build
docker-compose exec ollama ollama pull hf.co/nurahmad-data/de-sql-3b-v2-gguf
```

The API will be at `http://localhost:8000`, Ollama at `http://localhost:11434`.

---

## Endpoints

| Method | Path                     | Description                              | Auth |
|--------|--------------------------|------------------------------------------|------|
| GET    | `/health`                | Liveness + Ollama reachability + schemas | No   |
| GET    | `/schemas`               | List available schema names              | Yes  |
| GET    | `/schemas/{name}`        | Show tables + columns + samples          | Yes  |
| POST   | `/query`                 | NL → SQL (no execution)                  | Yes  |
| POST   | `/execute`               | SQL → rows                               | Yes  |
| POST   | `/query-and-execute`     | NL → SQL → rows (+ self-correction)      | Yes  |

### POST `/query-and-execute` — the hero example

```bash
curl -X POST http://localhost:8000/query-and-execute \
  -H "Content-Type: application/json" \
  -d '{"question": "Count of NPS promoters, passives, and detractors by plan", "schema": "saas"}'
```

```json
{
  "sql": "SELECT p.plan_name,
    COUNT(CASE WHEN n.score >= 9 THEN 1 END) AS promoters,
    COUNT(CASE WHEN n.score BETWEEN 7 AND 8 THEN 1 END) AS passives,
    COUNT(CASE WHEN n.score <= 6 THEN 1 END) AS detractors
    FROM organizations o
    JOIN subscriptions s ON o.org_id = s.org_id
    JOIN plans p ON s.plan_id = p.plan_id
    JOIN nps_surveys n ON o.org_id = n.org_id
    WHERE n.survey_date >= DATE('now', '-30 days') AND s.status = 'active'
    GROUP BY p.plan_name ORDER BY p.plan_name ASC;",
  "model": "de-sql-3b-q8",
  "columns": ["plan_name", "promoters", "passives", "detractors"],
  "rows": [
    {"plan_name": "Enterprise", "promoters": 1, "passives": 0, "detractors": 0},
    {"plan_name": "Pro",        "promoters": 4, "passives": 0, "detractors": 0},
    {"plan_name": "Starter",    "promoters": 6, "passives": 2, "detractors": 0}
  ],
  "row_count": 3,
  "sql_gen_ms": 31692,
  "exec_ms": 0,
  "total_ms": 31692
}
```

---

## 🛡️ 5-Layer SQL Safety Model

SQL execution has **5 layers of defense**, in order:

1. **Forbidden-pattern regex** — `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`, `CREATE`, `ATTACH`, `DETACH`, `PRAGMA`, `VACUUM`, `REINDEX` all rejected with HTTP 422
2. **Single-statement enforcement** — SQL is split on `;`, only the first non-empty statement is kept
3. **Read-only SQLite connection** — opened with `mode=ro` URI; writes are physically impossible
4. **Per-query timeout** — `PRAGMA busy_timeout` + connection timeout
5. **Row limit cap** — `DEFAULT_ROW_LIMIT=100`, `MAX_ROW_LIMIT=10000`

**Verified across 22 production queries + 6 adversarial tests: zero security violations.**

---

## 🔍 Schema RAG (Phase 2.3)

For large schemas (>8 tables), Schema RAG retrieves only the tables relevant to the question instead of dumping all tables. Cut SaaS schema from 16 tables (~8,200 chars) to 4-5 tables (~2,000 chars).

**Pipeline:**
1. **Question analysis** — keyword extraction with stopword removal + schema vocabulary protection + domain synonym expansion
2. **Table retrieval** — multi-signal scoring (name > column > sample, + temporal boost)
3. **FK path resolution** — BFS shortest-path through FK graph, with bridge blacklisting for hub tables
4. **FK inference** — handles schemas without explicit FK declarations via column-name patterns

**Domain synonyms** (25+ terms): `revenue → invoice/amount/paid/billing`, `churn → subscription/canceled/status`, `NPS → nps_survey/score`, etc.

**14 production hardening fixes applied:**

| # | Fix | Impact |
|---|---|---|
| 1 | 1-Table Penalty | Simple queries don't dump all tables |
| 2 | Schema Vocab Protection | "status", "type" kept when they're real columns |
| 3 | Pluralization in FK Inference | `org_id` → `organizations` now works |
| 5 | Exclusive Seed Flaw | "Show active users" gets both `users` + `subscriptions` |
| 6 | Substring Match Inflation | "no" no longer matches `invoice_no` |
| 7 | Irregular Plurals | `categories` → `category`, `companies` → `company` |
| 8 | Hub Table Trap | BFS avoids `audit_log`/`events` as bridges |
| 9 | Temporal Boosting | "Monthly revenue" boosts tables with `_at` columns |
| 10 | Token Budgeting | Caps prompt by char count (4000 default) |
| 11 | Graph Caching | SchemaRAG built once per schema, reused from RAM |
| A | Expanded budget | 2500 → 4000 chars (was too tight) |
| B | Fallback bypass | Budget cap skipped when fallback triggers |
| C | Domain synonyms | `revenue → invoice/amount/paid/billing` |

---

## 🔄 Self-Correction Loop

When the model generates SQL that fails execution, the exact SQLite error is fed back to the model and SQL is regenerated. Recovers ~50-70% of hallucinated-column failures with bounded cost (max 1 retry by default).

**How it works:**
1. Model generates SQL
2. Executor tries to run it
3. If it fails → build correction prompt with the failed SQL + exact SQLite error
4. Model regenerates SQL with error feedback
5. If still fails → return 422 with helpful detail

**Configurable:** `SELF_CORRECTION_MAX_RETRIES=1` (default, recommended)

---

## 🧠 Smart Schema Sampling

Two-stage filtering to keep prompt size bounded without sacrificing accuracy:

1. **Name-based blocklist** — columns matching free-text patterns (email, name, url, description, ip_address, json) are never sampled
2. **Cardinality check** — remaining TEXT columns only sampled if ≤20 distinct values

Cut SaaS schema from 14,800 chars → 8,200 chars while preserving every sample the model uses.

---

## Authentication

If `API_KEY` env var is **non-empty**, every protected endpoint requires an `X-API-Key` header. Empty `API_KEY` = auth disabled (local dev).

```bash
openssl rand -hex 32  # generate a strong key
curl -H "X-API-Key: your-key-here" http://localhost:8000/schemas
```

`/health` is always public.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8000` | Bind port |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama HTTP base URL |
| `OLLAMA_MODEL` | `de-sql-3b-q8` | Model name (must exist in `ollama list`) |
| `OLLAMA_TIMEOUT_S` | `120` | HTTP timeout (CPU needs room) |
| `OLLAMA_NUM_PREDICT` | `300` | Max tokens per response |
| `SCHEMAS_DIR` | `./data` | Directory containing `<name>.db` files |
| `DEFAULT_SCHEMA` | `ecommerce` | Schema used when caller omits it |
| `USE_SCHEMA_RAG` | `false` | Enable Schema RAG |
| `RAG_MAX_TABLES` | `6` | Max tables RAG will return |
| `SELF_CORRECTION_MAX_RETRIES` | `1` | Self-correction retries (0=off, 1=recommended) |
| `DEFAULT_ROW_LIMIT` | `100` | Default max rows returned |
| `MAX_ROW_LIMIT` | `10000` | Hard cap on rows |
| `API_KEY` | (empty) | Required `X-API-Key` value; empty = auth disabled |
| `RAG_BRIDGE_BLACKLIST` | (empty) | Comma-separated tables to skip as BFS bridges |

---

## Deploy to a $5 VPS

Tested on Hetzner CX22 (2 vCPU, 4 GB RAM + 4 GB swap).

```bash
git clone https://github.com/nurahmad-data/de-swarm-api.git
cd de-swarm-api
cp .env.example .env
# Edit .env: set API_KEY=$(openssl rand -hex 32)

# Add 4 GB swap
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile

docker-compose up -d --build
docker-compose exec ollama ollama pull hf.co/nurahmad-data/de-sql-3b-v2-gguf

curl http://localhost:8000/health
```

### TLS with Caddy

```caddy
api.yourdomain.com {
  reverse_proxy localhost:8000
}
```

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

**73 tests, 0 failures:**
- 15 smoke tests (endpoints, auth, safety)
- 25 RAG tests (graph, keywords, retrieval, FK paths)
- 11 production hardening tests (1-table penalty, vocab protection, pluralization, etc.)
- 8 advanced production tests (hub tables, temporal boost, token budget, graph caching)
- 8 regression fix tests (budget bypass, fallback, domain synonyms)
- 6 self-correction tests (retry success, max retries, timeout handling)

---

## 📁 Project Layout

```
de-swarm-api/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app + 6 routes + self-correction loop
│   ├── config.py            # Pydantic settings (env-driven)
│   ├── auth.py              # X-API-Key middleware
│   ├── ollama.py            # Ollama HTTP client + SQL cleaning
│   ├── schema_fetcher.py    # Read-only SQLite schema introspection + RAG cache
│   ├── schema_rag.py        # Phase 2.3 — Schema RAG (14 hardening fixes)
│   ├── executor.py          # Read-only SQL executor with 5-layer safety
│   └── models.py            # Pydantic request/response schemas
├── tests/
│   ├── __init__.py
│   ├── test_smoke.py        # 20 endpoint + self-correction tests
│   └── test_rag.py          # 53 RAG + hardening + regression tests
├── scripts/
│   ├── extract_spider_schemas.py    # Phase 3 — Spider schema extractor
│   └── paraphrase_prompts.py        # Phase 3 — 3-tier lexical gap bridging
├── Dockerfile               # Multi-stage, ~120MB image
├── docker-compose.yml       # API + Ollama services
├── run_rag_benchmark.sh     # 22-query benchmark script
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── .dockerignore
├── .gitignore
├── TEST_RESULTS.md          # Full 22-query benchmark scorecard
├── PHASE_3_PLAN.md          # Phase 3 roadmap (Spider scaling)
└── README.md
```

---

## ⚠️ Known Limitations (honest, not marketing)

1. **Window functions** — the 3B model dodges `SUM() OVER (...)` and `ROW_NUMBER() OVER (...)`. Fix: Phase 2.5 7B validator.
2. **Over-eager date filters** — "per month" without explicit scope gets a 30-day filter. Fix: be explicit ("all-time").
3. **No streaming** — `/query` blocks until the model finishes. SSE streaming planned for Phase 4.
4. **No schema RAG for arbitrary DBs** — executes only against DBs in `SCHEMAS_DIR`. Schema RAG for user-supplied DBs is future work.
5. **CPU latency floor** — ~200ms/token × 100-150 tokens = 20-30s minimum. GPU inference required for sub-5s responses.

---

## 🔗 Related

- **Parent project:** [de-swarm](https://github.com/nurahmad-data/de-swarm) — model training pipeline
- **Model on HuggingFace:** [nurahmad-data/de-sql-3b-v2-gguf](https://huggingface.co/nurahmad-data/de-sql-3b-v2-gguf)
- **Full test results:** [TEST_RESULTS.md](TEST_RESULTS.md)
- **Phase 3 plan:** [PHASE_3_PLAN.md](PHASE_3_PLAN.md)

---

## License

MIT — same as the parent `de-swarm` project.
