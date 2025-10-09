FROM docker.io/library/python:3.14@sha256:5f3ca57e8d3d5deee7606c1a2891eff5da2dc8fa71168f9471e955c6e79564eb
COPY --from=ghcr.io/astral-sh/uv:0.9.0@sha256:8f926a80debadba6f18442030df316c0e2b28d6af62d1292fb44b1c874173dc0 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
