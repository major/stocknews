FROM docker.io/library/python:3.14@sha256:8676e2e7a07b736aeea297a13a42ab7b235940623a7fcd3815c336662ffe33c8
COPY --from=ghcr.io/astral-sh/uv:0.9.0@sha256:8f926a80debadba6f18442030df316c0e2b28d6af62d1292fb44b1c874173dc0 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
