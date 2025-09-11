FROM python:3.12-alpine3.20
LABEL maintainer="serve@scilifelab.se"

RUN addgroup -S serve && adduser -S -G serve serve

WORKDIR /app

COPY requirements.txt /app/requirements.txt

# Install runtime deps
RUN apk add --no-cache \
        curl=8.12.1-r0 \
        jq=1.7.1-r0 \
    && pip install --no-cache-dir --upgrade "pip~=25.2" \
    && pip install --no-cache-dir -r requirements.txt \
    && rm requirements.txt

# Copy source, owned by non-root user
COPY --chown=serve:serve serve_event_listener/ /app/serve_event_listener/

# Make sure the package root is importable
ENV PYTHONPATH=/app

# Drop privileges for runtime
USER serve

# Run the package module so imports resolve properly
ENTRYPOINT ["python3", "-m", "serve_event_listener.main"]

# Sensible defaults; can override container.args
CMD ["--namespace", "default", "--label-selector", "type=app"]
