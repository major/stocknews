FROM docker.io/library/python:3.14@sha256:288157085f183c0000cb14cbf5c81d304f2dcdc4c386578a0c1bd214d178f7a4
COPY --from=ghcr.io/astral-sh/uv:0.11.12@sha256:3a59a3cdd5f7c217faa36e32dbc7fddbb0412889c2a0a5229f6d790e5a019dd7 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
