from __future__ import annotations

import dataclasses
from typing import Mapping, Sequence

from omniocr.application.structure.breaks import GapRule, IndentRule, ShortLineRule
from omniocr.application.structure.geometry import (
    PageGeometry,
    column_left_edge,
    column_right_edge,
    median_leading,
    median_line_height,
)
from omniocr.application.structure.hyphenation import Dehyphenator, extract_join_candidates
from omniocr.application.structure.roles import classify as classify_role
from omniocr.application.structure.running_heads import detect as detect_running_heads
from omniocr.domain.errors import LayoutError
from omniocr.domain.models import (
    DocumentPage,
    DocumentStructure,
    LineJoin,
    OCRLine,
    OCRParagraph,
    ParagraphRole,
    RegionType,
    Script,
    Suggestion,
    TenantContext,
)
from omniocr.domain.result import Ok, Result
from omniocr.ports.interfaces import ILexicon


class IdentityAssembler:
    """Default document assembler that returns the document structure unchanged.

    Provides a clean, non-breaking default seam for structure recovery.
    """

    def assemble(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[DocumentStructure, LayoutError]:
        return Ok(document)


class DocumentAssembler:
    """Document assembler that reconstructs paragraphs, joins hyphens, and marks running heads."""

    def __init__(
        self,
        lexicons: Mapping[Script, ILexicon] | None = None,
        top_k: int = 2,
        bottom_k: int = 2,
        window: int = 2,
        threshold: float = 0.9,
    ) -> None:
        self.top_k = top_k
        self.bottom_k = bottom_k
        self.window = window
        self.threshold = threshold
        self.dehyphenator = Dehyphenator(lexicons or {})
        self.break_rules = (IndentRule(), ShortLineRule(), GapRule())

    def assemble(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[DocumentStructure, LayoutError]:
        # Step 1: Running head detection
        running_heads = detect_running_heads(
            document,
            top_k=self.top_k,
            bottom_k=self.bottom_k,
            window=self.window,
            threshold=self.threshold,
        )

        new_pages = []
        for page in document.pages:
            # Re-map lines with region types updated
            marked_lines = []
            for line in page.lines:
                if line.id in running_heads:
                    marked_lines.append(
                        dataclasses.replace(line, region_type=RegionType.RUNNING_HEAD)
                    )
                else:
                    marked_lines.append(line)

            if not marked_lines:
                new_pages.append(page)
                continue

            # Compute PageGeometry
            page_geom = PageGeometry(
                left=column_left_edge(marked_lines),
                right=column_right_edge(marked_lines),
                median_leading=median_leading(marked_lines),
                median_line_height=median_line_height(marked_lines),
                width=page.width,
                height=page.height,
            )

            # Step 2: Group lines into paragraphs
            grouped_paragraphs_lines: list[list[OCRLine]] = []
            current_group: list[OCRLine] = []

            for line in marked_lines:
                if line.region_type == RegionType.RUNNING_HEAD:
                    if current_group:
                        grouped_paragraphs_lines.append(current_group)
                        current_group = []
                    # Running head is its own single-line paragraph
                    grouped_paragraphs_lines.append([line])
                else:
                    if not current_group:
                        current_group.append(line)
                    else:
                        prev_line = current_group[-1]
                        # Check break rules
                        if any(
                            rule.breaks_before(prev_line, line, page_geom)
                            for rule in self.break_rules
                        ):
                            grouped_paragraphs_lines.append(current_group)
                            current_group = [line]
                        else:
                            current_group.append(line)
            if current_group:
                grouped_paragraphs_lines.append(current_group)

            # Step 3: Reconstruct text, dehyphenate, and classify role for each paragraph
            assembled_paragraphs: list[OCRParagraph] = []
            page_suggestions = list(page.suggestions)

            for i, p_lines in enumerate(grouped_paragraphs_lines):
                para_id = f"page-{page.number}-para-{i}"

                # Dehyphenate and build paragraph text
                text = p_lines[0].text
                joins: list[LineJoin] = []

                for idx in range(1, len(p_lines)):
                    first = p_lines[idx - 1]
                    second = p_lines[idx]

                    join_decision = self.dehyphenator.join(first, second)
                    if join_decision is not None:
                        joins.append(join_decision)
                        if join_decision.separator == "":
                            # dropped hyphen
                            first_stripped = text.rstrip()
                            text = first_stripped[:-1] + second.text.lstrip()

                            # If it was unverified, also emit a Suggestion
                            if join_decision.verdict == "unverified":
                                extracted = extract_join_candidates(first.text, second.text)
                                A, B, hyphen_char = (
                                    extracted if extracted is not None else ("", "", "")
                                )
                                page_suggestions.append(
                                    Suggestion(
                                        line_id=first.id,
                                        source_text=f"{A}{hyphen_char} {B}",
                                        suggestion_text=f"{A}{B}",
                                        reason="unverified_hyphen_join",
                                    )
                                )
                        else:
                            # kept hyphen
                            text = text.rstrip() + second.text.lstrip()
                    else:
                        # plain wrap
                        text = text.rstrip() + " " + second.text.lstrip()

                # Step 4: Role classification
                role = classify_role(p_lines, page_geom)

                assembled_paragraphs.append(
                    OCRParagraph(
                        id=para_id,
                        lines=tuple(p_lines),
                        text=text,
                        role=role,
                        joins=tuple(joins),
                    )
                )

            # Re-build DocumentPage with paragraphs and updated lines and suggestions
            new_page = dataclasses.replace(
                page,
                lines=tuple(marked_lines),
                paragraphs=tuple(assembled_paragraphs),
                suggestions=tuple(page_suggestions),
            )
            new_pages.append(new_page)

        return Ok(dataclasses.replace(document, pages=tuple(new_pages)))


def unjoin(paragraph: OCRParagraph) -> tuple[str, ...]:
    """Reconstruct the original line texts from paragraph.text and paragraph.joins."""
    if not paragraph.lines:
        return ()
    # If we have the original lines, we can exactly reconstruct the text of each line
    # by reversing the joins.
    # Let's map each join by first_line_id
    joins = {j.first_line_id: j for j in paragraph.joins}

    reconstructed: list[str] = []
    text_ptr = 0
    para_text = paragraph.text

    for idx, line in enumerate(paragraph.lines):
        if idx == len(paragraph.lines) - 1:
            # Last line gets the rest of paragraph.text
            reconstructed.append(para_text[text_ptr:])
            break

        # Check if there is a join at this boundary
        j = joins.get(line.id)
        if j is not None:
            # We need to find where the next line starts in para_text.
            # Next line's text starts after first line's text.
            # If j.separator == "" (hyphen dropped):
            # first ends with j.removed, and next starts with second.text.lstrip()
            second = paragraph.lines[idx + 1]
            second_clean = second.text.lstrip()

            # The joined text has: A + second_clean
            # Where line.text originally was: A + j.removed
            # So the length of A is: len(line.text) - 1
            A_len = len(line.text) - 1
            line_reconstructed = para_text[text_ptr : text_ptr + A_len] + j.removed
            reconstructed.append(line_reconstructed)
            text_ptr += A_len
        else:
            # Plain wrap (joined by space)
            # Find the space that separates line.text and next line
            # It should be exactly at len(line.text) if we lstrip/rstrip carefully,
            # but let's just take len(line.text)
            line_reconstructed = para_text[text_ptr : text_ptr + len(line.text)]
            reconstructed.append(line_reconstructed)
            text_ptr += len(line.text) + 1  # skip the joining space

    return tuple(reconstructed)
