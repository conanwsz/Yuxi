from fastapi import APIRouter, Depends, HTTPException, Query

from yuxi.storage.postgres.models_business import User
from yuxi.services.task_service import tasker
from server.utils.auth_middleware import require_permission

tasks = APIRouter(prefix="/tasks", tags=["tasks"])


@tasks.get("")
async def list_tasks(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    current_user: User = Depends(require_permission("system.tasks.manage")),
):
    """List tasks, optionally filtered by status."""
    return await tasker.list_tasks(status=status, limit=limit)


@tasks.get(
    "/{task_id}",
    summary="查询后台任务进度和结果",
    description=(
        "知识文档处理接口返回 `task_id` 后轮询本接口。`task.status` 为 `pending` 或 `running` 时继续等待；"
        "`success` 表示解析/入库链路完成；`failed` 或 `cancelled` 必须记录错误并处理。需要 `system.tasks.manage`。"
    ),
    responses={404: {"description": "任务不存在或已被清理"}},
)
async def get_task(task_id: str, current_user: User = Depends(require_permission("system.tasks.manage"))):
    """Retrieve a single task by id."""
    task = await tasker.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task}


@tasks.post("/{task_id}/cancel")
async def cancel_task(task_id: str, current_user: User = Depends(require_permission("system.tasks.manage"))):
    """Request cancellation of a task."""
    success = await tasker.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Task cannot be cancelled")
    return {"task_id": task_id, "status": "cancelled"}


@tasks.delete("/{task_id}")
async def delete_task(task_id: str, current_user: User = Depends(require_permission("system.tasks.manage"))):
    """Delete a task by id."""
    success = await tasker.delete_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "status": "deleted"}
