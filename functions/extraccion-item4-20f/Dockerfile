FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    FUNCTIONS_FRAMEWORK=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV FUNCTION_TARGET=procesar_bucket_edgar_20f

EXPOSE 8080

CMD ["functions-framework", "--target", "procesar_bucket_edgar_20f"]