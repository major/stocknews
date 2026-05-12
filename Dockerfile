FROM docker.io/library/python:3.14@sha256:2ca6cda25055227533141435ab8ec0dd3afd9165d78d8bf0f58c8d959d57b9fc
COPY --from=ghcr.io/astral-sh/uv:0.11.14@sha256:1025398289b62de8269e70c45b91ffa37c373f38118d7da036fb8bb8efc85d97 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
