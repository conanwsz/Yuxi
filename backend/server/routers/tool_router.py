from fastapi import APIRouter, Depends

from yuxi.agents.toolkits.service import get_tool_metadata
from server.utils.auth_middleware import require_permission
from yuxi.storage.postgres.models_business import User
from yuxi.services.permission_service import has_permission
from yuxi.services.resource_access_runtime_service import filter_tool_metadata_for_user

tools = APIRouter(prefix="/system/tools", tags=["tools"])


@tools.get("")
async def list_tools(
    category: str = None,
    user: User = Depends(require_permission("tools.read")),
):
    """获取工具列表"""
    data = get_tool_metadata(category)
    if not has_permission(user, "tools.manage"):
        data = filter_tool_metadata_for_user(user, data)
    return {"success": True, "data": data}


@tools.get("/options")
async def get_tool_options(
    user: User = Depends(require_permission("tools.read")),
):
    """获取工具选项（前端下拉框用）"""
    all_tools = filter_tool_metadata_for_user(user, get_tool_metadata())
    return {"success": True, "data": [{"label": t["name"], "value": t["slug"]} for t in all_tools]}
