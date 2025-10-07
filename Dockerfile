FROM docker.io/library/python:3.13@sha256:fe841081ec55481496a4ab25e538833741295d57d2abdec8d38d74d65fb4715b
COPY --from=ghcr.io/astral-sh/uv:0.8.23@sha256:94390f20a83e2de83f63b2dadcca2efab2e6798f772edab52bf545696c86bdb4 /uv /uvx /bin/

ADD . /app
WORKDIR /app
RUN uv sync --locked

CMD ["uv", "run", "run_bot.py"]
