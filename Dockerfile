FROM docker.io/library/python:3.14@sha256:595140b768992c05b096570e5ae487a02a2c6b3ae23ba33ce0fc34b99579c98e
COPY --from=ghcr.io/astral-sh/uv:0.9.15@sha256:4c1ad814fe658851f50ff95ecd6948673fffddb0d7994bdb019dcb58227abd52 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
