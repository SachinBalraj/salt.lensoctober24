# Architecture

SaltLens DSS is a modular research prototype. The foundation commit wires the
outer shell (frontend ↔ FastAPI ↔ optional MongoDB) and defines all 18 module
boundaries. Every domain module is registered as a *placeholder router* that
returns `501 Not Implemented` — the API surface exists, the research logic does
not yet, and the system never fabricates results.

## Stack

| Layer        | Technology                                                        |
| ------------ | ----------------------------------------------------------------- |
| Frontend     | Next.js 16 (App Router, Server Components) · TypeScript · Tailwind |
| Dashboard    | recharts (React charting)                                          |
| Backend      | Python 3.12 · FastAPI · Uvicorn · Pydantic v2 + settings           |
| Web client   | motor + pymongo (async)                                            |
| Persistence  | MongoDB Atlas (`mongodb+srv://...`) — optional at boot             |
| ML stack     | NumPy · pandas · scikit-learn · joblib · SHAP                      |
| Explainable  | SHAP (tree/linear explainers)                                      |
| Auth         | PBKDF2 password hashing (stdlib) + JWT (HS256) as later modules    |

## Repo layout

```
.
├── frontend/            Next.js 16 app (TypeScript, Tailwind, recharts)
│   ├── src/app/         App Router: layout, page, route handlers
│   ├── src/components/  Client components (health, module map, chart)
│   └── src/lib/         API client + typed helpers
├── backend/             FastAPI service
│   ├── app/
│   │   ├── main.py      app factory, CORS, lifespan (Mongo connect/close)
│   │   ├── config.py    pydantic-settings; every value from env/.env
│   │   ├── core/        security helpers (PBKDF2 hash/verify, JWT utils)
│   │   ├── db/          optional async MongoDB (motor) client
│   │   ├── models/      (module 1) Mongo entity models — TBD
│   │   ├── schemas/     shared Pydantic response schemas
│   │   ├── api/         FastAPI routers (one per planned module)
│   │   └── services/    domain logic (twin, mass balance, ML, SHAP, …)
│   └── tests/           pytest; run with `cd backend && uv run pytest`
├── ml/                  ML engineering (training/eval/features/notebooks)
├── data/                raw / processed / exports (git-ignored contents)
├── docs/                architecture, module map, API reference
├── tests/               future cross-stack integration/E2E suite
└── scripts/             dev.sh (run both apps), setup.sh (one-time install)
```

## Request flow (foundation)

```
Browser ──▶ Next.js 16 (localhost:3000)
              │  server components pre-render frame
              ▼  client components poll
              NEXT_PUBLIC_API_BASE_URL ──▶ FastAPI (localhost:8000)
                                              │
                                              ├── no secret values, ever
                                              ├── MongoDB optional (motor)
                                              │   └─ not configured → 503
                                              └── ML stack importable lazily
```

## Database policy

MongoDB is intentionally optional so the prototype boots without credentials
in CI and on a fresh machine. When `MONGODB_URI` is set, `app/db/mongo.py`
creates an async client, pings the cluster on startup, and exposes
`db.get_db()` behind a 503 guard. Persistence-heavy endpoints will return
`503 Database not configured` (never a fabricated default) as long as the
cluster is unavailable.

## Design decisions

1. **No fabricated research values.** The only "simulated" artifact in the
   UI is a placeholder chart *explicitly labelled* `SIMULATED DEMO`. All real
   measurements will come from user entry, imports, sensors, weather feeds or
   trained-model output.
2. **Placeholder routers, not stubs that lie.** Each router reports its own
   status (`not_implemented`) and the endpoints it will expose; clients can
   build navigation today without fake data.
3. **Lazy, optional ML imports.** `app/services/ml.py` and the training
   package import the heavy ML stack only when the relevant module is
   implemented, so the web API stays fast at boot.
4. **Secrets only via environment.** Config reads `.env`; `.env.example`
   files ship real keys as *placeholders*. A `.gitignore` (root) and the
   root `.env.*` rules below keep secrets out of version control.

## Environment files

- `backend/.env.example` → copy to `backend/.env`
- `frontend/.env.example` → copy to `frontend/.env.local`

`scripts/setup.sh` performs both copies automatically if absent.
