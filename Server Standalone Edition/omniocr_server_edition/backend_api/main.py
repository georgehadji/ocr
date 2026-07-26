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
