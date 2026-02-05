FROM docker.io/library/python:3.14@sha256:1c4c033d666001d84b5c4d85280af2c5a21a4ea1eb86c2fc06e3ef2f33fa6776
COPY --from=ghcr.io/astral-sh/uv:0.9.29@sha256:db9370c2b0b837c74f454bea914343da9f29232035aa7632a1b14dc03add9edb /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
