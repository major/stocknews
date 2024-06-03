FROM python:3.12-slim
ENV POETRY_VIRTUALENVS_CREATE=false
RUN pip install poetry
WORKDIR /code
COPY poetry.lock pyproject.toml /code/
RUN poetry install --no-interaction --no-ansi --no-root --no-dev
COPY stocknews /code/stocknews/
COPY run_bot.py /code/
RUN chmod +x /code/run_bot.py
CMD ["./run_bot.py"]
