FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Baked in at build time (see RELEASING.md) — defaults keep everyday
# `docker-compose up --build` unaffected when nothing is exported.
ARG APP_VERSION=dev
ARG GIT_COMMIT=unknown
ENV APP_VERSION=${APP_VERSION}
ENV GIT_COMMIT=${GIT_COMMIT}

# Run migrations then start the server
CMD ["sh", "-c", "alembic upgrade head && gunicorn app.main:app -w ${WEB_CONCURRENCY:-4} -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 60 --graceful-timeout 30"]
