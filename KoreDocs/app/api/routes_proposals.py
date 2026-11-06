from __future__ import annotations

from difflib import unified_diff

from fastapi import HTTPException, Query

from ..documents.korefile import service as korefile


def _not_found_or_unprocessable(error: ValueError) -> HTTPException:
    status_code = 404 if str(error).startswith('Proposal not found:') else 422
    return HTTPException(status_code=status_code, detail=str(error))


def register_proposal_routes(app) -> None:
    @app.get('/api/proposals')
    def proposals_list(file_id: int = Query(...)) -> list[dict]:
        try:
            return korefile.list_proposals(file_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get('/api/proposals/{proposal_id}')
    def proposal_get(proposal_id: str) -> dict:
        try:
            proposal = korefile.get_proposal(proposal_id)
        except ValueError as exc:
            raise _not_found_or_unprocessable(exc) from exc
        proposal['diff'] = '\n'.join(unified_diff(
            str(proposal['base_content']).splitlines(),
            str(proposal['proposed_content']).splitlines(),
            fromfile='current',
            tofile='proposal',
            lineterm='',
        ))
        return proposal

    @app.post('/api/proposals/{proposal_id}/accept')
    def proposal_accept(proposal_id: str) -> dict:
        try:
            return korefile.apply_proposal(proposal_id)
        except korefile.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise _not_found_or_unprocessable(exc) from exc

    @app.post('/api/proposals/{proposal_id}/reject')
    def proposal_reject(proposal_id: str) -> dict:
        try:
            return korefile.reject_proposal(proposal_id)
        except ValueError as exc:
            raise _not_found_or_unprocessable(exc) from exc
