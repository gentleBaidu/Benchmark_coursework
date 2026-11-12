# -*- coding: utf-8 -*-
"""启动后端服务：python 后端核心/代码/run_server.py

环境变量：
  BENCHMARK_DB_HOST / BENCHMARK_APP_PASSWORD   题库连接（必需）
  MODEL_BASE_URL / MODEL_NAME / MODEL_API_KEY  接真实模型时可选
  PORT                                         默认 8000
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))            # 后端核心/代码
ROOT = os.path.dirname(os.path.dirname(BASE_DIR))                 # 项目根
sys.path.insert(0, BASE_DIR)
PYLIBS = os.path.join(ROOT, ".pylibs")
if os.path.isdir(PYLIBS):
    sys.path.insert(0, PYLIBS)   # 优先用项目自带依赖，避免和系统包版本冲突

import uvicorn  # noqa: E402

from backend.api import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
