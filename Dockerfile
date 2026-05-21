FROM docker.io/library/python:3.14@sha256:6928095b5c67225857e608dd95271a81e8bb134e4bf7e7c305a9cdc2502a1d06
COPY --from=ghcr.io/astral-sh/uv:0.11.16@sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
