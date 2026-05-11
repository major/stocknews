FROM docker.io/library/python:3.14@sha256:511f025a0718f01b977d5ad572bc431745b31887ceccc538c85b87e82ad2956f
COPY --from=ghcr.io/astral-sh/uv:0.11.13@sha256:841c8e6fe30a8b07b4478d12d0c608cba6de66102d29d65d1cc423af86051563 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
