FROM docker.io/library/python:3.14@sha256:4bdca440e7381ba0d706e3718714c1a4cde97b460d8411c1af9c704bba1fba0f
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
