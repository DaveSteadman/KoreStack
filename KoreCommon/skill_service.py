"""Small native HTTP adapter for manifest-registered KoreStack skills."""

import inspect
from typing import Any
from typing import Callable

from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field


class SkillInvocation(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def register_skill_invocation_routes(app, handlers: dict[str, Callable[..., Any]]) -> None:
    """Expose named service functions through the shared SkillManager invocation contract."""
    @app.post("/api/skills/{skill_name}/invoke")
    async def invoke_skill(skill_name: str, body: SkillInvocation) -> dict[str, Any]:
        handler = handlers.get(skill_name)
        if handler is None:
            raise HTTPException(status_code=404, detail=f"Unknown skill '{skill_name}'")
        try:
            result = handler(**body.arguments)
            if inspect.isawaitable(result):
                result = await result
        except HTTPException:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "result": result}
