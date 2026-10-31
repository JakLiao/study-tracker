"""/api/projects + /api/project-templates (M1 统一任务池)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from .. import config
from ..models import (
    ProjectCreate,
    ProjectDTO,
    ProjectPatch,
    ProjectTemplate,
    ProjectTemplatesResponse,
)
from ..services import project_service

router = APIRouter(prefix="/api", tags=["projects"])


@router.get("/projects")
def list_projects(include_archived: bool = Query(False)) -> dict:
    items = project_service.list_projects(include_archived)
    return {"items": [p.model_dump(mode="json") for p in items]}


@router.post("/projects", response_model=ProjectDTO)
def create_project(payload: ProjectCreate) -> ProjectDTO:
    return project_service.create_project(payload)


@router.patch("/projects/{project_id}", response_model=ProjectDTO)
def patch_project(project_id: int, payload: ProjectPatch) -> ProjectDTO:
    return project_service.update_project(project_id, payload)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int) -> dict:
    """归档而不是物理删除.

    历史作业仍引用这个 project_id, 物理删除会让"这周练了几次琴"的统计凭空消失;
    归档后项目不再出现在录入候选里, 但历史记录照常可查。
    """
    project_service.update_project(project_id, ProjectPatch(status="archived"))
    return {"id": project_id, "status": "archived"}


@router.get("/project-templates", response_model=ProjectTemplatesResponse)
def project_templates() -> ProjectTemplatesResponse:
    return ProjectTemplatesResponse(
        items=[
            ProjectTemplate.model_validate(
                {
                    "key": t["key"],
                    "label": t["label"],
                    "task_kind": t["task_kind"],
                    "project_type": t["project_type"],
                    "default_name": t["default_name"],
                    "tasks": list(t["tasks"]),
                }
            )
            for t in config.PROJECT_TEMPLATES
        ]
    )
