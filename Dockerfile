FROM docker.io/library/python:3.14@sha256:232ea2cc4a26b0ed8ae7f609e1d975d7f469ecdebd8e958410cf1ff1a9ff1be1
COPY --from=ghcr.io/astral-sh/uv:0.9.4@sha256:c4089b0085cf4d38e38d5cdaa5e57752c1878a6f41f2e3a3a234dc5f23942cb4 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
