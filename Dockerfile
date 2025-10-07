FROM docker.io/library/python:3.13@sha256:fe841081ec55481496a4ab25e538833741295d57d2abdec8d38d74d65fb4715b
COPY --from=ghcr.io/astral-sh/uv:0.8.24@sha256:1d31be550ff927957472b2a491dc3de1ea9b5c2d319a9cea5b6a48021e2990a6 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
