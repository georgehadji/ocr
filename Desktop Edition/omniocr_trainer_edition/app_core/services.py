# app_core/services.py

from app_core.interfaces import IOCRStrategy # Υποθέτουμε ότι το interface είναι εκεί

class OCRService:
    """
    Core service for executing OCR, utilizing a specific IOCRStrategy.
    This adheres to the Dependency Inversion Principle (DIP).
    """
    def __init__(self, strategy: IOCRStrategy):
        self.strategy = strategy

    # Add methods here (e.g., process_document)

class LayoutAnalyzer:
    """
    Service responsible for structuring raw OCR blocks into paragraphs, tables, etc.
    """
    def analyze(self, blocks):
        # Placeholder for complex layout logic
        pass