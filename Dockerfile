FROM docker.io/library/python:3.13@sha256:0c745292b7b34dcdd6050527907d78c39363dc45ad6afc6d107c454b93cebca1
COPY --from=ghcr.io/astral-sh/uv:0.8.22@sha256:9874eb7afe5ca16c363fe80b294fe700e460df29a55532bbfea234a0f12eddb1 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
