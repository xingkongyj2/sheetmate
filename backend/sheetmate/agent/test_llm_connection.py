"""填写配置，直接调用项目现有的 LLM 连接测试。

在项目根目录运行：
    backend/.venv/bin/python backend/sheetmate/agent/test_llm_connection.py
也可以在 PyCharm 中直接运行本文件。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# 支持从任意工作目录直接运行，确保可以导入项目代码。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sheetmate.agent.llm import LLMError, test_connection as check_connection
from sheetmate.config import LLMSettings


# ===== 在这里填写自己的配置 =====
BASE_URL = ""  # API 基础地址，不要加 /chat/completions。
API_KEY = ""
MODEL = ""
# ==============================


def main() -> int:
    if not all(value.strip() for value in (BASE_URL, API_KEY, MODEL)):
        print("请先填写文件顶部的 BASE_URL、API_KEY、MODEL。")
        return 1

    cfg = LLMSettings(api_key=API_KEY.strip(), base_url=BASE_URL.strip(), model=MODEL.strip())
    started = time.perf_counter()
    try:
        result = asyncio.run(check_connection(cfg))
    except LLMError as exc:
        print(f"连接失败：{str(exc).replace(cfg.api_key, '[密钥已隐藏]')}")
        return 1

    print(f"连接成功，耗时 {time.perf_counter() - started:.2f} 秒")
    print(f"模型：{result['model']}")
    print(f"回复：{result['reply']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("测试已取消。")
        raise SystemExit(130)
