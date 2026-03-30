FROM python:3.11-slim

WORKDIR /app


RUN pip config set global.timeout 120 && \
    pip config set global.retries 5

    
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000

EXPOSE 8000

CMD ["python", "main.py"]
