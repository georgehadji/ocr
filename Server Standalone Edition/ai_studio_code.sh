#!/bin/bash
# SCRIPT: create_omniocr_server_edition.sh
# Δημιουργεί τη δομή για ανάπτυξη σε παραδοσιακό server (FastAPI/RQ).

echo "Δημιουργία δομής φακέλων OmniOCR Server Edition..."

mkdir -p omniocr_server_edition/{app_core,backend_api,frontend_static/{css,js},data/{models,logs}}

cd omniocr_server_edition

# 1. Κεντρικές εξαρτήσεις (Προσθήκη FastAPI, Uvicorn, RQ)
cat <<EOF > requirements.txt
# Core Dependencies (Same as Master Edition)
numpy
opencv-python
pytesseract
torch
Pillow
reportlab
python-docx

# Server/API/Queue
fastapi
uvicorn
redis
rq # Redis Queue for local async tasks
python-multipart # For file uploads
EOF

# 2. App Core (Διατηρείται η Clean Architecture)
# (Τα αρχεία app_core/domain.py, interfaces.py, engines.py, services.py παραμένουν ίδια)
# ... (Δημιουργία των αρχείων app_core/*) ...

# 3. Backend API (FastAPI)
cat <<EOF > backend_api/main.py
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from rq import Queue
from redis import Redis
import asyncio
import base64
import logging
# Import core logic
from app_core.services import OCRService
from app_core.domain import TenantContext
# ... (Import Engines, Processors) ...

logger = logging.getLogger(__name__)

# Initialize Redis Queue (RQ)
redis_conn = Redis(host='localhost', port=6379) # Assumes Redis is running locally
task_queue = Queue(connection=redis_conn)

app = FastAPI(title="OmniOCR Server API")

# Mount static files for the Frontend UI
app.mount("/static", StaticFiles(directory="frontend_static"), name="static")

# Root endpoint serves the main HTML file
@app.get("/")
async def serve_frontend():
    with open("frontend_static/index.html", "r") as f:
        return f.read()

# Dependency to simulate authentication
def get_tenant_context_dependency():
    # In a real app, this reads JWT token from header
    return TenantContext(organization_id="SERVER_ORG", user_id="web_user", subscription_tier="Pro")

@app.post("/api/v1/submit_ocr")
async def submit_ocr(
    file: UploadFile = File(...),
    context: TenantContext = Depends(get_tenant_context_dependency)
):
    if file.content_type not in ["image/jpeg", "image/png"]:
        raise HTTPException(status_code=400, detail="Invalid file type.")

    image_bytes = await file.read()
    
    # Initialize the service (Dependency Injection)
    # Note: In a real app, this setup is done once at startup
    image_processor = OpenCVImageProcessor()
    layout_analyzer = LayoutAnalyzer()
    local_strategy = LocalOCRStrategy(image_processor, layout_analyzer)
    ocr_service = OCRService(local_strategy)
    
    # Submit the heavy OCR task to the RQ worker
    # We pass the bytes and context to the worker function
    job = task_queue.enqueue(
        ocr_service.execute_ocr, 
        image_bytes, 
        'grc+ell+eng', 
        context,
        job_timeout='10m' # Allow up to 10 minutes for complex OCR
    )
    
    return {"task_id": job.id, "status": "queued"}

@app.get("/api/v1/task_status/{task_id}")
def get_task_status(task_id: str):
    job = task_queue.fetch_job(task_id)
    if job is None:
        return {"status": "unknown"}
    
    if job.is_finished:
        return {"status": "completed", "result": job.result}
    if job.is_failed:
        return {"status": "failed", "error": str(job.exc_info)}
        
    return {"status": "processing"}
EOF

# 4. Frontend Static Files (Minimalist HTML/JS for the UI)
cat <<EOF > frontend_static/index.html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OmniOCR Server Edition</title>
    <link rel="stylesheet" href="/static/css/style.css">
</head>
<body>
    <header><h1>🏛️ OmniOCR Server Edition</h1></header>
    <main>
        <div id="upload-zone">
            <h2>Upload Document</h2>
            <input type="file" id="file-input" accept="image/*">
            <button id="process-button">🚀 Process</button>
        </div>
        <div id="status-area"></div>
        <div id="results-area" style="display:none;">
            <h2>Extracted Text</h2>
            <textarea id="text-output" rows="15" readonly></textarea>
            <!-- Placeholder for Synchronized Correction View -->
        </div>
    </main>
    <script src="/static/js/app.js"></script>
</body>
</html>
EOF

cat <<EOF > frontend_static/css/style.css
/* Master Designer Minimalist CSS */
body { font-family: sans-serif; margin: 20px; background-color: #f8f8f8; }
header { border-bottom: 1px solid #ccc; padding-bottom: 10px; margin-bottom: 20px; }
#upload-zone { padding: 20px; border: 2px dashed #007AFF; text-align: center; }
#process-button { background-color: #007AFF; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }
/* ... (Responsive and Dark Mode CSS would be added here) ... */
EOF

cat <<EOF > frontend_static/js/app.js
// JavaScript for handling file upload and polling the API
document.getElementById('process-button').addEventListener('click', async () => {
    const fileInput = document.getElementById('file-input');
    const file = fileInput.files[0];
    const statusArea = document.getElementById('status-area');
    
    if (!file) {
        statusArea.innerHTML = '<p style="color: red;">Please select a file.</p>';
        return;
    }

    statusArea.innerHTML = '<p>Uploading and submitting task...</p>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        // 1. Submit Task
        const response = await fetch('/api/v1/submit_ocr', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        const taskId = data.task_id;

        if (data.status === 'queued') {
            statusArea.innerHTML = `<p>Task submitted (ID: ${taskId}). Waiting for processing...</p>`;
            
            // 2. Start Polling (Check status every 3 seconds)
            const interval = setInterval(async () => {
                const statusResponse = await fetch(\`/api/v1/task_status/\${taskId}\`);
                const statusData = await statusResponse.json();

                if (statusData.status === 'completed') {
                    clearInterval(interval);
                    statusArea.innerHTML = '<p style="color: green;">Processing Complete!</p>';
                    document.getElementById('text-output').value = statusData.result;
                    document.getElementById('results-area').style.display = 'block';
                } else if (statusData.status === 'failed') {
                    clearInterval(interval);
                    statusArea.innerHTML = \`<p style="color: red;">Task Failed: \${statusData.error}</p>\`;
                } else {
                    statusArea.innerHTML = \`<p>Status: \${statusData.status}...</p>\`;
                }
            }, 3000);
        }
    } catch (error) {
        statusArea.innerHTML = '<p style="color: red;">An API error occurred.</p>';
        console.error(error);
    }
});
EOF

echo "Ολοκληρώθηκε η δημιουργία της δομής φακέλων στο ./omniocr_server_edition"
echo "Για να τρέξει, χρειάζεται να εκκινήσετε: 1. Redis, 2. RQ Worker, 3. Uvicorn/FastAPI."