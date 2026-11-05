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
RUN pip install --no-cache-dir ".[cloud,pdf,kraken,opencv,tesseract]"

# Settings defaults the model path to "", which Kraken reads as /app and cannot load.
# This is the default declared in models/manifest.json.
ENV OMNIOCR_KRAKEN_MODEL_PATH=/app/models/greek-german_serifs_bsb10234118.mlmodel

# Uploads and results live on a volume shared with the worker.
RUN useradd --create-home --uid 10001 omniocr \
    && mkdir -p /data/uploads /data/results \
    && chown -R omniocr /data
USER omniocr

CMD ["uvicorn", "editions.cloud.main:app", "--host", "0.0.0.0", "--port", "8000"]
