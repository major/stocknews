FROM docker.io/library/python:3.13@sha256:c1dab8c06c4fe756fd4316f33a2ba4497be09fa106d2cd9782c33ee411193f9c
COPY --from=ghcr.io/astral-sh/uv:0.8.17@sha256:e4644cb5bd56fdc2c5ea3ee0525d9d21eed1603bccd6a21f887a938be7e85be1 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
