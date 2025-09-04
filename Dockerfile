FROM python:3.12-alpine3.20
LABEL maintainer="serve@scilifelab.se"

ARG USER=serve
ARG HOME=/home/$USER

WORKDIR $HOME

COPY requirements.txt .

RUN apk add --no-cache curl \
    && pip install --no-cache-dir --upgrade "pip~=25.2" \
    && pip install --no-cache-dir -r requirements.txt \
    && rm requirements.txt

RUN adduser -D $USER --home $HOME

COPY /serve_event_listener/ $HOME/

ENTRYPOINT [ "python3", "main.py" ]
