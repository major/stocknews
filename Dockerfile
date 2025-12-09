FROM docker.io/library/python:3.14@sha256:165e434a1a12549ed9b7cc0e4440c44ed5d7614af9d6df0e9f2395ae70f90ed4
COPY --from=ghcr.io/astral-sh/uv:0.9.16@sha256:ae9ff79d095a61faf534a882ad6378e8159d2ce322691153d68d2afac7422840 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
