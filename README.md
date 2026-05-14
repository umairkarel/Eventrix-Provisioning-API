# Provisioning API

REST API for provisioning and managing server-side GTM containers. Each API call to create a server triggers DNS record creation, Traefik routing config generation, and Docker container startup — all atomically with rollback on failure.

Multi-tenant: each API key is scoped to a tenant, and all server operations are isolated to that tenant.

Part of the [Server-Side GTM Tracking Platform](../../README.md).

---

## Table of Contents

- [Running Locally](#running-locally)
- [Configuration](#configuration)
- [API Reference](#api-reference)
  - [Authentication](#authentication)
  - [Tenants](#tenants)
  - [Servers](#servers)
  - [Custom Domains](#custom-domains)
- [Database Migrations](#database-migrations)
- [Testing](#testing)
- [Architecture](#architecture)
- [Data Models](#data-models)

---

## Running Locally

The API is designed to run as part of the full Docker Compose stack. See the [root README](../../README.md) for stack setup.

To run the API standalone for development:

```bash
# Install dependencies
uv sync

# Set required environment variables
export DATABASE_URL="postgresql+asyncpg://provisioning:provisioning@localhost:5432/provisioning"
export MOCK_CLOUDFLARE=true
export BASE_DOMAIN=localtest.me
export TRAEFIK_CONF_DIR=/tmp/traefik-conf

# Run migrations
uv run alembic upgrade head

# Start the server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The interactive API docs are available at `http://localhost:8000/docs`.

---

## Configuration

All configuration is via environment variables (loaded from `.env` if present).

| Variable | Default | Required | Description |
|---|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://provisioning:provisioning@postgres:5432/provisioning` | Yes | PostgreSQL async connection string |
| `DOCKER_HOST` | `tcp://docker-socket-proxy:2375` | Yes | Docker API endpoint (use socket proxy, not raw socket) |
| `BASE_DOMAIN` | — | Yes | Root domain; servers served at `{subdomain}.{BASE_DOMAIN}` |
| `VM_IP` | — | Prod | Public IP for Cloudflare A records |
| `CF_ZONE_ID` | — | Prod | Cloudflare zone ID |
| `CF_API_TOKEN` | — | Prod | Cloudflare API token (Zone:DNS:Edit scope) |
| `CF_API_TOKEN_FILE` | — | Prod | Path to file containing the Cloudflare token (alternative to `CF_API_TOKEN`) |
| `TRAEFIK_CONF_DIR` | `/traefik/conf.d` | Yes | Directory where Traefik watches for dynamic config files |
| `MOCK_CLOUDFLARE` | `false` | No | Skip real Cloudflare API calls (dev/test) |
| `TRAEFIK_RESTART_ON_CONFIG_CHANGE` | `false` | No | Restart Traefik container after writing config (Windows/WSL2 only — inotify doesn't work on mounted volumes) |
| `PREVIEW_USE_SIDECAR` | `false` | No | Spawn an nginx sidecar container for preview server TLS (dev only) |
| `GTM_IMAGE` | `gcr.io/cloud-tagging-10302018/gtm-cloud-image:stable` | No | GTM container image |
| `GTM_PORT` | `8080` | No | Port GTM containers listen on |
| `GTM_SERVER_MEM_LIMIT` | `512m` | No | Memory limit for GTM server containers |
| `GTM_SERVER_NANO_CPUS` | `1000000000` | No | CPU limit for GTM server containers (1 CPU = 1,000,000,000) |
| `GTM_PREVIEW_MEM_LIMIT` | `256m` | No | Memory limit for GTM preview containers |
| `GTM_PREVIEW_NANO_CPUS` | `500000000` | No | CPU limit for GTM preview containers |
| `GTM_PROXY_MEM_LIMIT` | `64m` | No | Memory limit for nginx sidecar (dev only) |

---

## API Reference

### Authentication

Server endpoints require an `X-API-Key` header. Keys are bcrypt-hashed, tenant-scoped, and stored in the `api_keys` table. Tenant management endpoints require no authentication (bootstrap).

```
X-API-Key: your-api-key
```

API keys are managed via the Tenants API. Create a tenant first, then create a key under it:

```bash
# Create a tenant (no auth required)
curl -s -X POST http://localhost:8000/api/v1/tenants \
  -H "Content-Type: application/json" \
  -d '{"name": "My Tenant"}'
# → {"id": "...", "name": "My Tenant", "created_at": "..."}

# Create an API key for the tenant (raw key is returned once — save it)
curl -s -X POST http://localhost:8000/api/v1/tenants/{tenant_id}/keys \
  -H "Content-Type: application/json" \
  -d '{"name": "dev-key"}'
# → {"id": "...", "name": "dev-key", "key": "raw-key-here", "created_at": "..."}
```

Use the raw key in the `X-API-Key` header. It is not stored in plaintext and cannot be retrieved again.

---

### Tenants

#### POST `/api/v1/tenants`

Creates a new tenant.

**Request body:**

```json
{
  "name": "My Tenant"
}
```

**Response `201`:**

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "name": "My Tenant",
  "created_at": "2026-01-01T00:00:00Z"
}
```

---

#### GET `/api/v1/tenants`

Returns all tenants.

**Response `200`:** Array of tenant objects.

---

#### GET `/api/v1/tenants/{tenant_id}`

**Response `200`:** Single tenant object.  
**`404`** if not found.

---

#### DELETE `/api/v1/tenants/{tenant_id}`

**Response `204`** (no body).  
**`409`** if the tenant has active servers — deprovision all servers first.

---

#### POST `/api/v1/tenants/{tenant_id}/keys`

Creates an API key for the tenant. The raw key is returned once and cannot be retrieved again.

**Request body:**

```json
{
  "name": "dev-key"
}
```

**Response `201`:**

```json
{
  "id": "...",
  "name": "dev-key",
  "key": "raw-key-here",
  "created_at": "2026-01-01T00:00:00Z"
}
```

---

#### GET `/api/v1/tenants/{tenant_id}/keys`

Lists all API keys for the tenant. The `key` field is not included in list responses.

**Response `200`:** Array of API key objects (without `key` field).

---

#### DELETE `/api/v1/tenants/{tenant_id}/keys/{key_id}`

Revokes an API key.

**Response `204`** (no body).

---

### Servers

All server endpoints require `X-API-Key`. Servers are scoped to the tenant of the API key — requests for servers belonging to a different tenant return `404`, not `403`.

#### POST `/api/v1/servers`

Provisions a new GTM server. Creates DNS A record, writes Traefik routing config, starts GTM server and preview containers. On any failure, all created resources are rolled back.

**Request body:**

```json
{
  "name": "Acme Corp",
  "subdomain": "acme",
  "container_config": "<base64-encoded GTM container config>",
  "addons": {
    "geoip": false,
    "bot_filter": false,
    "gtm_js_proxy": true,
    "lib_proxy": false,
    "cookie_extension": false,
    "rate_limit_rps": 50
  }
}
```

- `subdomain`: 2–63 chars, lowercase letters, digits, and hyphens only. Must be unique among active servers.
- `container_config`: Base64-encoded GTM workspace config string (from GTM container settings).
- `addons`: Optional. Defaults shown above.

**Response `201`:**

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "tenant_id": "...",
  "name": "Acme Corp",
  "subdomain": "acme",
  "container_config": "...",
  "gtm_container_id": "GTM-XXXXXX",
  "gtm_env": 1,
  "status": "provisioning",
  "addons": { "geoip": false, "bot_filter": false, "gtm_js_proxy": true, "lib_proxy": false, "cookie_extension": false, "rate_limit_rps": 50 },
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

The server starts in `provisioning` status. It transitions to `active` automatically when the GTM container passes its health check (typically 30–60 seconds). Poll `GET /api/v1/servers/{id}` or `GET /api/v1/servers/{id}/health` to track progress.

**Errors:**
- `409` — subdomain already in use by an active server
- `422` — invalid subdomain format
- `502` — provisioning failed (all resources rolled back, check server logs)

---

#### GET `/api/v1/servers`

Returns all non-deleted servers scoped to the API key's tenant.

**Response `200`:** Array of server objects (same schema as POST response).

---

#### GET `/api/v1/servers/{server_id}`

**Response `200`:** Single server object.  
**`404`** if not found, deleted, or belongs to a different tenant.

---

#### PATCH `/api/v1/servers/{server_id}`

Updates server properties. All fields are optional; only provided fields are changed.

**Request body:**

```json
{
  "name": "Acme Corp (Updated)",
  "container_config": "<new base64 config>",
  "addons": {
    "geoip": true,
    "rate_limit_rps": 100
  }
}
```

Addon updates are **merged** — only the specified keys are changed, others are preserved.

Updating `addons` regenerates the Traefik config for the server immediately.

**Response `200`:** Updated server object.

---

#### DELETE `/api/v1/servers/{server_id}`

Deprovisions the server:
1. Marks status as `deleted` in DB (before stopping containers, to avoid misclassifying the stop as a crash)
2. Stops and removes Docker containers
3. Deletes Cloudflare DNS record
4. Removes Traefik config file

**Response `204`** (no body).

A deleted server's subdomain can be reused by a new server.

---

#### POST `/api/v1/servers/{server_id}/suspend`

Stops the GTM containers without removing any configuration. The server remains restorable.

Status committed to `suspended` **before** containers are stopped, so Docker die events from the stop are not misclassified as crashes.

**Response `200`:** Updated server object with `status: "suspended"`. Idempotent — returns immediately if already suspended.

---

#### POST `/api/v1/servers/{server_id}/resume`

Starts previously suspended containers.

**Response `200`:** Updated server object with `status: "active"`. Idempotent — returns immediately if already active.

---

#### GET `/api/v1/servers/{server_id}/health`

Returns live container health from Docker (not cached DB state).

**Response `200`:**

```json
{
  "server_id": "3fa85f64-...",
  "status": "active",
  "container_health": "healthy",
  "subdomain": "acme"
}
```

`container_health` values: `healthy`, `unhealthy`, `starting`, `running`, `exited`, `not_found`, `unknown`.

---

#### GET `/api/v1/servers/{server_id}/snippet`

Returns the GTM tracking snippet to embed on the website. Uses the custom domain if one is active, otherwise the platform subdomain.

**Response `200`:**

```json
{
  "server_url": "https://acme.yourdomain.com",
  "gtm_snippet": "<script>window.dataLayer=window.dataLayer||[];(function(w,d,s,l,i){...})</script>"
}
```

---

### Custom Domains

Servers can serve from a custom domain (`analytics.acme.com`) instead of the platform subdomain.

#### POST `/api/v1/servers/{server_id}/custom-domain`

Registers a custom domain and starts background DNS verification polling.

**Request body:**

```json
{
  "domain": "analytics.acme.com"
}
```

**Response `201`:**

```json
{
  "id": "...",
  "server_id": "...",
  "domain": "analytics.acme.com",
  "status": "pending_dns",
  "cname_target": "acme.yourdomain.com",
  "verified_at": null,
  "created_at": "2026-01-01T00:00:00Z"
}
```

After creating the domain, add a CNAME record pointing to `cname_target`. The platform polls DNS in the background until the record resolves, then sets status to `active`.

**Errors:**
- `409` — server already has a custom domain, or domain is registered to another server

---

#### GET `/api/v1/servers/{server_id}/custom-domain/status`

Returns current custom domain status.

**Status values:**

| Status | Meaning |
|---|---|
| `pending_dns` | Waiting for CNAME to resolve |
| `pending_cert` | DNS verified, waiting for TLS cert |
| `active` | Domain verified and serving traffic |
| `failed` | Verification failed after max retries |

---

#### POST `/api/v1/servers/{server_id}/custom-domain/verify`

Re-triggers DNS polling for a domain in `pending_dns` or `failed` state.

**Response `200`:** `{"detail": "DNS verification restarted"}`

---

#### DELETE `/api/v1/servers/{server_id}/custom-domain`

Removes the custom domain registration.

**Response `204`** (no body).

---

## Database Migrations

Migrations are run automatically on container startup via `entrypoint.sh`. To run manually:

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Check current migration state
uv run alembic current

# Create a new migration
uv run alembic revision --autogenerate -m "description"

# Roll back one migration
uv run alembic downgrade -1
```

**Migration history:**

| Revision | Description |
|---|---|
| `0001` | Initial schema: clients, custom_domains, api_keys tables |
| `0002` | Partial unique index on subdomain (unique only when status ≠ deleted) |
| `0003` | Add `gtm_container_id` and `gtm_env` columns to clients |
| `0004` | Add `error` value to ClientStatus enum |
| `0005` | Rename clients→servers, add tenants table, tenant_id FK on servers and api_keys, rename ClientStatus→ServerStatus enum |

---

## Testing

Tests use SQLite in-memory via aiosqlite — no running PostgreSQL or Docker required.

```bash
# Install dev dependencies
uv sync

# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_docker_events.py -v

# Run a specific test
uv run pytest tests/test_servers.py::test_create_server -v
```

**Test coverage:**

| File | What it covers |
|---|---|
| `test_auth.py` | API key validation (missing, invalid, valid) |
| `test_tenants.py` | Tenant CRUD, API key create/list/revoke, end-to-end auth |
| `test_servers.py` | Full server lifecycle via HTTP including tenant isolation (cross-tenant 404) |
| `test_domains.py` | Custom domain add/status/remove |
| `test_docker_events.py` | Event-driven status transitions: healthy → active, die → error, suspend/delete race condition fix, startup recovery |
| `test_traefik.py` | Traefik config generation (middleware chain, routing rules, rate limits) |
| `test_schemas.py` | `parse_container_config` parsing |
| `test_health.py` | `/healthz` endpoint, ORM model creation |

All external services (Docker, Cloudflare, Traefik file I/O) are mocked in tests. The Docker event handler tests use a `StaticPool` SQLite engine so the patched `SessionLocal` shares the same in-memory database as the test fixtures.

---

## Architecture

### Provisioning Flow

```
POST /api/v1/servers
         │
         ├─ 1. Check subdomain uniqueness
         ├─ 2. Create Server record (status: provisioning, scoped to tenant from API key)
         ├─ 3. Create Cloudflare A record → save dns_record_id
         ├─ 4. Write Traefik config to /traefik/conf.d/client-{subdomain}.yml
         ├─ 5. Start Docker containers (server + preview)
         └─ 6. Save container IDs → return server
                    │
                    │  (async, via Docker event stream)
                    ▼
         Docker health_status: healthy
                    │
                    ▼
         Server status → active
```

On any failure in steps 3–6, previously created resources are cleaned up (DNS record deleted, Traefik config removed, containers stopped).

### Docker Event Listener

A persistent background task started at API startup that subscribes to Docker container events:

- **`health_status: healthy`** — If the container belongs to a `provisioning` server, transitions status to `active`.
- **`health_status: unhealthy`** — Logged as a warning; waits for a `die` event.
- **`die`** — Waits 2 seconds (to let intentional suspend/delete commits arrive), then checks if the server is still `active`. If so, marks it `error` (unexpected crash).

On startup, the listener also runs a **recovery pass**: checks all `provisioning` servers and immediately marks any whose containers are already healthy. This handles cases where the API restarted while a container was starting up.

The event stream runs in a thread executor (Docker SDK is synchronous) and bridges events into the asyncio world via `asyncio.Queue + loop.call_soon_threadsafe`. Reconnects with exponential backoff (1s → 60s) if the stream drops.

### Traefik Config Generation

Each server gets a YAML file at `/traefik/conf.d/client-{subdomain}.yml` containing:

- **Routers** — `Host({subdomain}.{BASE_DOMAIN})` → server container; `Host(preview-{subdomain}.{BASE_DOMAIN})` → preview container
- **Services** — load balancer pointing to the Docker container by name
- **Middlewares** — rate limiting (configurable RPS), custom `X-Server-ID` header injection, and optional bot filter / GeoIP plugins

Files are written atomically via `tempfile.mkstemp` + `os.replace()` to prevent partial writes from disrupting other servers' routing.

---

## Data Models

### Tenant

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `name` | String | Display name |
| `created_at` | Timestamp | |

### Server

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | Foreign key → Tenant |
| `name` | String | Display name |
| `subdomain` | String(63) | Routing subdomain (unique among non-deleted) |
| `container_config` | String | Base64-encoded GTM workspace config |
| `status` | Enum | `provisioning`, `active`, `suspended`, `error`, `deleted` |
| `addons` | JSON | Feature flags and rate limit config |
| `gtm_container_id` | String | Extracted from container_config (e.g. `GTM-XXXXXX`) |
| `gtm_env` | Integer | GTM environment number, extracted from container_config |
| `dns_record_id` | String | Cloudflare DNS record ID (for deletion) |
| `server_container_id` | String | Docker container ID of GTM server |
| `preview_container_id` | String | Docker container ID of GTM preview server |
| `created_at` | Timestamp | |
| `updated_at` | Timestamp | Auto-updated on change |

### CustomDomain

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `server_id` | UUID | Foreign key → Server |
| `domain` | String | The custom domain (unique) |
| `status` | Enum | `pending_dns`, `pending_cert`, `active`, `failed` |
| `verified_at` | Timestamp | When CNAME was successfully verified |
| `created_at` | Timestamp | |

### ApiKey

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | Foreign key → Tenant |
| `name` | String | Human-readable label |
| `key_hash` | String | bcrypt hash of the raw key |
| `last_used_at` | Timestamp | Updated on each successful auth |
| `created_at` | Timestamp | |
