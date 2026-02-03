FROM docker.io/library/python:3.14@sha256:c951a589819a647ef52c8609a8ecf50a4fa23eab030500e3d106f3644431ff30
COPY --from=ghcr.io/astral-sh/uv:0.9.29@sha256:db9370c2b0b837c74f454bea914343da9f29232035aa7632a1b14dc03add9edb /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
