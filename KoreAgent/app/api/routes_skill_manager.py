from typing import Any

from fastapi import Body
from fastapi import HTTPException
from pydantic import BaseModel


class SkillRegistration(BaseModel):
    service: str
    service_label: str = ""
    skills: list[dict[str, Any]]


class ToolRegistration(BaseModel):
    tool: dict[str, Any]


class SkillItemRegistration(BaseModel):
    service: str
    service_label: str = ""
    skill: dict[str, Any]


def register_skill_manager_routes(app, *, manager) -> None:
    @app.get("/api/skill-manager/skills")
    def skill_manager_skills_get() -> dict[str, Any]:
        skills = manager.list_skills()
        return {"skills": skills, "count": len(skills)}

    @app.get("/api/skill-manager/tools")
    def skill_manager_tools_get() -> dict[str, Any]:
        tools = manager.list_tools()
        return {"tools": tools, "count": len(tools)}

    @app.post("/api/skill-manager/register")
    def skill_manager_register_post(body: SkillRegistration) -> dict[str, Any]:
        try:
            return manager.register(body.service, body.skills, service_label=body.service_label)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"skill registry temporarily unavailable: {exc}") from exc

    @app.post("/api/skill-manager/ingest")
    def skill_manager_ingest_post(registration_json: str = Body(..., media_type="text/plain")) -> dict[str, Any]:
        try:
            return manager.ingest_registration(registration_json)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"skill registry temporarily unavailable: {exc}") from exc

    @app.post("/api/skill-manager/skills")
    def skill_manager_skill_register_post(body: SkillItemRegistration) -> dict[str, Any]:
        try:
            return manager.register_skill(body.skill, service_id=body.service, service_label=body.service_label)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"skill registry temporarily unavailable: {exc}") from exc

    @app.post("/api/skill-manager/skills/{skill_name}/tools")
    def skill_manager_tool_register_post(skill_name: str, body: ToolRegistration) -> dict[str, Any]:
        try:
            return manager.register_tool(skill_name, body.tool)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"skill registry temporarily unavailable: {exc}") from exc

    @app.delete("/api/skill-manager/skills/{skill_name}")
    def skill_manager_skill_delete(skill_name: str) -> dict[str, Any]:
        try:
            return {"skill": skill_name, "removed": manager.remove_skill(skill_name)}
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"skill registry temporarily unavailable: {exc}") from exc

    @app.delete("/api/skill-manager/skills/{skill_name}/tools/{tool_name}")
    def skill_manager_tool_delete(skill_name: str, tool_name: str) -> dict[str, Any]:
        try:
            return {"skill": skill_name, "tool": tool_name, "removed": manager.remove_tool(skill_name, tool_name)}
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"skill registry temporarily unavailable: {exc}") from exc

    @app.delete("/api/skill-manager/services/{service}")
    def skill_manager_unregister_delete(service: str) -> dict[str, Any]:
        try:
            return {"service": service, "removed": manager.unregister(service)}
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"skill registry temporarily unavailable: {exc}") from exc
