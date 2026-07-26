# app_core/interfaces.py

from abc import ABC, abstractmethod
from typing import List, Tuple
import numpy as np

# Υποθέτουμε ότι τα Domain Objects έχουν οριστεί στο app_core/domain.py
# Αυτό είναι κρίσιμο για το type hinting και την καθαρή αρχιτεκτονική.
from app_core.domain import TenantContext, OCRParagraph, OCRBlock 

class IImageProcessor(ABC):
    """
    Interface για όλες τις στρατηγικές προεπεξεργασίας εικόνας (π.χ., OpenCV, Pillow).
    Ορίζει τη σύμβαση για τη μετατροπή raw bytes σε επεξεργασμένο πίνακα εικόνας.
    """
    @abstractmethod
    def process(self, image_bytes: bytes) -> np.ndarray:
        """
        Εκτελεί προεπεξεργασία (π.χ., deskew, binarization) σε raw bytes εικόνας.
        
        Args:
            image_bytes: Τα raw bytes της εικόνας.
            
        Returns:
            np.ndarray: Ο επεξεργασμένος πίνακας εικόνας (π.χ., grayscale).
        """
        raise NotImplementedError

class IOCRStrategy(ABC):
    """
    Interface για όλες τις στρατηγικές εκτέλεσης OCR (π.χ., Tesseract, Kraken, Cloud API).
    Αυτό επιτρέπει την εύκολη εναλλαγή OCR engines (Strategy Pattern).
    """
    @abstractmethod
    def process_document(self, image_bytes: bytes, lang: str, context: TenantContext) -> Tuple[List[OCRParagraph], List[OCRBlock]]:
        """
        Εκτελεί την πλήρη διαδικασία OCR σε ένα έγγραφο.
        
        Args:
            image_bytes: Τα raw bytes της εικόνας.
            lang: Η γλώσσα/οι γλώσσες OCR (π.χ., 'grc+ell+eng').
            context: Το TenantContext για ρυθμίσεις (π.χ., custom μοντέλα).
            
        Returns:
            Tuple[List[OCRParagraph], List[OCRBlock]]: 
                - Μια λίστα με τις δομημένες παραγράφους.
                - Μια λίστα με τα raw blocks (απαραίτητα για το Confidence Heatmap).
        """
        raise NotImplementedError

# Προσθήκη υποδομής για μελλοντικά features (Επεκτασιμότητα)
class IModelExporter(ABC):
    """
    Interface για την εξαγωγή εκπαιδευμένων μοντέλων σε διάφορες μορφές/πλατφόρμες.
    """
    @abstractmethod
    def export(self, run_id: str, target_format: str) -> str:
        """Εξάγει το μοντέλο που αντιστοιχεί στο MLflow run_id."""
        raise NotImplementedError