from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    host = os.environ.get("AUDIT_HOST", "127.0.0.1")
    port = int(os.environ.get("AUDIT_PORT", "8765"))
    if not 1 <= port <= 65535:
        raise ValueError(f"端口超出有效范围：{port}")
    uvicorn.run("platform_app.api:app", host=host, port=port, reload=False)
