FROM docker.io/library/python:3.14@sha256:5b95b240f2db781f34a5da81e6e2301378495b3ab78d689df325c937be267e1c
COPY --from=ghcr.io/astral-sh/uv:0.9.0@sha256:8f926a80debadba6f18442030df316c0e2b28d6af62d1292fb44b1c874173dc0 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
