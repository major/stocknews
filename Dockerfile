FROM docker.io/library/python:3.14@sha256:855caf820856a90fa94a8064cb247a72fcec4f67ebd3ec7c84cc3970da8a6526
COPY --from=ghcr.io/astral-sh/uv:0.9.4@sha256:c4089b0085cf4d38e38d5cdaa5e57752c1878a6f41f2e3a3a234dc5f23942cb4 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
