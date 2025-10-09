FROM docker.io/library/python:3.14@sha256:8676e2e7a07b736aeea297a13a42ab7b235940623a7fcd3815c336662ffe33c8
COPY --from=ghcr.io/astral-sh/uv:0.9.1@sha256:3b368e735c0227077902233a73c5ba17a3c2097ecdd83049cbaf2aa83adc8a20 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
