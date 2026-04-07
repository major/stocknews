FROM docker.io/library/python:3.14@sha256:0b45a7265e3d6f3cbe81b09907772245c4c319b8bc8fb9f5a4088cd2b7ba66c6
COPY --from=ghcr.io/astral-sh/uv:0.11.3@sha256:90bbb3c16635e9627f49eec6539f956d70746c409209041800a0280b93152823 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
