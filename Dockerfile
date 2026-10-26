FROM python:3.13-slim

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 创建配置文件目录并提供默认配置
RUN mkdir -p /config && \
    cp config_template.ini /config/config.ini

# 非 root 运行；/app 与 /config 需要可写（Cookie / 日志 / 配置）
RUN useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app /config

VOLUME /config

USER appuser

# 使用配置文件启动应用
ENTRYPOINT ["python3", "main.py", "-c", "/config/config.ini"]
