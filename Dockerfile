# 稽核平台 运行镜像
#
# 构建：
#   docker build -t jihe-audit:latest .
# 运行：
#   docker run -d --name jihe-audit -p 8765:8765 \
#     -v "$PWD/output:/app/output" \
#     jihe-audit:latest
# 访问：http://localhost:8765

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# LibreOffice：parser/word_header.py 用 shutil.which("soffice") 查找，
#   装进 PATH 即可被识别，不必改 config/paths.json。
# fonts-noto-cjk：中文文档转换必需，缺失会导致 Word/PDF 转出乱码。
# 依赖用的是 opencv-python-headless，因此不需要 libgl1 / libglib2.0-0。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-calc \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖单独成层：只改业务代码时不会触发重装
COPY requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

# rapidocr 会把 opencv-python（非 headless）作为依赖拖进来，它与清单里显式
# 指定的 opencv-python-headless 争抢同一个 cv2 模块（谁后装谁赢），而非
# headless 版运行时需要 libGL.so.1 —— 镜像里没有这个库，LibreOffice 带来的
# 那批图形库并不包含它。
#
# 代码里 audit_rules/stb_04.py、parser/document_parsers/_shared/pdf_light.py
# 等都是顶层 `import cv2`，不修会在导入阶段直接崩，不是可选优化。
#
# 两个都卸掉再单独装 headless，确保 cv2 只来自 headless 版本。
RUN pip uninstall -y opencv-python opencv-python-headless \
    && pip install --no-cache-dir --no-deps opencv-python-headless \
    && python -c "import cv2; print('cv2', cv2.__version__, cv2.__file__)"

COPY . .

ENV AUDIT_HOST=0.0.0.0 \
    AUDIT_PORT=8765 \
    AUDIT_PLATFORM_OUTPUT=/app/output/单系统

RUN mkdir -p /app/output/单系统

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/').read()" || exit 1

CMD ["python", "app.py"]
