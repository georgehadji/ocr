# OCRService, LayoutAnalyzer, PostProcessor (Business Logic)
import asyncio
from app_core.interfaces import IOCRStrategy
from app_core.domain import TenantContext

class OCRService:
    def __init__(self, strategy: IOCRStrategy):
        self._strategy = strategy
        # ... (Dependency Injection setup) ...

    async def execute_ocr(self, image_bytes: bytes, lang: str, context: TenantContext) -> List['OCRParagraph']:
        # Security/Audit Logging/Circuit Breaker checks here
        return await self._strategy.process_document(image_bytes, lang, context)

class LayoutAnalyzer:
    # ... (Functional grouping logic) ...
    pass
