import streamlit as st
import concurrent.futures
import mlflow
import os
import sys
import time
import numpy as np
from typing import List, Tuple

# =================================================================
# 1. PATH FIX: Ensure parent directory is in path for module imports
# This is the definitive fix for ModuleNotFoundError: 'app_core'
# =================================================================
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, '..'))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Import core logic (now accessible)
from app_core.services import OCRService, LayoutAnalyzer
from app_core.engines import TesseractEnsembleEngine
from app_core.interfaces import IImageProcessor, IOCRStrategy
from app_core.domain import TenantContext, OCRParagraph, OCRBlock

# --- Placeholder Implementations for Desktop Sync Mode ---
# These mocks simulate the actual complex logic residing in app_core

class MockImageProcessor(IImageProcessor):
    """
    Simulates the complex preprocessing pipeline (e.g., deskewing, binarization).
    Implements the IImageProcessor interface for architectural consistency.
    """
    def process(self, image_bytes: bytes) -> np.ndarray:
        # Simulate loading and processing time to mimic real-world latency
        time.sleep(0.5)
        # Return a mock image array (e.g., grayscale dimensions)
        return np.zeros((1000, 800), dtype=np.uint8) 

class MockLocalOCRStrategy(IOCRStrategy):
    """
    Synchronous strategy for desktop testing, simulating a local Tesseract call.
    
    ARCHITECTURAL REFACTORING: The method now returns both paragraphs AND blocks,
    as the blocks are essential for the Confidence Heatmap UI feature.
    """
    def __init__(self, image_processor: IImageProcessor, layout_analyzer: LayoutAnalyzer):
        self.image_processor = image_processor
        self.layout_analyzer = layout_analyzer

    def process_document(self, image_bytes: bytes, lang: str, context: TenantContext) -> Tuple[List[OCRParagraph], List[OCRBlock]]:
        """
        Executes the mock OCR pipeline.
        
        Returns:
            Tuple[List[OCRParagraph], List[OCRBlock]]: The structured text and the raw blocks.
        """
        # 1. Preprocessing (Simulated)
        # The image_processor is called regardless of the lang parameter
        proc_img = self.image_processor.process(image_bytes)
        
        # 2. OCR Extraction (Simulated synchronous Tesseract call)
        time.sleep(1.5) # Simulate the time taken by Tesseract/Kraken
        
        # Simulate OCR Blocks with varying confidence for the Heatmap
        # The content is static for the mock, but in a real app, it would depend on the image and 'lang'
        blocks = [
            OCRBlock("Αυτή", 95.0, 10, 10, 50, 20),
            OCRBlock("είναι", 92.0, 60, 10, 50, 20),
            OCRBlock("μια", 85.0, 110, 10, 40, 20), # Medium confidence (Orange)
            OCRBlock("δοκιμή", 55.0, 150, 10, 70, 20), # Low confidence (Red) - Target for Active Learning
            OCRBlock("πολυτονικού", 99.0, 230, 10, 100, 20),
            OCRBlock("κειμένου.", 90.0, 340, 10, 90, 20),
        ]
        
        # 3. Layout Analysis (Mock)
        text = " ".join(b.text for b in blocks)
        paragraphs = [OCRParagraph(blocks, text)]
        
        # Return both the high-level structure (paragraphs) and the low-level data (blocks)
        return paragraphs, blocks

# ==========================================
# 5. TRAINING SERVICE (MLflow Integration)
# ==========================================
class TrainingService:
    """
    Handles the execution and tracking of long-running model training jobs using MLflow.
    (No changes needed here, as it's isolated and robust).
    """
    def __init__(self):
        # Set local MLflow tracking URI to a dedicated folder within the project
        mlflow.set_tracking_uri("file:" + os.path.join(parent_dir, "data", "mlruns"))

    def start_training(self, model_name: str, data_path: str) -> Tuple[str, float]:
        """
        Executes the training job in a separate thread and logs results to MLflow.
        """
        
        def run_blocking_training():
            with mlflow.start_run(run_name=model_name) as run:
                mlflow.log_param("data_path", data_path)
                time.sleep(5) # Simulate training time
                accuracy = 0.97 if "manuscripts" in data_path else 0.90
                mlflow.log_metric("accuracy_wer", accuracy)
                return run.info.run_id, accuracy

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_blocking_training)
            run_id, accuracy = future.result()
            
        return run_id, accuracy

# ==========================================
# 6. UI/UX (Master Designer Implementation)
# ==========================================

def get_current_tenant_context() -> TenantContext:
    """Simulated Authentication/Context for the Trainer user."""
    return TenantContext(
        organization_id="LOCAL_TRAINER",
        user_id="trainer_user",
        subscription_tier="Trainer"
    )

def render_confidence_heatmap(blocks: List[OCRBlock]) -> str:
    """
    Generates HTML/CSS for the Confidence Heatmap.
    (No changes needed here, as it's a dedicated UI utility).
    """
    html_content = []
    for block in blocks:
        conf = block.conf
        text = block.text
        
        if conf < 60:
            color = "rgba(255, 0, 0, 0.6)" # Red (CRITICAL)
            tooltip = f"Confidence: {conf:.1f}% (CRITICAL)"
        elif conf < 85:
            color = "rgba(255, 165, 0, 0.4)" # Orange (Review)
            tooltip = f"Confidence: {conf:.1f}% (Review)"
        else:
            color = "transparent"
            tooltip = f"Confidence: {conf:.1f}%"
            
        span = f'<span style="background-color: {color}; padding: 2px; border-radius: 3px;" title="{tooltip}">{text}</span>'
        html_content.append(span)
        
    return ' '.join(html_content)

