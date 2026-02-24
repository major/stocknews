FROM docker.io/library/python:3.14@sha256:151ab3571dad616bb031052e86411e2165295c7f67ef27206852203e854bcd12
COPY --from=ghcr.io/astral-sh/uv:0.10.5@sha256:476133fa2aaddb4cbee003e3dc79a88d327a5dc7cb3179b7f02cabd8fdfbcc6e /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
