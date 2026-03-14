FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Render sets PORT env var automatically
ENV PORT=8000

EXPOSE 8000

CMD ["python", "main.py"]
