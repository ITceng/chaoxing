ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE} AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements-prod.lock ./requirements-prod.lock
RUN python -m pip install --require-hashes --prefix=/install -r requirements-prod.lock

FROM ${PYTHON_BASE} AS runtime

ARG WORKER_PLATFORM=generic
LABEL io.unified-learning.worker-platform="${WORKER_PLATFORM}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN groupadd --gid 10001 worker \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin worker
WORKDIR /app
COPY --from=dependencies /install /usr/local
COPY --chown=10001:10001 . /app/unified_system
USER 10001:10001

HEALTHCHECK --interval=20s --timeout=3s --start-period=45s --retries=3 \
  CMD python -m unified_system.workers.runtime.healthcheck || exit 1

CMD ["python", "-m", "unified_system.workers.runtime.entrypoint"]
