FROM docker.io/library/python:3.14@sha256:049247cbb7ce998685d780c9d5325781a7429bc6693bbfab86bcb8e36e7a059e
COPY --from=ghcr.io/astral-sh/uv:0.9.2@sha256:6dbd7c42a9088083fa79e41431a579196a189bcee3ae68ba904ac2bf77765867 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
