# Relay Chat

Relay Chat is a React chat interface backed by FastAPI, LiteLLM, and SQLite. Each assistant reply displays its source, model, server latency, and request token counts.

## 1. Live URLs

- [Frontend](https://relay-web-production-0abd2054.up.railway.app)
- [Backend health](https://relay-api-production.up.railway.app/health)
- [Railway project dashboard](https://railway.com/project/b924f7ac-6886-46e7-b26d-8bcbd873d1ef) for service administration

The backend needs two distinct provider keys in the Railway service's `LLM_API_KEYS` variable before readiness can return 200. Set the variable in Railway's Variables tab. The deployed backend uses [AICredits](https://aicredits.in/docs/api-reference) with `LLM_API_BASE=https://api.aicredits.in/v1` and `LLM_MODEL=openai/google/gemini-2.5-flash-lite`. Use two distinct AICredits API keys. Local installations default to the direct Gemini API. Set the same two variables in `.env` to use AICredits locally.

## 2. Run locally: with Docker and without

**With Docker Compose**

```sh
cp .env.example .env
# Set LLM_API_KEYS in .env to two distinct keys for LLM_MODEL.
make up
```

Open [the frontend](http://localhost:3000) or [backend health](http://localhost:8000/health). `make down` stops both services and preserves the named volume.

**Without Docker**

Use Python 3.12 and Node 22. Copy `.env.example` to `.env`, add the keys, and run from the repository root.

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
set -a
. ./.env
set +a
DATABASE_URL=sqlite:///./data/app.db LITELLM_LOCAL_MODEL_COST_MAP=True \
  .venv/bin/python -m uvicorn main:app --app-dir backend --port 8000
```

In a separate terminal at the repository root:

```sh
npm --prefix frontend ci
npm --prefix frontend run dev
```

For a custom backend URL, export `VITE_API_BASE_URL` before starting Vite or building the frontend. The frontend uses this URL at build time. The backend's `ALLOWED_ORIGINS` should contain the exact frontend origin. `.env.example` lists the application settings.

## 3. Design decisions: key fallback, caching, SQLite session storage

- **Fallback.** The application calls LiteLLM's async completion interface using the configured model and key pool in order. Each attempt has its own timeout and LiteLLM retries are disabled so the next key gets a chance immediately. A failed key is counted once. On exhaustion, the API returns a JSON 503 and leaves the session untouched. Key values stay on the backend.
- **Caching.** LiteLLM's in-memory cache stores reply text and provider-reported token counts for one hour. The cache key encodes the model and full ordered role/content list. New sessions with identical first prompts can share a cache entry. Simulation requests skip cache reads and writes so they exercise the actual fallback path even when a response is cached.
- **SQLite.** Startup creates the schema and enables WAL. A successful turn commits the effective system prompt, user message, assistant reply, and metadata in one transaction. The server rebuilds model context from SQLite. A per-session lock serializes concurrent turns in the single backend process. Run one worker and one replica with a volume mounted at `/data`. History survives restarts, while the memory cache starts empty.
- **Latency and usage.** The backend measures from request entry through reply resolution. It records the provider's prompt and completion counts for each turn and reuses those counts on a cache hit.

## 4. Verification: how to see failover, cache hits and per-message token usage

Set `API` to the backend base URL. Use a new run ID to prime a fresh cache entry. The following commands exercise all three endpoints.

```sh
API=https://relay-api-production.up.railway.app
RUN=$(date +%s)
PROMPT="Reply with the capital of France in one sentence. Run $RUN"

curl -sS "$API/health"
for session in "demo-$RUN" "cache-$RUN"; do
  curl -sS "$API/chat" -H 'Content-Type: application/json' \
    -d "{\"session_id\":\"$session\",\"message\":\"$PROMPT\"}"
  echo
done
curl -sS "$API/sessions/demo-$RUN/messages"

curl -sS "$API/chat" -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d "{\"session_id\":\"fallback-$RUN\",\"message\":\"$PROMPT\"}"
curl -i "$API/chat" -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' \
  -d "{\"session_id\":\"failed-$RUN\",\"message\":\"$PROMPT\"}"
curl -i "$API/sessions/failed-$RUN/messages"
```

The first request should show `LIVE_INFERENCE`. The second should show `LLM_CACHE` with the same reply and token counts, a smaller latency, and a null key ID. With the failure hook enabled, single-key simulation should return `key_2` with one fallback attempt. All-key simulation should return 503, and the failed session's history should return 404.

### Demo recording (2–3 minutes)

| Time | On screen | Narration |
| --- | --- | --- |
| 0:00–0:50 | Open the deployed frontend. Send “Remember that my project is called Alder,” then “What is my project called?” Show the reply that recalls Alder and point to the metadata. | “This is Relay Chat. I send only the new message each turn. The backend rebuilds the context from the session's SQLite history, so Relay can recall Alder. Each reply shows the model, source, latency, and token counts for that request.” |
| 0:50–1:30 | Run the health check and the two identical prompts in different session IDs. Compare the results and fetch the first session's history. | “Health shows both keys and the cache are configured. The first prompt goes to the provider. The second has different session metadata but the same model input, so the reply and token counts come from cache. The history endpoint returns both stored messages in order.” |
| 1:30–2:15 | Run the single-key and all-key simulations, then request the failed session's history. | “Here the header makes the first key return 429 for this request. The second key produces a real reply and the metadata shows one fallback attempt. When both keys fail, the API returns 503. The failed session's history returns 404 because the backend only commits completed turns.” |


Run backend tests without provider keys:

```sh
.venv/bin/pip install -r backend/requirements-dev.txt
(cd backend && LITELLM_LOCAL_MODEL_COST_MAP=True ../.venv/bin/python -m pytest -q)
```

## 5. Setup Used: your editor and AI tools

Built in the Codex desktop app with OpenAI Codex. Backend checks use pytest and FastAPI TestClient. The production frontend build uses Vite.

## 6. Bonus features (if any): what you added beyond the requirements and why

The session settings panel lets users set a system prompt. The backend persists the effective prompt so it applies to later messages in the same session.
