#!/bin/bash
# The hub under test: 2 server processes on MongoDB, with stand-ins for the AI engine, WhatsApp and the attendees' websites
# (lt_app.py). Development mode on localhost, so login codes show on screen and never go to a real phone.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../backend" || exit 1
export PYTHONPATH="$HERE:$PWD"
export APP_ENV=development APP_URL=http://localhost:8830 MONGO_URL="${MONGO_URL:-mongodb://127.0.0.1:27017}" DB_NAME="${DB_NAME:-loadtest}" \
  AI_PROVIDER=anthropic ANTHROPIC_API_KEY=simulated-engine-no-key ADMIN_PHONES=+919999900000 RUN_SCHEDULER=false FETCH_ALLOW_PRIVATE=true
exec python -m uvicorn lt_app:app --host 127.0.0.1 --port 8830 --workers "${WORKERS:-2}" --log-level warning --no-access-log
