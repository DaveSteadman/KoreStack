from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel


class SkillRegistration(BaseModel):
    service: str
    service_label: str = ""
    skills: list[dict[str, Any]]


def register_skill_manager_routes(app, *, manager) -> None:
    @app.get("/api/skill-manager/skills")
    def skill_manager_skills_get() -> dict[str, Any]:
        skills = manager.list_skills()
        return {"skills": skills, "count": len(skills)}

    @app.get("/api/skill-manager/keywords")
    def skill_manager_keywords_get() -> dict[str, list[str]]:
        return manager.keyword_map()

    @app.post("/api/skill-manager/register")
    def skill_manager_register_post(body: SkillRegistration) -> dict[str, Any]:
        try:
            return manager.register(body.service, body.skills, service_label=body.service_label)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/skill-manager/services/{service}")
    def skill_manager_unregister_delete(service: str) -> dict[str, Any]:
        return {"service": service, "removed": manager.unregister(service)}
