FROM docker.io/library/python:3.13@sha256:2deb0891ec3f643b1d342f04cc22154e6b6a76b41044791b537093fae00b6884
COPY --from=ghcr.io/astral-sh/uv:0.8.21@sha256:ca74b4b463d7dfc1176cbe82a02b6e143fd03a144dcb1a87c3c3e81ac16c6f6d /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
