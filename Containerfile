FROM registry.access.redhat.com/ubi9/python-314:latest@sha256:a041f081854d50fa8055fe8cc75d1d7446158104c66d4f901206df84269f0c32 AS builder

WORKDIR /opt/app-root/src

COPY pyproject.toml uv.lock .python-version ./
COPY src ./src

RUN python3 -m pip install --no-cache-dir uv==0.12.18 \
    && uv sync --locked --no-dev --no-editable --python python3.14

FROM registry.access.redhat.com/ubi9/python-314:latest@sha256:a041f081854d50fa8055fe8cc75d1d7446158104c66d4f901206df84269f0c32

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}"

WORKDIR /opt/app-root/src

COPY --from=builder --chown=1001:0 /opt/app-root/src/.venv /opt/app-root/src/.venv

ENV PATH="/opt/app-root/src/.venv/bin:${PATH}"
ENV GIT_SHA="${GIT_SHA}"

USER 1001:0
ENTRYPOINT ["/opt/app-root/src/.venv/bin/stocknews"]
