"""`python -m sheetmate` 启动后端服务。"""

import uvicorn

from sheetmate.core.config.settings import settings


def main() -> None:
    uvicorn.run("sheetmate.app:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
