FROM docker.io/library/python:3.14@sha256:e3a6ccbe44d9cbfa4f107f238a0e95fa70e0d084e87689222e951d062ac89854
COPY --from=ghcr.io/astral-sh/uv:0.9.4@sha256:35aca64ac15f61941da93e92ebfb22220359e95056e6a827f4aef2235c4d353f /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
