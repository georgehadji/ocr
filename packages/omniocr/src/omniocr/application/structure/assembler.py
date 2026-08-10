from __future__ import annotations

from omniocr.domain.errors import LayoutError
from omniocr.domain.models import DocumentStructure, TenantContext
from omniocr.domain.result import Ok, Result


class IdentityAssembler:
    """Default document assembler that returns the document structure unchanged.

    Provides a clean, non-breaking default seam for structure recovery.
    """

    def assemble(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[DocumentStructure, LayoutError]:
        return Ok(document)
