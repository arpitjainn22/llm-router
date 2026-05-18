FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway/    ./gateway/
COPY classifier/ ./classifier/
COPY logger/     ./logger/
COPY start.py    ./start.py

RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["python", "start.py"]
