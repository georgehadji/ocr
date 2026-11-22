from __future__ import annotations

import dataclasses
from typing import Mapping

from omniocr.application.structure.breaks import GapRule, IndentRule, ShortLineRule
from omniocr.application.structure.geometry import (
    PageGeometry,
    column_left_edge,
    column_right_edge,
    is_probably_multi_column,
    median_leading,
    median_line_height,
)
from omniocr.application.structure.hyphenation import Dehyphenator, extract_join_candidates
from omniocr.application.structure.roles import classify as classify_role
from omniocr.application.structure.running_heads import detect as detect_running_heads
from omniocr.domain.errors import LayoutError
from omniocr.domain.models import (
    DocumentStructure,
    JoinSeparator,
    JoinVerdict,
    LineJoin,
    OCRLine,
    OCRParagraph,
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
                if (page.number, line.id) in running_heads:
                    marked_lines.append(
                        dataclasses.replace(line, region_type=RegionType.RUNNING_HEAD)
                    )
                else:
                    marked_lines.append(line)

            # Every rule below assumes one column; a two-column page would be
            # assembled into cross-column nonsense instead of just failing to
            # assemble, so it is left as unassembled lines (plan §9). Its heads
            # are still marked: they sit above and below the columns.
            if is_probably_multi_column(page.lines, page.width):
                new_pages.append(dataclasses.replace(page, lines=tuple(marked_lines)))
                continue

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
                        if join_decision.separator is JoinSeparator.DROPPED:
                            # dropped hyphen
                            first_stripped = text.rstrip()
                            text = first_stripped[:-1] + second.text.lstrip()

                            # If it was unverified, also emit a Suggestion
                            if join_decision.verdict is JoinVerdict.UNVERIFIED:
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
                                        # Spans two lines, so it cannot replace
                                        # either: a flag for the reviewer.
                                        reversible=False,
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
    """Reconstruct the original line texts from paragraph.text and paragraph.joins.

    ``assemble`` strips the whitespace at every boundary and drops a hyphen only
    for ``JoinSeparator.DROPPED``. Each line's contribution to ``paragraph.text``
    is therefore its text without the leading whitespace (unless it is the first
    line), without the trailing whitespace (unless it is the last) and without a
    dropped hyphen; a plain wrap also adds one joining space. The stripped
    whitespace and the dropped hyphen are put back from the line and its join.
    """
    lines = paragraph.lines
    joins = {j.first_line_id: j for j in paragraph.joins}
    para_text = paragraph.text
    last = len(lines) - 1

    reconstructed: list[str] = []
    text_ptr = 0
    for idx, line in enumerate(lines):
        if idx == last:
            # The last line gets the rest of paragraph.text.
            chunk = para_text[text_ptr:]
        else:
            join = joins.get(line.id)
            dropped = join is not None and join.separator is JoinSeparator.DROPPED
            kept = line.text if idx == 0 else line.text.lstrip()
            kept = kept.rstrip()
            kept = kept[:-1] if dropped else kept
            chunk = para_text[text_ptr : text_ptr + len(kept)]
            # A kept hyphen stays in the text and a dropped one is restored below;
            # only a plain wrap skips the space that joined the two lines.
            text_ptr += len(kept) + (0 if join is not None else 1)
            if join is not None and dropped:
                chunk += join.removed
        lead = line.text[: len(line.text) - len(line.text.lstrip())] if idx else ""
        trail = line.text[len(line.text.rstrip()) :] if idx != last else ""
        reconstructed.append(lead + chunk + trail)

    return tuple(reconstructed)
