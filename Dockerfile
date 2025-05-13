# From https://fastapi.tiangolo.com/deployment/docker/#docker-image-with-poetry
FROM docker.io/library/python:3.13

ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

RUN pip install -U pip poetry

WORKDIR /app
COPY ./pyproject.toml ./poetry.lock* README.md sp1500_stocks.json ./
COPY ./stocknews ./stocknews
COPY ./run_bot.py ./run_bot.py

RUN poetry install

CMD ["/app/.venv/bin/python", "run_bot.py"]
