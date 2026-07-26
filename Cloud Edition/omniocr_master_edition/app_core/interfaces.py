# Abstract Base Classes (ABCs)
from abc import ABC, abstractmethod
from typing import List
from app_core.domain import OCRBlock, TenantContext

class IImageProcessor(ABC):
    @abstractmethod
    def process(self, image_bytes: bytes) -> 'np.ndarray': pass

class IOCREngine(ABC):
    @abstractmethod
    async def extract(self, img: 'np.ndarray', context: TenantContext) -> List[OCRBlock]: pass

class IOCRStrategy(ABC):
    @abstractmethod
    async def process_document(self, image_bytes: bytes, lang: str, context: TenantContext) -> List['OCRParagraph']: pass
# ... (ITableExtractor, IAuditLogger) ...
