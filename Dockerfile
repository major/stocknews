FROM docker.io/library/python:3.13@sha256:3efe6d5302c6131cbfbdb089c0dff7cf5a85ae5675c025df8488da10010acced
COPY --from=ghcr.io/astral-sh/uv:0.8.15@sha256:a5727064a0de127bdb7c9d3c1383f3a9ac307d9f2d8a391edc7896c54289ced0 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
