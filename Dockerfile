FROM docker.io/library/python:3.14@sha256:d29cf0828933ed271be9234ca2c2d578c16f2911451418aacc4525ac04ac7114
COPY --from=ghcr.io/astral-sh/uv:0.9.0@sha256:8f926a80debadba6f18442030df316c0e2b28d6af62d1292fb44b1c874173dc0 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
