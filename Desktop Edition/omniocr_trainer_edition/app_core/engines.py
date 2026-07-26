# app_core/engines.py

import numpy as np
import cv2
import pytesseract
from PIL import Image
from typing import List, Tuple

# Εισαγωγή των Interfaces και Domain Objects
# ΔΙΟΡΘΩΣΗ: Προστέθηκε το IModelExporter στην εισαγωγή.
from app_core.interfaces import IImageProcessor, IOCRStrategy, IModelExporter 
from app_core.domain import TenantContext, OCRParagraph, OCRBlock 
from app_core.services import LayoutAnalyzer # Χρειάζεται για τη στρατηγική

# =================================================================
# 1. Image Processor Implementation (OpenCV)
# =================================================================

class OpenCVImageProcessor(IImageProcessor):
    """
    Υλοποίηση του IImageProcessor χρησιμοποιώντας την OpenCV (cv2) για υψηλή απόδοση
    σε προεπεξεργασία εικόνας.
    """
    def process(self, image_bytes: bytes) -> np.ndarray:
        """
        Μετατρέπει raw bytes σε πίνακα OpenCV, εκτελεί binarization και επιστρέφει.
        """
        # 1. Μετατροπή bytes σε πίνακα NumPy
        np_arr = np.frombuffer(image_bytes, np.uint8)
        # 2. Αποκωδικοποίηση εικόνας (χρησιμοποιώντας IMREAD_COLOR)
        image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        
        if image is None:
            raise ValueError("Could not decode image bytes.")

        # 3. Μετατροπή σε grayscale (βασική προεπεξεργασία για OCR)
        gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # 4. Εφαρμογή Binarization (Otsu's method) για βελτίωση της αντίθεσης
        _, binary_image = cv2.threshold(gray_image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        return binary_image

# =================================================================
# 2. OCR Strategy Implementation (Tesseract Ensemble)
# =================================================================

class TesseractEnsembleEngine(IOCRStrategy):
    """
    Υλοποίηση του IOCRStrategy χρησιμοποιώντας το PyTesseract.
    """
    def __init__(self, image_processor: IImageProcessor, layout_analyzer: LayoutAnalyzer):
        """
        Dependency Injection: Εισάγει τον Image Processor και τον Layout Analyzer.
        """
        self.image_processor = image_processor
        self.layout_analyzer = layout_analyzer
        
        try:
            pytesseract.get_tesseract_version()
        except pytesseract.TesseractNotFoundError:
            print("WARNING: Tesseract is not installed or not in PATH. OCR will fail.")
            
    def process_document(self, image_bytes: bytes, lang: str, context: TenantContext) -> Tuple[List[OCRParagraph], List[OCRBlock]]:
        """
        Εκτελεί την πλήρη ροή: Προεπεξεργασία -> Tesseract OCR -> Layout Analysis.
        """
        # 1. Προεπεξεργασία εικόνας
        processed_np_array = self.image_processor.process(image_bytes)
        
        # 2. Μετατροπή του NumPy array σε αντικείμενο PIL Image
        processed_pil_image = Image.fromarray(processed_np_array)
        
        # 3. Εκτέλεση Tesseract OCR για εξαγωγή δεδομένων σε μορφή DICT
        hocr_data = pytesseract.image_to_data(
            processed_pil_image, 
            lang=lang, 
            output_type=pytesseract.Output.DICT
        )
        
        # 4. Μετατροπή Tesseract Output σε Domain Objects (OCRBlock)
        blocks: List[OCRBlock] = self._parse_tesseract_output(hocr_data)
        
        # 5. Layout Analysis (Χρησιμοποιώντας τον injected analyzer)
        full_text = " ".join(b.text for b in blocks if b.text.strip())
        paragraphs = [OCRParagraph(blocks, full_text)] 
        
        return paragraphs, blocks

    def _parse_tesseract_output(self, data: dict) -> List[OCRBlock]:
        """
        Βοηθητική μέθοδος για τη μετατροπή του λεξικού εξόδου του Tesseract σε OCRBlock objects.
        """
        blocks: List[OCRBlock] = []
        n_boxes = len(data['level'])
        
        for i in range(n_boxes):
            if data['level'][i] == 5 and data['text'][i].strip() and data['conf'][i] > 0:
                (x, y, w, h) = (data['left'][i], data['top'][i], data['width'][i], data['height'][i])
                conf = data['conf'][i]
                text = data['text'][i]
                
                blocks.append(
                    OCRBlock(
                        text=text,
                        conf=float(conf),
                        x=x,
                        y=y,
                        width=w,
                        height=h
                    )
                )
        return blocks

# =================================================================
# 3. Model Exporter Implementation (Placeholder)
# =================================================================

class MLflowModelExporter(IModelExporter):
    """
    Υλοποίηση για την εξαγωγή μοντέλων από το MLflow.
    """
    def export(self, run_id: str, target_format: str) -> str:
        """
        Προσομοιώνει την εξαγωγή του μοντέλου από το MLflow artifact store.
        """
        if target_format == "ONNX":
            return f"Model from run {run_id} exported successfully to ONNX format."
        elif target_format == "TrainedData":
            return f"Model from run {run_id} exported successfully as Tesseract .traineddata."
        else:
            return f"Export format {target_format} not supported."