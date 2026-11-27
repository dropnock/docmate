# The in-app manuals (repo manuals/, passed in as the "manuals" build context
# by docker-compose.yml): Markdown + brand/brand.yaml + templates/ → HTML.
FROM python:3.11-slim AS manuals
RUN apt-get update && apt-get install -y --no-install-recommends pandoc \
    && rm -rf /var/lib/apt/lists/*
COPY --from=manuals requirements.txt /manuals/requirements.txt
RUN pip install --no-cache-dir -r /manuals/requirements.txt
COPY --from=manuals . /manuals
ARG APP_VERSION=dev
RUN python /manuals/build.py --out /manuals_dist --version "${APP_VERSION}"

FROM python:3.11-slim

WORKDIR /app

# WeasyPrint (manual PDFs, services/help_service.py) needs Pango.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=manuals /manuals_dist /app/manuals_dist

# Baked in at build time (see RELEASING.md) — defaults keep everyday
# `docker-compose up --build` unaffected when nothing is exported.
ARG APP_VERSION=dev
ARG GIT_COMMIT=unknown
ENV APP_VERSION=${APP_VERSION}
ENV GIT_COMMIT=${GIT_COMMIT}

# Run migrations then start the server
CMD ["sh", "-c", "alembic upgrade head && gunicorn app.main:app -w ${WEB_CONCURRENCY:-4} -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 60 --graceful-timeout 30"]
