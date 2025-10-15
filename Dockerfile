FROM docker.io/library/python:3.14@sha256:e3a6ccbe44d9cbfa4f107f238a0e95fa70e0d084e87689222e951d062ac89854
COPY --from=ghcr.io/astral-sh/uv:0.9.3@sha256:ecfea7316b266ba82a5e9efb052339ca410dd774dc01e134a30890e6b85c7cd1 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
