"""四个前端共享的构建阶段、产物目录和资源前缀参数。

文档职责：为 Dockerfile、Compose 和入口契约提供唯一的前端参数表，避免管理员、
普通用户、官网和 RAG 评测台各自复制同名目录与变量。

适用范围：前端构建阶段、镜像产物目录或资源前缀变化时；各入口独有的权限、安全
和路由约束仍由对应契约测试维护。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrontendLayout:
    """一个前端工程的构建阶段、产物目录和资源前缀。"""

    name: str
    builder_stage: str
    dist_env: str
    dist_dir: str
    copy_source: str
    asset_base: str
    vite_config: str


FRONTEND_LAYOUTS = (
    FrontendLayout(
        name="admin",
        builder_stage="admin-builder",
        dist_env="ADMIN_FRONTEND_DIST_DIR",
        dist_dir="/opt/causalagent-admin",
        copy_source="/frontend/dist",
        asset_base="/admin/",
        vite_config="admin-frontend/vite.config.ts",
    ),
    FrontendLayout(
        name="chat",
        builder_stage="chat-builder",
        dist_env="CHAT_FRONTEND_DIST_DIR",
        dist_dir="/opt/causalagent-chat",
        copy_source="/frontend/dist",
        asset_base="/dashboard-assets/",
        vite_config="chat-frontend/vite.config.ts",
    ),
    FrontendLayout(
        name="website",
        builder_stage="website-builder",
        dist_env="WEBSITE_FRONTEND_DIST_DIR",
        dist_dir="/opt/causalagent-website",
        copy_source="/frontend/dist",
        asset_base="/site-assets/",
        vite_config="website-frontend/vite.config.ts",
    ),
    FrontendLayout(
        name="rag_eval",
        builder_stage="rag-eval-builder",
        dist_env="RAG_EVAL_FRONTEND_DIST_DIR",
        dist_dir="/opt/causalagent-rag-eval",
        copy_source="/workspace/app/rag_eval/frontend_dist",
        asset_base="/rag-eval/",
        vite_config="app/rag_eval/frontend/vite.config.ts",
    ),
)

FRONTENDS_BY_NAME = {layout.name: layout for layout in FRONTEND_LAYOUTS}
