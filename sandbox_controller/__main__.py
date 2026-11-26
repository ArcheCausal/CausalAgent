"""控制服务固定运行单个 ASGI 进程。"""

import uvicorn

from sandbox_controller.api import create_app
from sandbox_controller.config import ControllerSettings


def main() -> None:
    settings = ControllerSettings.from_environment()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
