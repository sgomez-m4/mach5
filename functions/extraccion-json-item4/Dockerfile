FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    FUNCTIONS_FRAMEWORK=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV FUNCTION_TARGET=extract_fleet_20f_http

EXPOSE 8080

CMD ["functions-framework", "--target", "extract_fleet_20f_http"]