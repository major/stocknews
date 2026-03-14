FROM docker.io/library/python:3.14@sha256:7aea6827c8787754f99339ffed8cfc41fb09421f9c7d0e77a198b08422a3455e
COPY --from=ghcr.io/astral-sh/uv:0.10.10@sha256:cbe0a44ba994e327b8fe7ed72beef1aaa7d2c4c795fd406d1dbf328bacb2f1c5 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
