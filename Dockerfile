FROM docker.io/library/python:3.14@sha256:2c25a316ddb08d471d24f6529b12e2b28c40c3ff63b9a397e659418ae93adb64
COPY --from=ghcr.io/astral-sh/uv:0.9.0@sha256:8f926a80debadba6f18442030df316c0e2b28d6af62d1292fb44b1c874173dc0 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
