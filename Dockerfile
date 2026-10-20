# Business AI Action Hub — one container: builds the React app, then serves it with the API.
# Railway detects this file automatically. Set the environment variables listed in backend/.env.example.

FROM node:22-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# FriBiDi lets Pillow's Raqm shape Hindi and Marathi correctly in brand-kit designs
RUN apt-get update && apt-get install -y --no-install-recommends libfribidi0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=web /app/frontend/dist frontend/dist
WORKDIR /app/backend
RUN useradd --create-home hub && chown -R hub /app
USER hub
EXPOSE 8000
# Railway sets PORT. Two workers are plenty for a workshop room; all state lives in MongoDB.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
