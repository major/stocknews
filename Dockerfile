FROM docker.io/library/python:3.13@sha256:18634e45b29c0dd1a9a3a3d0781f9f8a221fe32ee7a853db01e9120c710ef535
COPY --from=ghcr.io/astral-sh/uv:0.8.15@sha256:a5727064a0de127bdb7c9d3c1383f3a9ac307d9f2d8a391edc7896c54289ced0 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
