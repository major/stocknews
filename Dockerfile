FROM docker.io/library/python:3.14@sha256:2febcd1e225a79391d5c9a7b416125af542ffd2f686201a58ea1d5595e110c4d
COPY --from=ghcr.io/astral-sh/uv:0.9.17@sha256:5cb6b54d2bc3fe2eb9a8483db958a0b9eebf9edff68adedb369df8e7b98711a2 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
