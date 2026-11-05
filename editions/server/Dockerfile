FROM python:3.12-slim

# Tesseract with the Greek packs: the ensemble's baseline engine and the source
# of the word boxes that ground every other voter.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ell tesseract-ocr-grc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# The source goes in before the install: a non-editable install copies the
# package into site-packages, so it must see the code it is installing.
COPY . .
RUN pip install --no-cache-dir ".[server,pdf,kraken,opencv,tesseract]"

# Uploads and results live on a volume shared with the worker.
RUN useradd --create-home --uid 10001 omniocr \
    && mkdir -p /data/uploads /data/results \
    && chown -R omniocr /data
USER omniocr

CMD ["uvicorn", "editions.server.main:app", "--host", "0.0.0.0", "--port", "8000"]
