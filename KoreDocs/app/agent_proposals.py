from __future__ import annotations

from typing import Annotated

from .documents.korefile import service as korefile


def koredocs_proposal_create(
    file_id: Annotated[int, 'KoreDocs file id for a .koredoc or .koresheet.'],
    expected_revision: Annotated[int, 'Revision from the latest KoreDocs file read.'],
    proposed_content: Annotated[str, 'Complete proposed file content. For .koredoc preserve the existing metadata header; for .koresheet provide the complete JSON document.'],
    summary: Annotated[str, 'Short human-readable summary shown in the review panel.'],
) -> dict:
    """Create a pending Agent proposal for explicit user review in KoreDocs."""
    return korefile.create_proposal(
        file_id=file_id,
        expected_revision=expected_revision,
        proposed_content=proposed_content,
        summary=summary,
    )
