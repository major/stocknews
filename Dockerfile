FROM docker.io/library/python:3.13@sha256:a66f18ee22c568a3d45191dfd70bdea2e1bd8d303f982ea1bca276a065285a21
COPY --from=ghcr.io/astral-sh/uv:0.8.10@sha256:b05b3d61eb2b264ed785265b71155738a0d3d382ea0699e048d4b36f90b88788 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
