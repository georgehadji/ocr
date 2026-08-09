import cv2
import numpy as np
import pytesseract
import logging
import unicodedata
import os
import io
import fitz  # PyMuPDF
import torch
import streamlit as st
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from docx import Document
from docx.shared import Pt
from reportlab.pdfgen import canvas
from PIL import Image

# ==========================================
# 1. HARDWARE & ACCELERATION MANAGER
# ==========================================
class HardwareManager:
    def __init__(self):
        self.device = self._detect()

    def _detect(self):
        if torch.cuda.is_available(): return "CUDA (NVIDIA GPU)"
        if cv2.ocl.haveOpenCL(): 
            cv2.ocl.setUseOpenCL(True)
            return "OpenCL (Integrated GPU)"
        return "CPU"

# ==========================================
# 2. IMAGE PROCESSING (GPU ACCELERATED)
# ==========================================
class ImageProcessor:
    @staticmethod
    def process(image_bytes):
        # Load image
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        
        # Use Transparent API (UMat) for automatic GPU offload
        umat = cv2.UMat(img)
        umat = cv2.resize(umat, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        umat = cv2.fastNlMeansDenoising(umat, h=10)
        umat = cv2.adaptiveThreshold(umat, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        
        return umat.get() # Return to CPU for Tesseract

# ==========================================
# 2b. PDF RASTERIZATION
# ==========================================
class PdfRasterizer:
    """Render one PDF page to PNG bytes.

    The uploader has always advertised 'pdf', but nothing here decoded one:
    raw PDF bytes went straight to PIL/cv2, which cannot read them. One page
    at a time keeps memory flat on the 300MB+ scans this is pointed at.
    """

    DPI: int = 300

    @staticmethod
    def page_count(pdf_bytes: bytes) -> int:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return doc.page_count

    @staticmethod
    def render(pdf_bytes: bytes, page_number: int) -> bytes:
        """Render 1-indexed ``page_number`` at DPI as PNG bytes."""
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc.load_page(page_number - 1)
            matrix = fitz.Matrix(PdfRasterizer.DPI / 72, PdfRasterizer.DPI / 72)
            return page.get_pixmap(matrix=matrix).tobytes("png")


# ==========================================
# 3. ENSEMBLE OCR ENGINE (VOTING LOGIC)
# ==========================================
@dataclass
class OCRBlock:
    text: str; conf: float; x: int; y: int; w: int; h: int

class EnsembleEngine:
    def __init__(self, lang='grc+ell+eng'):
        self.lang = lang

    def get_best_ocr(self, img):
        # Run two passes with different segmentation modes
        def ocr_pass(psm):
            config = f'--oem 3 --psm {psm}'
            d = pytesseract.image_to_data(img, lang=self.lang, config=config, output_type=pytesseract.Output.DICT)
            return [OCRBlock(d['text'][i], float(d['conf'][i]), d['left'][i], d['top'][i], d['width'][i], d['height'][i]) 
                    for i in range(len(d['text'])) if int(d['conf'][i]) > 0]

        with ThreadPoolExecutor() as ex:
            p1 = ex.submit(ocr_pass, 1)
            p2 = ex.submit(ocr_pass, 3)
            res1, res2 = p1.result(), p2.result()
        
        conf1 = sum([b.conf for b in res1])/len(res1) if res1 else 0
        conf2 = sum([b.conf for b in res2])/len(res2) if res2 else 0
        return res1 if conf1 >= conf2 else res2

# ==========================================
# 4. EXPORT MANAGER (DOCX, PDF, TXT)
# ==========================================
class ExportManager:
    @staticmethod
    def to_docx(blocks):
        doc = Document()
        doc.styles['Normal'].font.name = 'Palatino Linotype'
        # Simple layout reconstruction
        text = " ".join([b.text for b in blocks])
        doc.add_paragraph(unicodedata.normalize('NFC', text))
        bio = io.BytesIO()
        doc.save(bio)
        return bio.getvalue()

    @staticmethod
    def to_pdf(img, blocks):
        h, w = img.shape[:2]
        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=(w, h))
        for b in blocks:
            t = c.beginText(b.x, h - b.y - b.h)
            t.setTextRenderMode(3) # Invisible
            t.setFont("Helvetica", b.h * 0.7)
            t.textOut(b.text)
            c.drawText(t)
        c.showPage(); c.save()
        return packet.getvalue()

# ==========================================
# 5. PERFECT UX/UI (STREAMLIT)
# ==========================================
def main():
    st.set_page_config(page_title="OmniOCR Enterprise", layout="wide")
    st.title("🏛️ OmniOCR Polytonic Suite")
    
    hw = HardwareManager()
    st.sidebar.success(f"Hardware: {hw.device}")
    
    uploaded = st.file_uploader("Upload Document", type=['png', 'jpg', 'pdf'])
    
    if uploaded:
        raw = uploaded.getvalue()
        is_pdf = uploaded.name.lower().endswith(".pdf")

        if is_pdf:
            total = PdfRasterizer.page_count(raw)
            page_number = st.sidebar.number_input(
                "Page", min_value=1, max_value=total, value=1, step=1
            )
            st.sidebar.caption(f"{total} pages in this PDF")
            # One page per run: this prototype is a single-page viewer. Use
            # `omniocr run` for whole books.
            image_bytes = PdfRasterizer.render(raw, int(page_number))
            caption = f"Original — page {page_number} of {total}"
        else:
            image_bytes = raw
            caption = "Original"

        col1, col2 = st.columns(2)
        with col1:
            st.image(image_bytes, caption=caption)

        if st.button("🚀 Process with GPU Acceleration"):
            with st.spinner("Analyzing Layout & Extracting Text..."):
                # 1. Process Image
                proc_img = ImageProcessor.process(image_bytes)
                # 2. OCR
                engine = EnsembleEngine()
                blocks = engine.get_best_ocr(proc_img)
                # 3. UI Display
                full_text = " ".join([b.text for b in blocks])
                clean_text = unicodedata.normalize('NFC', full_text)
                
                with col2:
                    st.subheader("Result")
                    edited = st.text_area("Edit Text", value=clean_text, height=400)
                    
                    st.divider()
                    # 4. Exports
                    st.download_button("Download DOCX", ExportManager.to_docx(blocks), "result.docx")
                    st.download_button("Download Searchable PDF", ExportManager.to_pdf(proc_img, blocks), "result.pdf")

if __name__ == "__main__":
    main()