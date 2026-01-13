FROM docker.io/library/python:3.14@sha256:99536892f722b2a8f83c7b3a1e26734e1c183aa914f6cad1d89d9adb68b4dd90
COPY --from=ghcr.io/astral-sh/uv:0.9.24@sha256:816fdce3387ed2142e37d2e56e1b1b97ccc1ea87731ba199dc8a25c04e4997c5 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
