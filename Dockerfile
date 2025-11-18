FROM docker.io/library/python:3.14@sha256:29f6dd673b2788db9eef6bb8896ee6cec7b4506a63fe4a90a9258d3b82221a2a
COPY --from=ghcr.io/astral-sh/uv:0.9.10@sha256:29bd45092ea8902c0bbb7f0a338f0494a382b1f4b18355df5be270ade679ff1d /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
