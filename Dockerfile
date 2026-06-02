FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data

EXPOSE 8502

CMD ["uvicorn", "payment_gateway.main:app", "--host", "0.0.0.0", "--port", "8502"]
