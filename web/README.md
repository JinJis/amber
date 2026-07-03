# Web (ValueGraph)

A Claude-style chat over the platform: ask freely about holdings, news, markets, the economy — the
agent answers using our data (connectors + RAG via the gateway) and shows **tools & sources**. Next.js
(App Router) + Auth.js (Google), with a dev-login fallback for local use.

```
browser ─▶ /api/chat (BFF: Auth.js session) ─▶ studio-api /chat/stream ─▶ agent-engine (SSE) ─▶ tools via gateway
```

The browser never holds a platform key — the BFF passes a service token + the user's email to studio-api,
which holds the tenant key server-side.

## Run (local)

```bash
cd web
cp .env.example .env.local      # set STUDIO_API_URL + SERVICE_TOKEN; AUTH_DEV_LOGIN=true for no-Google login
npm install
npm run dev                     # http://localhost:3000  (studio-api must be up on :8004)
```

For Google login set `AUTH_GOOGLE_ID` / `AUTH_GOOGLE_SECRET` / `AUTH_SECRET` (and remove `AUTH_DEV_LOGIN`).
For streamed answers, run the agent engine with `AGENT_LLM_BACKEND=gemini` + `GOOGLE_API_KEY`
(the platform is Gemini-only — there is no keyless planner; without a key the agent can't run).

Bring up the backend first: `docker compose up --build` (datasets, control-plane, rag,
agent-engine, studio-api).
