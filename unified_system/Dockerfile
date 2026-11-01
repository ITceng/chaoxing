# Build Python dependencies separately so build tooling and caches do not enter runtime.
ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE} AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements-prod.lock ./requirements-prod.lock
RUN python -m pip install --require-hashes --prefix=/install -r requirements-prod.lock

FROM ${PYTHON_BASE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    FLASK_APP=unified_system.app:create_app

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin appuser
WORKDIR /app
COPY --from=dependencies /install /usr/local
COPY --chown=10001:10001 . /app/unified_system
USER 10001:10001

EXPOSE 8088
HEALTHCHECK --interval=20s --timeout=4s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/api/v1/health/ready', timeout=3)" || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:8088", "--workers", "2", "--threads", "4", "--timeout", "60", "--graceful-timeout", "30", "--keep-alive", "5", "--max-requests", "1000", "--max-requests-jitter", "100", "--access-logfile", "-", "--error-logfile", "-", "--access-logformat", "%(h)s %(m)s %(U)s %(s)s %(B)s %(L)s", "unified_system.wsgi:app"]
