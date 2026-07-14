# env/ — split configuration

The stack's environment is split into topical files so you only touch what you need.
Every service reads **all** of these (docker compose merges them); later files override earlier.

| file | what goes here | you touch it… |
|---|---|---|
| `app.env` | app behaviour, feature flags, model tiers, universe, tuning knobs | rarely (good defaults) |
| `gemini.env` | `GOOGLE_API_KEY` — the one Gemini key (agent + embeddings + eval) | once |
| `data-keys.env` | data-source API keys (OpenDART, ECOS, FRED, FMP, KIS, KRX, API Ninjas, DocAI…) | as you get keys |
| `auth-billing.env` | social login · email OTP · Toss billing · alert channels | for production |
| `secrets.env` | generated prod secrets (AUTH_SECRET, SERVICE_TOKEN, ADMIN_TOKEN, ADMINUI_*…) | for production |

## Setup

```bash
# option A — start fresh: copy each template and fill in
for f in env/*.env.example; do cp "$f" "${f%.example}"; done
#   then edit env/gemini.env, env/data-keys.env, …

# option B — already have a monolithic .env? split it automatically:
bash scripts/split_env.sh          # reads .env → writes env/*.env by topic
```

The real `env/*.env` files are gitignored (only the `*.example` templates are committed).

## Migrating from a root `.env`

The root `.env` is **no longer read** by docker compose (2026-07-14) — the `- path: .env` entries
were removed from every service, so all config must live in these `env/*.env` files. If you still
have a monolithic `.env`, split it in one shot:

```bash
bash scripts/split_env.sh          # reads .env → writes env/*.env by topic
```

## env_file vs. `${...}` interpolation (important)

These files are `env_file:` entries — they're injected **into the containers**. They are **invisible**
to docker compose's `${VAR}` **interpolation** in the compose file itself (ports, etc.), which compose
reads **only from the shell/CLI**. There is exactly one such var: the admin console's host port bind,
`${ADMIN_BIND}` (defaults to loopback `127.0.0.1`, SC-0.2). To expose the admin console you pass it on
the command line — it cannot come from an `env/*.env` file:

```bash
ADMIN_BIND=0.0.0.0 docker compose up -d admin
```

The full annotated reference of every variable lives in the `*.example` files here.
