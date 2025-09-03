FROM docker.io/library/python:3.13@sha256:18634e45b29c0dd1a9a3a3d0781f9f8a221fe32ee7a853db01e9120c710ef535
COPY --from=ghcr.io/astral-sh/uv:0.8.15@sha256:1eca97b33175f9c0896ec34f30e03ba0227efd8e38a9a0f6d12c6003eacb6faa /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