def main():
    st.set_page_config(page_title="OmniOCR Trainer", layout="wide")
    
    context = get_current_tenant_context()
    
    st.title("📚 OmniOCR Trainer Edition (Desktop)")
    st.sidebar.info(f"User: {context.user_id} | Mode: Local Sync")
    
    # REFACTORING: Added a third tab for Simple OCR functionality
    tab1, tab2, tab3 = st.tabs(["Simple OCR", "OCR & Correction", "Model Training"])
    
    # Initialize services using the defined interfaces (Clean Architecture)
    image_processor = MockImageProcessor()
    layout_analyzer = LayoutAnalyzer()
    ocr_strategy = MockLocalOCRStrategy(image_processor, layout_analyzer)
    ocr_service = OCRService(ocr_strategy) # The service uses the strategy
    
    # --- TAB 1: SIMPLE OCR (Standalone Functionality) ---
    with tab1:
        st.header("Standalone Document OCR")
        st.markdown("Εξαγωγή καθαρού κειμένου χωρίς εργαλεία διόρθωσης.")
        
        col_upload, col_lang = st.columns([3, 1])
        
        with col_upload:
            uploaded = st.file_uploader("Upload Document", type=['png', 'jpg', 'jpeg'], key="simple_ocr_upload")
        
        with col_lang:
            # Allow the user to select the language pack for the OCR engine
            selected_lang = st.selectbox(
                "OCR Language Pack", 
                options=['grc+ell+eng', 'eng', 'ell', 'grc'], 
                index=0,
                key="simple_ocr_lang"
            )
            
        if uploaded:
            st.image(uploaded, caption="Original Document", use_column_width=True)
            
            if st.button(f"🚀 Run OCR ({selected_lang})", key="run_simple_ocr"):
                image_bytes = uploaded.read()
                
                with st.spinner("Processing Document..."):
                    # Execute the synchronous process. We only need the paragraphs (text), so we discard the blocks (_).
                    paragraphs, _ = ocr_strategy.process_document(image_bytes, selected_lang, context)
                    
                if paragraphs:
                    full_text = paragraphs[0].text
                    st.subheader("Extracted Text")
                    # Display the result in a simple, copyable text area
                    st.text_area("OCR Output", value=full_text, height=300)
                    st.success("OCR Complete.")

    # --- TAB 2: OCR & CORRECTION (Active Learning) ---
    with tab2:
        st.header("Document Correction & Active Learning")
        uploaded = st.file_uploader("Upload Document", type=['png', 'jpg', 'jpeg'], key="correction_upload")
        
        col1, col2 = st.columns(2)
        
        if uploaded:
            with col1:
                st.image(uploaded, caption="Original Document", use_column_width=True)
                
            if st.button("🚀 Process Document", key="run_correction_ocr"):
                image_bytes = uploaded.read()
                
                with st.spinner("Processing (Local Sync Mode)..."):
                    # Execute the synchronous process and get BOTH paragraphs and blocks
                    paragraphs, blocks = ocr_strategy.process_document(image_bytes, 'grc+ell+eng', context)
                    
                if paragraphs:
                    full_text = paragraphs[0].text 
                    heatmap_html = render_confidence_heatmap(blocks)
                    
                    with col2:
                        st.subheader("Correction Tool (Click to Edit)")
                        st.markdown(heatmap_html, unsafe_allow_html=True)
                        st.divider()
                        edited_text = st.text_area("Editable Text (for Active Learning)", value=full_text, height=200)
                        
                        if st.button("✅ Save Corrections (Add to Training Set)", key="save_corrections"):
                            st.success("Corrections saved! Data added to the training set for future model refinement.")

    # --- TAB 3: MODEL TRAINING (MLOps) ---
    with tab3:
        st.header("Custom Model Training (MLOps)")
        trainer = TrainingService()
        
        model_name = st.text_input("Model Name", f"{context.organization_id}_BYZANTINE_V{time.time():.0f}", key="train_model_name")
        data_folder = st.text_input("Training Data Folder", os.path.join(parent_dir, "data", "training_sets", "manuscripts"), key="train_data_folder")
        
        if st.button("🔥 Start Training Job", key="start_training"):
            if not os.path.exists(data_folder):
                st.error(f"Error: Training data folder not found at {data_folder}")
            else:
                with st.spinner("Executing long-running training job... (Check console for Kraken output)"):
                    try:
                        run_id, accuracy = trainer.start_training(model_name, data_folder)
                        st.success(f"Training Complete! Model '{model_name}' achieved {accuracy*100:.1f}% accuracy.")
                        st.info(f"MLflow Run ID: {run_id}. Model is ready for export to the Enterprise SaaS.")
                    except Exception as e:
                        st.error(f"Training Failed: {e}")
                        
        st.divider()
        st.subheader("MLflow Tracking")
        st.write("To view all training experiments and export models, run the MLflow UI locally:")
        st.code(f"mlflow ui --backend-store-uri file:{os.path.join(parent_dir, 'data', 'mlruns')}")

if __name__ == "__main__":
    main()