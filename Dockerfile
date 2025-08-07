FROM docker.io/library/python:3.13@sha256:4ea77121eab13d9e71f2783d7505f5655b25bb7b2c263e8020aae3b555dbc0b2
COPY --from=ghcr.io/astral-sh/uv:0.8.6@sha256:6d9911b9f5703ed5f570d8032f2bfacc524e12f77d88e1e8f39eec742811a983 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
