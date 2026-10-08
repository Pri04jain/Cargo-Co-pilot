FROM python:3.11-slim

# System deps: Tesseract for OCR, poppler for PDF rendering, gcc for compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create upload directory
RUN mkdir -p /data/uploads

# Make scripts executable
RUN chmod +x scripts/demo_data.py 2>/dev/null || true

EXPOSE 8000
