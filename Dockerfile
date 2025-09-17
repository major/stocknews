FROM docker.io/library/python:3.13@sha256:2deb0891ec3f643b1d342f04cc22154e6b6a76b41044791b537093fae00b6884
COPY --from=ghcr.io/astral-sh/uv:0.8.18@sha256:62022a082ce1358336b920e9b7746ac7c95b1b11473aa93ab5e4f01232d6712d /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
