FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Railway injects env vars; .env file is optional
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
