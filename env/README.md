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

## Back-compat

A single root `.env` **still works** — compose reads it too (all files are `required: false`).
So an existing deployment keeps running unchanged; migrate to `env/` whenever you like. If both
a value in `.env` and in an `env/*.env` file exist, the `env/*.env` value wins.

The full annotated reference of every variable lives in the `*.example` files here (and the old
monolithic list is preserved in the repo-root `.env.example`, which now just points here).
