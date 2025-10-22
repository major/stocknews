FROM docker.io/library/python:3.14@sha256:232ea2cc4a26b0ed8ae7f609e1d975d7f469ecdebd8e958410cf1ff1a9ff1be1
COPY --from=ghcr.io/astral-sh/uv:0.9.5@sha256:f459f6f73a8c4ef5d69f4e6fbbdb8af751d6fa40ec34b39a1ab469acd6e289b7 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
