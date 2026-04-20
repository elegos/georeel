from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from georeel.server.workspace import get_manager

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreated(BaseModel):
    workspace_id: str


@router.post("", response_model=WorkspaceCreated, status_code=201)
async def create_workspace() -> WorkspaceCreated:
    ws = get_manager().create()
    return WorkspaceCreated(workspace_id=ws.workspace_id)


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(workspace_id: str) -> None:
    deleted = get_manager().delete(workspace_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workspace not found")
