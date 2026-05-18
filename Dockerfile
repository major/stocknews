FROM docker.io/library/python:3.14@sha256:89a288a9a9e9141b9f0c51744c358138da6369897792f1af3f5425e407d9529a
COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
