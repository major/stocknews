FROM docker.io/library/python:3.13@sha256:b9a26ed0117af0612457ffdbfb8973f2b5d88f3670d7e353ed7db0fda9e177c8
COPY --from=ghcr.io/astral-sh/uv:0.8.22@sha256:9874eb7afe5ca16c363fe80b294fe700e460df29a55532bbfea234a0f12eddb1 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
