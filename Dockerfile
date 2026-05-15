FROM docker.io/library/python:3.14@sha256:89a288a9a9e9141b9f0c51744c358138da6369897792f1af3f5425e407d9529a
COPY --from=ghcr.io/astral-sh/uv:0.11.14@sha256:1025398289b62de8269e70c45b91ffa37c373f38118d7da036fb8bb8efc85d97 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
