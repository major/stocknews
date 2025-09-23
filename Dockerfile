FROM docker.io/library/python:3.13@sha256:2deb0891ec3f643b1d342f04cc22154e6b6a76b41044791b537093fae00b6884
COPY --from=ghcr.io/astral-sh/uv:0.8.20@sha256:4e3bde91035d8d11cc1d5e4d1c273b895bb293575b8d23c3e5c6058eed2f1bb9 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
