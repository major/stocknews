FROM docker.io/library/python:3.14@sha256:0abd92bfb95474ec37a2348257752941e27ecbbaa6e42debc26a7a91d96bfbc4
COPY --from=ghcr.io/astral-sh/uv:0.9.24@sha256:816fdce3387ed2142e37d2e56e1b1b97ccc1ea87731ba199dc8a25c04e4997c5 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
