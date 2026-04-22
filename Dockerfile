FROM docker.io/library/python:3.14@sha256:ef1efc7276a14c09645bf6fdb0a9f11065dab3d30c80d9a1a7dc7f1e66be8398
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
