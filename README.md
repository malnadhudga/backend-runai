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

**GitHub Actions (deploy to GCE VM):** workflow `deploy-vm.yml`

- GitHub only runs workflows from the **default branch** (or the branch that contains the workflow file, depending on event). If your default branch is `main` and it has no `deploy/` or `.github/workflows/`, **merge `development` into `main`** or set **Settings → General → Default branch** to `development`.
- **After** `Release Docker image` succeeds, deploy runs automatically (same repo).
- Or **Actions → Deploy to GCE VM → Run workflow** (pick tag, default `latest`).
- Repository **variable:** `GCP_GEMINI_SECRET_ID` — Secret **id** only (e.g. `backend-runai-gemini-api-key`), not the API key string.
- Repository **variable:** `GCP_GEMINI_SECRET_LOCATION` — **Required for regional secrets** (e.g. `us-central1`). Leave **empty** if the secret is **global** (no location).
- Repository **secrets:** `VM_HOST` (e.g. `35.225.99.206`), `VM_USER` (Linux login, e.g. your username), `VM_SSH_PRIVATE_KEY` (private key for that user; matching public key in `~/.ssh/authorized_keys` on the VM).
- Repository **variable (optional):** `VM_DEPLOY_PATH` — directory on the VM where `vm-compose.yml` is copied and containers run. **Default:** `/tmp/backend-runai` (no sudo). To use `/opt/backend-runai` instead, run once on the VM: `sudo mkdir -p /opt/backend-runai && sudo chown "$USER:$USER" /opt/backend-runai`, then set `VM_DEPLOY_PATH` to `/opt/backend-runai`.

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

## GCE VM: one-time setup (e.g. Debian 12)

Do this **before** the deploy workflow can succeed.

### 1. Secret Manager

- Enable **Secret Manager API** on the project.
- Create a secret whose **value** is your Gemini API key. If you use a **regional** secret (e.g. `us-central1`), set `GCP_GEMINI_SECRET_LOCATION=us-central1` in GitHub and use the same region in `gcloud` on the VM (`--location=us-central1`).

### 2. VM service account IAM

Your VM uses the default compute service account, e.g. `PROJECT_NUMBER-compute@developer.gserviceaccount.com`. Grant it:

| Role | Why |
|------|-----|
| **Secret Manager Secret Accessor** | On that secret (or project) — `gcloud secrets versions access` on the VM |
| **Artifact Registry Reader** | Pull image from `us-central1-docker.pkg.dev/.../runai/runai-gemini-proxy` |

### 3. VM access scopes (important)

If **Cloud Platform** access is **off**, `gcloud` on the VM cannot call Google APIs. **Stop the VM**, **Edit** → **Security and access** → set access scope to **Allow full access to all Cloud APIs** (or enable **Cloud Platform**), then start the VM again.

### 4. Software on the VM

SSH in and install:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl google-cloud-cli docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"
# log out and back in so `docker` works without sudo
```

### 5. SSH for GitHub Actions

- Generate a key pair (on your laptop): `ssh-keygen -t ed25519 -f deploy-vm -N ""`
- Append `deploy-vm.pub` to **`~/.ssh/authorized_keys`** on the VM for `VM_USER`.
- Put the contents of **`deploy-vm`** (private key) in GitHub secret **`VM_SSH_PRIVATE_KEY`**.

### 6. Firewall

**VPC network → Firewall rules**: allow **TCP 8080** (or 443 if you terminate TLS on the VM) from **your IP** first; avoid `0.0.0.0/0` until you add TLS + auth.

### 7. Deploy flow

1. Push git tag `v0.1.0` → **Release Docker image** builds and pushes to Artifact Registry.
2. **Deploy to GCE VM** runs next and SSHs in, reads the Gemini key from Secret Manager, configures Docker auth, `docker compose pull` + `up -d` using `deploy/vm-compose.yml`.

Put TLS (load balancer, Caddy, or nginx) in front for production.

## Security & logging

- Never log `GEMINI_API_KEY` or the `Authorization` header.
- At INFO, message bodies and full prompts are **not** logged; enable `LOG_PROMPTS=true` only for local debugging.

**Rate limiting:** not included in v1. For public endpoints, put a proxy (Cloud Armor, nginx limit_req, or add something like `slowapi`) in front.

---

## License

Use and license as you prefer for your org; no license file included by default.
