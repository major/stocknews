FROM docker.io/library/python:3.13@sha256:68d0775234842868248bfe185eece56e725d3cb195f511a21233d0f564dee501
COPY --from=ghcr.io/astral-sh/uv:0.8.6@sha256:6d9911b9f5703ed5f570d8032f2bfacc524e12f77d88e1e8f39eec742811a983 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
