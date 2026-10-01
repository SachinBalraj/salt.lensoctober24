# SaltLens — Reproducibility Instructions

Everything in this evidence directory was produced by the environment fixed on 2026-09-30. The
commands below reproduce it. Paths are relative to the repository root.

```bash
# 0) Secrets — copy the template and fill in your own MONGODB_URI
cp backend/.env.example backend/.env        # then edit; .env is git-ignored

# 1) Backend servers (dev script starts uvicorn --reload on :8000)
scripts/dev.sh                               # backend; frontend is started separately

# 2) Health — must show connected=true indexes_ready=true error=null in a clean run
curl -s http://localhost:8000/api/health | python3 -m json.tool

# 3) Full backend test suite + lint
cd backend
.venv/bin/python -m pytest -p no:cacheprovider --tb=short -q    # 1086 collected, 1086 passed
.venv/bin/ruff check app tests                                   # All checks passed

# 4) Full frontend suite
cd ../frontend
npx vitest run        # 39 files / 816 tests
npx tsc --noEmit      # clean
npx eslint src/       # clean

# 5) OpenAPI surface (91 paths, 21 prefixes)
curl -s http://localhost:8000/openapi.json | python3 -c \
  "import json,sys; p=json.load(sys.stdin)['paths']; print(len(p))"

# 6) Live module statuses (each router declares its own state)
for m in pans observations weather twin simulator datasets ml evaluation risk explain \
         decisions feedback outcomes validation models experiments auth dashboard export; do
  printf '%-14s ' "$m"; curl -s "http://localhost:8000/api/$m/" | head -c 120; echo
done

# 7) Storage state (provenance of every row on the cluster)
curl -s http://localhost:8000/api/modules/ | python3 -m json.tool
# pans=2 observations=3 weather=3 outcomes=2 models=0 experiments=0 ml.trained=false evidential_models=0

# 8) Labelled demonstration (full transcript in labelled_demonstration.md)
```
See `docs/STAGE8_DEMO_GUIDE.md` for the ordered demonstration calls with expected status codes.

## Reproducing a specific §C claim

**"evidential_models = 0, no model artifact exists":** run step 6 `ml`, then
`ls -R backend/ml/models/` (empty of artifacts).

**"both outcomes are demonstration-labelled":**
```bash
curl -s http://localhost:8000/api/outcomes/ | python3 -m json.tool
# outcome-7fd2fb7015a642fd...  demo=true   post_salin=null (not comparable)
# outcome-c20ce3da5dec...      demo=true   post_salin=271.2
```

**"the comparison pairs are exactly as printed":**
```bash
curl -s 'http://localhost:8000/api/validation/pairs?comparison=forecast_vs_actual_rainfall'
curl -s 'http://localhost:8000/api/validation/pairs?comparison=simulated_vs_actual_salinity'
curl -s 'http://localhost:8000/api/validation/pairs?comparison=forecast_simulated_vs_actual_salinity'
```

**"health error never conflates index failure with unreachable DB":**
```bash
cd backend && .venv/bin/python -m pytest tests/test_database.py -q
# 14 passed, incl. test_an_index_provisioning_failure_is_not_reported_as_a_connection_failure
```

## Environment

- Backend Python 3.12 (uv). Numpy-style: none — the backend runs stdlib + FastAPI/pydantic stack.
- Frontend Node via the project's Next.js toolchain.
- MongoDB Atlas `saltlens` database on `cluster0.mzk9n8i.mongodb.net`.