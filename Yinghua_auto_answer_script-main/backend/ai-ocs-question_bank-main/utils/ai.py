import importlib
import json

# 读取配置文件
with open('config.json', 'r', encoding='utf-8') as f:
    API_CONFIG = json.load(f)

# provider 名称到实现模块的映射
PROVIDER_MODULES = {
    'sensenova': 'utils.sensenova',   # 商汤日日新
    'bailian': 'utils.bailian',       # 阿里百炼
    'volcengine': 'utils.volcengine', # 火山引擎
    'flow': 'utils.flow',             # 硅基流动
    'siliconflow': 'utils.flow',      # 硅基流动别名
}

DEFAULT_PROVIDER = 'sensenova'


def query_large_model(title, options, question_type):
    """
    根据 config.json 中的 provider 字段分发到对应的大模型实现。

    provider 可选值:
        sensenova  商汤日日新（默认）
        bailian    阿里百炼
        volcengine 火山引擎
        flow       硅基流动
    """
    provider = str(API_CONFIG.get('provider') or DEFAULT_PROVIDER).strip().lower()
    module_name = PROVIDER_MODULES.get(provider)

    if not module_name:
        return (
            f"API调用失败: 未知的 AI 提供商 '{provider}'，"
            f"可选值: {', '.join(sorted(PROVIDER_MODULES))}"
        )

    module = importlib.import_module(module_name)
    return module.query_large_model(title, options, question_type)
