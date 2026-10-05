FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir starlette uvicorn tzdata

# Copy application
COPY app.py .

ENV PORT=8080

CMD ["sh", "-c", "exec python -m uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
