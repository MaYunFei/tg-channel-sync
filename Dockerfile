FROM python:3.10-slim

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    TG_SYNC_HOST=0.0.0.0 \
    TG_SYNC_PORT=8011

WORKDIR /app

# 安装必要的系统工具（构建 TgCrypto 等可能需要编译环境）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 暴露端口
EXPOSE 8011

# 启动服务
CMD ["python", "main.py"]
