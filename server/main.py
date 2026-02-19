"""
入口文件，保持 `uvicorn main:app` 兼容。
"""
from app.main import app

__all__ = ["app"]
