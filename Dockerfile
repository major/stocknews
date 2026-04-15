FROM docker.io/library/python:3.14@sha256:63fdc2bca05b142ef64d05e8f9dbc4e8bf9f51294824db5c3d8b10f419fe1bdd
COPY --from=ghcr.io/astral-sh/uv:0.11.6@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
