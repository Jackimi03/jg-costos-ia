FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home jg && mkdir /app/data && chown jg /app/data
COPY jg ./jg
USER jg
EXPOSE 8000
CMD ["uvicorn", "jg.api:app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
