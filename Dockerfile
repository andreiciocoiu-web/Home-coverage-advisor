FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ron tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -r -u 10001 app && mkdir -p /app/data && chown -R app /app/data
USER app
ENV HCA_DATA_DIR=/app/data
EXPOSE 8000
CMD ["uvicorn", "advisor.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
