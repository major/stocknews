FROM docker.io/library/python:3.13@sha256:a66f18ee22c568a3d45191dfd70bdea2e1bd8d303f982ea1bca276a065285a21
COPY --from=ghcr.io/astral-sh/uv:0.8.9@sha256:cda9608307dbbfc1769f3b6b1f9abf5f1360de0be720f544d29a7ae2863c47ef /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
