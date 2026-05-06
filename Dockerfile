FROM docker.io/library/python:3.14@sha256:0ba001803c72c128063cfa88863755f905cefabe73c026c66a5a86d8f1d63e98
COPY --from=ghcr.io/astral-sh/uv:0.11.11@sha256:798712e57f879c5393777cbda2bb309b29fcdeb0532129d4b1c3125c5385975a /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
