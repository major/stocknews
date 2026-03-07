FROM docker.io/library/python:3.14@sha256:61346539f7b26521a230e72c11da5ebd872924745074b19736e7d65ba748c366
COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
