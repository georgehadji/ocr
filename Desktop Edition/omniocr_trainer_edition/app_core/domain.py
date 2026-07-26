# app_core/domain.py

from dataclasses import dataclass, field
from typing import List, Optional

# =================================================================
# 1. Domain Objects (Data Structures)
# Αυτά τα αντικείμενα ορίζουν τη γλώσσα και τη δομή δεδομένων του συστήματος.
# Χρησιμοποιούμε dataclasses για καθαρότητα και αυτόματη δημιουργία __init__, __repr__, κλπ.
# =================================================================

@dataclass(frozen=True)
class TenantContext:
    """
    Αντικείμενο που περιέχει το πλαίσιο (context) του χρήστη/οργανισμού.
    Είναι κρίσιμο για την ασφάλεια, την παρακολούθηση και την εξατομίκευση ρυθμίσεων.
    """
    organization_id: str
    user_id: str
    subscription_tier: str
    custom_model_id: Optional[str] = field(default=None) # Για custom training

@dataclass(frozen=True)
class OCRBlock:
    """
    Αντικείμενο που αναπαριστά μια μεμονωμένη λέξη ή σύμβολο που αναγνωρίστηκε από το OCR engine.
    Αυτά τα blocks χρησιμοποιούνται για το Confidence Heatmap και τη διόρθωση.
    """
    text: str
    conf: float  # Confidence score (0.0 - 100.0)
    x: int       # Bounding box: top-left x coordinate
    y: int       # Bounding box: top-left y coordinate
    width: int
    height: int
    
    # Προσθήκη υποδομής για μελλοντικά features (π.χ., αναγνώριση γραμματοσειράς)
    font_family: Optional[str] = field(default=None)

@dataclass(frozen=True)
class OCRParagraph:
    """
    Αντικείμενο που αναπαριστά μια δομημένη παράγραφο κειμένου.
    Είναι το αποτέλεσμα του Layout Analyzer.
    """
    blocks: List[OCRBlock] # Η λίστα των OCRBlocks που συνθέτουν την παράγραφο
    text: str              # Το πλήρες, ανακατασκευασμένο κείμενο της παραγράφου
    
    # Bounding box της συνολικής παραγράφου (υπολογίζεται από τα blocks)
    x: int = field(init=False)
    y: int = field(init=False)
    width: int = field(init=False)
    height: int = field(init=False)

    def __post_init__(self):
        """
        Υπολογίζει αυτόματα το συνολικό bounding box της παραγράφου.
        """
        if not self.blocks:
            # Αν δεν υπάρχουν blocks, ορίζουμε μηδενικές διαστάσεις
            object.__setattr__(self, 'x', 0)
            object.__setattr__(self, 'y', 0)
            object.__setattr__(self, 'width', 0)
            object.__setattr__(self, 'height', 0)
            return

        # Βρίσκουμε τις ελάχιστες/μέγιστες συντεταγμένες
        min_x = min(b.x for b in self.blocks)
        min_y = min(b.y for b in self.blocks)
        max_x = max(b.x + b.width for b in self.blocks)
        max_y = max(b.y + b.height for b in self.blocks)

        # Ορίζουμε τα πεδία (χρησιμοποιούμε object.__setattr__ επειδή η κλάση είναι frozen=True)
        object.__setattr__(self, 'x', min_x)
        object.__setattr__(self, 'y', min_y)
        object.__setattr__(self, 'width', max_x - min_x)
        object.__setattr__(self, 'height', max_y - min_y)

# Προσθήκη υποδομής για μελλοντικά features (Επεκτασιμότητα)
@dataclass(frozen=True)
class TrainingJob:
    """
    Αντικείμενο που αναπαριστά μια εκπαιδευτική εργασία (job) στο MLOps.
    """
    job_id: str
    model_name: str
    data_path: str
    status: str # e.g., 'PENDING', 'RUNNING', 'COMPLETED', 'FAILED'
    start_time: float
    end_time: Optional[float] = field(default=None)
    mlflow_run_id: Optional[str] = field(default=None)