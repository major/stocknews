FROM docker.io/library/python:3.14@sha256:8676e2e7a07b736aeea297a13a42ab7b235940623a7fcd3815c336662ffe33c8
COPY --from=ghcr.io/astral-sh/uv:0.9.2@sha256:6dbd7c42a9088083fa79e41431a579196a189bcee3ae68ba904ac2bf77765867 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
