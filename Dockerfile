FROM docker.io/library/python:3.14@sha256:934873f1360893d07afe0d25b99af46640e916a5900f1677fb86e41f73920253
COPY --from=ghcr.io/astral-sh/uv:0.9.5@sha256:f459f6f73a8c4ef5d69f4e6fbbdb8af751d6fa40ec34b39a1ab469acd6e289b7 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
