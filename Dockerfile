FROM python:3.11-slim

WORKDIR /app

# 复制项目文件
COPY requirements.txt .
COPY divers/HG/baoqi/hg_baoqi.py ./divers/HG/baoqi/hg_baoqi.py
COPY common/ ./common/
COPY models/ ./models/

# 安装依赖
RUN pip install --no-cache-dir -i https://pypi.doubanio.com/simple -r requirements.txt

# 设置PYTHONPATH
ENV PYTHONPATH=/app

# 设置工作目录
WORKDIR /app/divers/HG/baoqi

# 运行应用程序
CMD ["python", "hg_baoqi.py"]