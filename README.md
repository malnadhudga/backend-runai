# backend-runai

Small **FastAPI** service that proxies chat requests to **Google Gemini** using a **server-only** `GEMINI_API_KEY`. Intended for the open-source **RunAI** CLI when users do not set a local Gemini key.

## What RunAI clients configure

Point the CLI at this service (full URL including path):

| Variable | Purpose |
|----------|---------|
| `RUNAI_GEMINI_PROXY_URL` | Base URL of the proxy, e.g. `https://proxy.example.com/v1/chat` |

When `GEMINI_API_KEY` is unset locally, RunAI POSTs JSON to that URL. This service calls Gemini with **its** key only; v1 does **not** read API keys from the request body. The **model** is chosen on the server (`GEMINI_MODEL`, default `gemini-2.5-flash-lite`); the JSON `model` field is accepted for logging / client compatibility but **not** used for the upstream call.

Optional client headers:

- `Authorization: Bearer <token>` — required only if the server sets `PROXY_BEARER_TOKEN`.
- `X-Runai-Client-Id` — optional metering / attribution (logged, not secrets).

Optional JSON field `client_id` — same idea as the header; both are logged at INFO without logging full prompts.

## API

### `GET /healthz`

Returns `{"status":"ok"}`.

### `POST /v1/chat`

**Headers:** `Content-Type: application/json`; optional `Authorization`; optional `X-Runai-Client-Id`.

**Body:**

```json
{
  "model": "gemini-2.5-flash-lite",
  "system": "You are helpful.",
  "messages": [
    { "role": "user", "content": "Hello" }
  ],
  "client_id": "optional-string"
}
```

**Response:** JSON with a single string field (this implementation uses `text`):

```json
{ "text": "..." }
```

The server assembles one prompt for Gemini: if `system` is non-empty, `[System]\n{system}\n`, then each message as `[{role}]\n{content}`, blocks separated by blank lines — aligned with RunAI direct mode.

**Timeouts:** Gemini `generate_content` uses a **300s** request timeout to match long-running RunAI-style calls. Clients should use a comparable HTTP timeout (RunAI often uses ~300s).

## Configuration

Copy `.env.example` to `.env` and set values.

| Variable | Required | Default | Notes |
|----------|----------|---------|--------|
| `GEMINI_API_KEY` | yes | — | Server only; never log or return it. |
| `GEMINI_MODEL` | no | `gemini-2.5-flash-lite` | Always used for Gemini; request body `model` is ignored for the API call. |
| `PROXY_BEARER_TOKEN` | no | empty | If set, `Authorization: Bearer` must match. |
| `HOST` | no | `0.0.0.0` | Used when running via `python -m uvicorn` locally. |
| `PORT` | no | `8080` | Same. |
| `LOG_LEVEL` | no | `INFO` | Use `DEBUG` only in trusted environments. |
| `CORS_ORIGINS` | no | empty | Comma-separated list; empty disables CORS. Prefer explicit origins in production. |
| `LOG_PROMPTS` | no | `false` | If `true`, logs a truncated prompt preview at DEBUG — sensitive. |

**GitHub Actions (release workflow):**

- Repository **variable:** `GCP_PROJECT_ID` — your GCP project ID.
- Repository **secret:** `GCP_SA_KEY` — JSON key for a service account with **Artifact Registry Writer** (and any roles needed to push to your repository).

The workflow fails fast if `GCP_PROJECT_ID` is missing.

**Image URI pattern** (after a successful tag push):

```text
us-central1-docker.pkg.dev/<GCP_PROJECT_ID>/runai/runai-gemini-proxy:<tag>
```

Example tags: `v0.1.0`, `latest`.

## Local run

```bash
cp .env.example .env
# edit .env — set GEMINI_API_KEY at minimum

docker compose up --build
```

Or without Docker:

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
set PYTHONPATH=src
set GEMINI_API_KEY=your-key
uvicorn backend_runai.main:app --host 0.0.0.0 --port 8080
```

## Manual test

```bash
curl -sS http://localhost:8080/healthz

curl -sS http://localhost:8080/v1/chat ^
  -H "Content-Type: application/json" ^
  -H "X-Runai-Client-Id: test-cli" ^
  -d "{\"model\":\"gemini-2.5-flash-lite\",\"system\":\"\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi in one word.\"}]}"
```

(PowerShell: use `` ` `` for line continuation instead of `^`, or a single line.)

With bearer token:

```bash
curl -sS http://localhost:8080/v1/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PROXY_BEARER_TOKEN" \
  -d '{"model":"gemini-2.5-flash-lite","system":"","messages":[{"role":"user","content":"Hi"}]}'
```

## GCP deploy (outline)

1. Store `GEMINI_API_KEY` and optional `PROXY_BEARER_TOKEN` in **Secret Manager**.
2. On the VM (or Cloud Run / GKE — adjust networking): install Docker and Docker Compose plugin.
3. Use a `docker-compose.yml` that **pulls** the Artifact Registry image (not `build: .`) and injects secrets as env (e.g. `environment` from a startup script that reads Secret Manager, or [secret-specific compose syntax](https://docs.docker.com/compose/how-tos/use-secrets/) if you adopt Docker secrets).
4. Deploy:

   ```bash
   gcloud auth configure-docker us-central1-docker.pkg.dev
   docker compose pull && docker compose up -d
   ```

5. Terminate TLS at a load balancer or reverse proxy in front of the app.

## Security & logging

- Never log `GEMINI_API_KEY` or the `Authorization` header.
- At INFO, message bodies and full prompts are **not** logged; enable `LOG_PROMPTS=true` only for local debugging.

**Rate limiting:** not included in v1. For public endpoints, put a proxy (Cloud Armor, nginx limit_req, or add something like `slowapi`) in front.

---

<!-- Optional second workflow (not implemented): SSH deploy after image push
  Secrets: SSH_KEY (private key), optional VM_HOST / VM_USER as secrets or vars.
  Job would: ssh to VM, docker compose pull, docker compose up -d.
  Placeholders only — add when you have a fixed VM and key management.
-->

## License

Use and license as you prefer for your org; no license file included by default.
