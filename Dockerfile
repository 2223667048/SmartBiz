FROM python:3.12-slim
WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY scripts ./scripts
