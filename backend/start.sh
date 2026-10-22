#!/bin/sh
# Container start. With SEED_DEMO=true the demo hub (an owner on every plan, an admin, a cohort) is loaded first —
# only into an EMPTY database, so it can never overwrite real data. Then the API and the React app are served.
if [ "${SEED_DEMO:-false}" = "true" ]; then
  # The loader runs once, in-process (no network listener), with laptop settings so it can use test payments and demo
  # connections. It writes to the same MONGO_URL / DB_NAME and keeps JWT_SECRET and CONNECTIONS_KEY, so what it stores
  # is readable by the live hub. The hub itself still starts in production mode below.
  # The sending keys are left out for the loader, so the demo never sends real WhatsApp, SMS, e-mail or payments
  # (seed_demo.py refuses to run with them set).
  env -u RAILWAY_ENVIRONMENT -u RAILWAY_ENVIRONMENT_ID -u RAILWAY_ENVIRONMENT_NAME -u RAILWAY_PROJECT_ID \
      -u AISENSY_API_KEY -u MSG91_AUTH_KEY -u RAZORPAY_KEY_SECRET -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY EMAIL_PROVIDER= \
      APP_ENV=development APP_URL=http://localhost:8000 AI_PROVIDER=mock ADMIN_PHONES=+919999900000 RUN_SCHEDULER=false \
      python3 seed_demo.py --if-empty || echo "Demo data could not be loaded; starting the hub anyway."
fi
if [ -n "${DEMO_PASSWORD:-}" ]; then
  # Email + password logins for the demo accounts (admin@demo.hub, legacy@demo.hub, …). Safe to run on every start.
  python3 seed_demo.py --demo-logins || echo "Demo email logins could not be set."
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-2}"
