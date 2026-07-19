# Control Plane

The platform control plane: **per-account data-source entitlements, metering, and a gateway** in
front of the `datasets/` data plane. An account (one `Project` per user — no Tenant parent, SIMPL-1)
activates the connectors they need (from the data plane's `/catalog`), gets scoped API keys, and all
data requests flow through this gateway which enforces activation, rate-limits, meters usage, and writes
an audit log.

```
account API key ─▶ control-plane gateway ─▶ (entitlement · rate-limit · meter · audit) ─▶ datasets data plane
```

## Run

```bash
cd control-plane
uv sync --extra dev
cp .env.example .env            # set DATASETS_URL to the running data plane, ADMIN_TOKEN, etc.
uv run uvicorn controlplane.main:app --reload --port 8001
uv run pytest -q
```

## Admin flow (guarded by X-Admin-Token)

```bash
A='-H X-Admin-Token:dev-admin-token'
curl $A -X POST localhost:8001/admin/projects -d '{"name":"Acme"}'                 # a project = an account
curl $A -X POST localhost:8001/admin/projects/<pid>/keys -d '{"name":"key1"}'      # returns the key ONCE
curl $A -X POST localhost:8001/admin/projects/<pid>/activations -d '{"connector_id":"sec_edgar"}'
curl $A localhost:8001/admin/projects/<pid>/usage
```

Then call the data plane *through* the gateway with the account key:

```bash
curl -H 'X-API-KEY: vgk_...' 'localhost:8001/company/facts?ticker=AAPL&market=US'   # 200 if sec_edgar activated, else 403
```

Defaults: SQLite (`controlplane.db`); in-memory rate-limit (Redis later). Set `DATABASE_URL` for Postgres.
