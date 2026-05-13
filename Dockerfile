FROM docker.io/library/python:3.14@sha256:fe6ffb340c566fe7459832667da8b545896edb30c3dce0c3b1218ab4d377ba7d
COPY --from=ghcr.io/astral-sh/uv:0.11.14@sha256:1025398289b62de8269e70c45b91ffa37c373f38118d7da036fb8bb8efc85d97 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
