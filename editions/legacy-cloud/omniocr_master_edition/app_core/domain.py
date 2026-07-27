# Data Models (OCRBlock, TenantContext, etc.)
from dataclasses import dataclass
from typing import List, Optional

@dataclass(frozen=True)
class OCRBlock:
    text: str; conf: float; x: int; y: int; w: int; h: int
# ... (OCRLine, OCRParagraph, DocumentStructure) ...
@dataclass(frozen=True)
class TenantContext:
    organization_id: str
    user_id: str
    subscription_tier: str
    custom_model_id: Optional[str] = None
    # ... (Security flags) ...
