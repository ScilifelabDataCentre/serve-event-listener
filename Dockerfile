FROM python:alpine3.19
LABEL maintainer="serve@scilifelab.se"

ARG USER=serve
ARG HOME=/home/$USER

WORKDIR $HOME

COPY requirements.txt .

RUN apk add --update --no-cache \
    && pip install --no-cache-dir --upgrade pip==23.3.2\
    && pip install --no-cache-dir -r requirements.txt \
    && rm requirements.txt

RUN adduser -D $USER --home $HOME

COPY /src/ $HOME/
