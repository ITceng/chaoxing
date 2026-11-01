FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt /tmp/unified-requirements.txt
RUN pip install --no-cache-dir -r /tmp/unified-requirements.txt \
    && useradd --system --uid 10001 --create-home worker
COPY . /app/unified_system
ENV PYTHONPATH=/app
USER 10001:10001
CMD ["python", "-m", "unified_system.workers.runtime.entrypoint"]
