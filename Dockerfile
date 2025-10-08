FROM python:3.12-alpine3.20
LABEL maintainer="serve@scilifelab.se"

# create non-root user
RUN addgroup -S serve && adduser -S -G serve serve

WORKDIR /app

COPY requirements.txt /app/requirements.txt

# install runtime deps (unversioned to avoid Alpine repo rotation breakage)
# hadolint ignore=DL3018
RUN apk add --no-cache curl jq \
 && pip install --no-cache-dir --upgrade "pip~=25.2" \
 && pip install --no-cache-dir -r requirements.txt \
 && rm requirements.txt

# copy source with correct ownership, owned by non-root user
COPY --chown=serve:serve serve_event_listener/ /app/serve_event_listener/

ENV PYTHONPATH=/app

USER serve

# run the package module so imports resolve properly
ENTRYPOINT ["python3", "-m", "serve_event_listener.main"]
# sensible defaults; can override container.args
CMD ["--namespace", "default", "--label-selector", "type=app"]
