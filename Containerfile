FROM docker.io/library/python:3.15.1-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51 AS builder

WORKDIR /opt/app-root/src

COPY pyproject.toml uv.lock .python-version ./
COPY src ./src

RUN python3 -m pip install --no-cache-dir uv==0.13.5 \
    && uv sync --locked --no-dev --no-editable --python python3.15

FROM docker.io/library/python:3.15.1-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51

ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}"

WORKDIR /opt/app-root/src

RUN useradd --uid 1001 --gid 0 --home-dir /opt/app-root/src --no-create-home stocknews \
    && mkdir -p /opt/app-root/src/.stocknews \
    && chown 1001:0 /opt/app-root/src/.stocknews

COPY --from=builder --chown=1001:0 /opt/app-root/src/.venv /opt/app-root/src/.venv

ENV PATH="/opt/app-root/src/.venv/bin:${PATH}"
ENV GIT_SHA="${GIT_SHA}"
ENV BUILD_DATE="${BUILD_DATE}"

USER 1001:0
ENTRYPOINT ["/opt/app-root/src/.venv/bin/stocknews"]
