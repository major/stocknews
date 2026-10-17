FROM registry.access.redhat.com/ubi9/python-314:latest@sha256:c19e852fe0c935dad0ecc2ef6a90582612e57ed4653ca32ec974606637520ce2 AS builder

WORKDIR /opt/app-root/src

COPY pyproject.toml uv.lock .python-version ./
COPY src ./src

RUN python3 -m pip install --no-cache-dir uv==0.12.18 \
    && uv sync --locked --no-dev --no-editable --python python3.14

FROM registry.access.redhat.com/ubi9/python-314:latest@sha256:c19e852fe0c935dad0ecc2ef6a90582612e57ed4653ca32ec974606637520ce2

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}"

WORKDIR /opt/app-root/src

COPY --from=builder --chown=1001:0 /opt/app-root/src/.venv /opt/app-root/src/.venv

ENV PATH="/opt/app-root/src/.venv/bin:${PATH}"
ENV GIT_SHA="${GIT_SHA}"

USER 1001:0
ENTRYPOINT ["/opt/app-root/src/.venv/bin/stocknews"]
