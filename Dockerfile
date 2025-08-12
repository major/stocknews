FROM docker.io/library/python:3.13@sha256:68d0775234842868248bfe185eece56e725d3cb195f511a21233d0f564dee501
COPY --from=ghcr.io/astral-sh/uv:0.8.9@sha256:cda9608307dbbfc1769f3b6b1f9abf5f1360de0be720f544d29a7ae2863c47ef /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
