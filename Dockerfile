FROM docker.io/library/python:3.14@sha256:2febcd1e225a79391d5c9a7b416125af542ffd2f686201a58ea1d5595e110c4d
COPY --from=ghcr.io/astral-sh/uv:0.9.16@sha256:ae9ff79d095a61faf534a882ad6378e8159d2ce322691153d68d2afac7422840 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
