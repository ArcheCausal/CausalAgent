from flask import Flask

from app.request_context import current_request_log_details
from observability.logging_runtime import log_event


class CausalFlask(Flask):
    """只替换 Flask 默认未处理异常日志，不改变 500 响应语义。"""

    def log_exception(self, exc_info) -> None:
        log_event(
            self.logger,
            "web.request.unhandled",
            details={**current_request_log_details(include_route=True)},
            exc_info=exc_info,
        )


def create_app():
    from config.settings import settings
    from app.db import check_database_readiness
    from app.auth.routes import auth_bp
    from app.chat.routes import chat_bp
    from app.chat.page_routes import dashboard_asset_bp, dashboard_page_bp
    from app.files.routes import files_bp
    from app.agent.routes import agent_bp
    from app.main.routes import main_bp
    from app.admin.routes import admin_bp, admin_page_bp
    from app.analytics.routes import analytics_bp
    from app.request_context import register_request_context
    from app.rag_eval.routes import rag_eval_bp
    from app.rag_eval.page_routes import rag_eval_page_bp

    # 四个前端都由显式蓝图按路径提供，应用不再暴露 Flask 默认的 /static 目录。
    app = CausalFlask(__name__)
    app.secret_key = settings.SECRET_KEY
    register_request_context(app)

    check_database_readiness()

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(dashboard_page_bp)
    app.register_blueprint(dashboard_asset_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_page_bp)
    app.register_blueprint(rag_eval_bp)
    app.register_blueprint(rag_eval_page_bp)
    app.register_blueprint(analytics_bp)
    return app


__all__ = ["CausalFlask", "create_app"]
