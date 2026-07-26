# OmniOCR Instruction Manual

Welcome to the OmniOCR workspace. This document provides an architectural mapping of the repository, key execution commands for the various editions, development conventions, and instructions for future AI agents operating in this workspace.

---

## 🏗️ Project Overview & Architecture

OmniOCR is a multi-edition, high-performance Optical Character Recognition suite specialized in document layout reconstruction and polytonic/monotonic Greek (and English) text extraction. The codebase is organized into modular "Editions", each addressing a specific hosting, execution, and deployment paradigm.

### System-Wide Directory Structure
- **Root**: `ocr.py` - A standalone, rapid-prototyping polytonic Streamlit app for OCR and layout preservation.
- **`Cloud Edition/`**: The enterprise-grade master deployment of OmniOCR featuring full separation of concerns, containerization, and orchestration.
- **`Desktop Edition/`**: A trainer-focused distribution for fine-tuning OCR engines, monitoring metrics, and managing training sets.
- **`Server Standalone Edition/`**: A lightweight self-hosted deployment running as a single-node API server with static frontend files and local queues.

### Core Architecture (`app_core`)
Both the **Cloud Edition** and the **Server Standalone Edition** (and partially the **Desktop Edition**) employ a clean **Domain-Driven Design (DDD)** and **Clean Architecture** approach. This core is structured into:
1. **Domain (`app_core/domain.py`)**: Immutable models, dataclasses (such as `OCRBlock`, `TenantContext`), and data contracts.
2. **Interfaces (`app_core/interfaces.py`)**: Abstract Base Classes (ABCs) defining structural contracts (e.g., `IImageProcessor`, `IOCREngine`, `IOCRStrategy`).
3. **Engines (`app_core/engines.py`)**: Concrete implementations of engines (e.g., `TesseractEnsembleEngine` with asyncio thread-pooling, and `DeepLearningOCRStrategy` for PyTorch/HuggingFace GPU acceleration).
4. **Services (`app_core/services.py`)**: Business orchestrations, including security audits, circuit breakers, and layout reconstructions.

---

## 🚀 Building, Running, and Testing

Each edition contains its own virtual environment requirements and run commands:

### 1. Root Standalone CLI & Streamlit App (`ocr.py`)
Provides GPU-accelerated image preprocessing (using OpenCV Transparent API/UMat), ensemble voting logic between multiple segmentation modes, and exports to DOCX and Searchable PDF.
- **Dependencies**: Located in any of the child editions, but requires: `numpy`, `opencv-python`, `pytesseract`, `torch`, `streamlit`, `python-docx`, `reportlab`, `Pillow`.
- **Run Command**:
  ```bash
  streamlit run ocr.py
  ```

### 2. Cloud Edition (`Cloud Edition/omniocr_master_edition`)
An enterprise distributed OCR engine using FastAPI, Streamlit, Celery, Redis, and PyTorch.
- **Frontend UI (Streamlit)**:
  ```bash
  cd "Cloud Edition/omniocr_master_edition"
  streamlit run frontend_ui/app.py
  ```
- **Backend Worker / API**:
  ```bash
  cd "Cloud Edition/omniocr_master_edition"
  uvicorn backend_worker.worker_api:app --host 0.0.0.0 --port 8000
  ```
- **Celery Worker Tasks**:
  Requires Redis as the broker. (Run via Celery commands matching deployment configurations).
- **Docker Compose**:
  Contains deployment orchestration files in `orchestration/docker-compose.yml`.

### 3. Desktop Trainer Edition (`Desktop Edition/omniocr_trainer_edition`)
Specifically designed for engine fine-tuning and annotating datasets. Integrates with MLflow for experiment tracking.
- **Run Command**:
  ```bash
  cd "Desktop Edition/omniocr_trainer_edition"
  streamlit run trainer_ui/app.py
  ```
- **Prerequisites**: Ensure Redis and MLflow server are reachable if experiment logging is enabled.

### 4. Server Standalone Edition (`Server Standalone Edition/omniocr_server_edition`)
A self-hosted service bundling a fast REST API with static web pages and light-weight Python RQ (Redis Queue) background task managers.
- **API Server with Static Assets**:
  ```bash
  cd "Server Standalone Edition/omniocr_server_edition"
  uvicorn backend_api.main:app --host 0.0.0.0 --port 8000
  ```
- **RQ Queue Worker**:
  Requires a separate terminal session running:
  ```bash
  cd "Server Standalone Edition/omniocr_server_edition"
  rq worker --url redis://localhost:6379
  ```

---

## 🛠️ Development Conventions & Engineering Standards

When contributing code, modifying existing services, or introducing new engines, strictly adhere to the following standards:

1. **Separation of Concerns & DDD**:
   - Never write database/infrastructure or raw OpenCV/Pytesseract commands directly into the Streamlit UI or FastAPI endpoint controllers.
   - Always map data into `app_core/domain.py` dataclasses.
   - Inject concrete engines via structural interfaces (`app_core/interfaces.py`).
2. **Asynchronous Patterns**:
   - CPU-bound or blocking operations (such as Tesseract execution) must be delegated using asynchronous thread-pooling (`asyncio.to_thread` or standard executors like `ThreadPoolExecutor`) or task queues (`Celery`, `RQ`) to avoid blocking the main server loop.
3. **Hardware Acceleration Management**:
   - Utilize standard device detection through `torch.cuda.is_available()` or OpenCV `UMat` (`cv2.ocl.haveOpenCL()`) to leverage hardware acceleration if available.
4. **Encoding and Normalization**:
   - Polytonic Greek texts can contain a variety of character combinations. Ensure all final text extractions and layout reconstructions are normalized using `unicodedata.normalize('NFC', ...)` before outputting or serving them.
5. **No Suppression Hacks**:
   - Keep structural typings strict. Avoid `Any` or `cast` suppression unless critically necessary. Use explicit type guards.
