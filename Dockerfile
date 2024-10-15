# From https://fastapi.tiangolo.com/deployment/docker/#docker-image-with-poetry
FROM python:3.13 AS requirements-stage
WORKDIR /app

RUN pip install -U pip poetry
COPY ./pyproject.toml ./poetry.lock* /

RUN poetry config virtualenvs.create false
RUN poetry install --only main --no-root

COPY ./stocknews /app/stocknews
COPY ./run_bot.py /app/run_bot.py
CMD ["./run_bot.py"]
