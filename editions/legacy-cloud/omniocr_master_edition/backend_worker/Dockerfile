# Dockerfile for the OCR Worker (GPU enabled)
FROM nvidia/cuda:11.8.0-base-ubuntu22.04 # Base image with CUDA support

# Install Python, Tesseract, and dependencies
RUN apt-get update && apt-get install -y tesseract-ocr libtesseract-dev python3-pip

# Install Python dependencies (including PyTorch, OpenCV with CUDA support)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Set non-root user for security (Least Privilege Principle)
RUN useradd -m appuser
USER appuser

# Copy application code
COPY . /app
WORKDIR /app

# Command to run Celery worker
CMD ["celery", "-A", "worker_tasks", "worker", "-l", "info"]
