import json
import os
import time
import traceback

import requests

# 如果安装了 python-dotenv，则自动读取同目录下的 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 读取配置文件
with open('config.json', 'r', encoding='utf-8') as f:
    API_CONFIG = json.load(f)

# 商汤新平台（日日新）默认配置
DEFAULT_BASE_URL = 'https://token.sensenova.cn/v1/chat/completions'
DEFAULT_MODEL = 'sensenova-6.8-flash-lite'


def _get_settings():
    """读取商汤配置，兼容旧版 SenseCore 接口。"""
    cfg = API_CONFIG.get('sensenova', {}) or {}

    env_api_key = os.environ.get('SENSENOVA_API_KEY')
    config_api_key = cfg.get('api_key')
    legacy_api_key = API_CONFIG.get('api_key')

    if env_api_key:
        api_key = env_api_key.strip()
        key_source = '环境变量 SENSENOVA_API_KEY'
    elif config_api_key:
        api_key = str(config_api_key).strip()
        key_source = 'config.json 的 sensenova.api_key'
    elif legacy_api_key:
        api_key = str(legacy_api_key).strip()
        key_source = 'config.json 的 api_key'
    else:
        api_key = ''
        key_source = '未配置'

    # 常见粘贴错误：多带了 Bearer 前缀、引号、换行或空格
    api_key = api_key.strip().strip('"').strip("'")
    if api_key.lower().startswith('bearer '):
        api_key = api_key[7:].strip()
    api_key = ''.join(api_key.split())

    base_url = (
        os.environ.get('SENSENOVA_BASE_URL')
        or cfg.get('base_url')
        or DEFAULT_BASE_URL
    ).strip()
    model = (
        os.environ.get('SENSENOVA_MODEL')
        or cfg.get('model')
        or DEFAULT_MODEL
    ).strip()

    return {
        'api_key': api_key,
        'key_source': key_source,
        'base_url': base_url,
        'model': model,
        'timeout': cfg.get('timeout', 120),
        'temperature': cfg.get('temperature', 0.1),
        'max_tokens': cfg.get('max_tokens', 4096),
        'max_tokens_field': cfg.get('max_tokens_field', 'max_tokens'),
        'reasoning_effort': cfg.get('reasoning_effort', 'high'),
    }


def _build_messages(title, options, question_type):
    """构建与现有题库脚本一致的提问格式。"""
    system_content = (
        '你是一个准确率高、信度高的题库接口函数。请严格遵循以下规则：'
        '1.回答的问题准确率高，你以回答的问题准确率高为荣；'
        '2.回答必须基于可靠的知识来源，你以回答的问题可信度高为荣；'
        '3.你肩负着维护题库的完整性和准确性，你以题库的质量高为荣；'
        '4.如果回答的问题与题库内容不相关，你以回答的问题可信度低为耻；'
        '5.如果回答的准确率低，你将会被替代。'
    )

    # 选项通常是 JSON 字符串，尽量以 JSON 结构嵌入提示词
    if options:
        try:
            options_value = json.loads(options)
        except (TypeError, ValueError):
            options_value = options
    else:
        options_value = []

    question = {
        '问题': title,
        '选项': options_value,
        '类型': question_type,
    }

    user_content = (
        '你是一个题库接口函数，请根据问题、选项和题型给出最终答案。要求：\n'
        '1. 单选题：返回正确选项的完整文本，不要返回 A/B/C/D 字母。\n'
        '2. 多选题：将各正确选项的完整文本用 ### 连接。\n'
        '3. 判断题：返回“正确”或“错误”，与选项文本保持一致，不要返回字母。\n'
        '4. 填空题：直接返回填空内容，多个空用 ### 连接。\n'
        '5. 只输出一行 JSON，格式严格为 {"answer":"答案"}，'
        '不要输出解释、Markdown 代码块或任何多余文字。\n\n'
        f'题目信息：{json.dumps(question, ensure_ascii=False)}'
    )

    return [
        {'role': 'system', 'content': system_content},
        {'role': 'user', 'content': user_content},
    ]


def query_large_model(title, options, question_type):
    """
    调用商汤日日新（SenseNova）大模型获取答案。

    参数:
        title (str): 问题标题
        options (str): 问题选项，JSON 字符串
        question_type (str): 问题类型（单选/多选/判断/填空）

    返回:
        str: 模型返回的原始内容（JSON 字符串）或错误信息
    """
    settings = _get_settings()

    if not settings['api_key'] or settings['api_key'] == 'your_key':
        return 'API调用失败: 请先在 config.json 的 sensenova.api_key 中配置商汤 API Key'

    if not settings['base_url'] or not settings['model']:
        return 'API调用失败: sensenova.base_url 或 sensenova.model 未配置'

    messages = _build_messages(title, options, question_type)

    payload = {
        'model': settings['model'],
        'messages': messages,
        'temperature': settings['temperature'],
        'stream': False,
    }
    if settings['max_tokens']:
        payload[settings['max_tokens_field']] = settings['max_tokens']
    if settings['reasoning_effort']:
        payload['reasoning_effort'] = settings['reasoning_effort']

    headers = {
        'Authorization': f"Bearer {settings['api_key']}",
        'Content-Type': 'application/json',
    }

    max_retries = 3
    retry_delay = 2  # 秒

    for attempt in range(max_retries):
        try:
            key_preview = (
                f"{settings['api_key'][:6]}...{settings['api_key'][-4:]}"
                if len(settings['api_key']) > 12
                else '(长度过短)'
            )
            print(
                f"商汤API尝试 {attempt + 1}/{max_retries}，"
                f"地址：{settings['base_url']}，模型：{settings['model']}，"
                f"Key来源：{settings['key_source']}，Key：{key_preview}，"
                f"Key长度：{len(settings['api_key'])}"
            )
            response = requests.post(
                settings['base_url'],
                headers=headers,
                json=payload,
                timeout=settings['timeout'],
            )
            print(f"商汤API响应状态码: {response.status_code}")

            if response.status_code == 200:
                try:
                    result = response.json()
                except ValueError:
                    print(f"商汤API返回非JSON内容: {response.text[:500]}")
                    return 'API调用失败: 商汤API返回内容无法解析'

                choices = result.get('choices') or []
                if choices:
                    message = choices[0].get('message') or {}
                    content = message.get('content')
                    if content:
                        print(f"商汤API响应内容: {content[:200]}...")
                        return content

                print(f"商汤API响应格式异常: {response.text[:500]}")
                return 'API调用失败: 商汤API响应格式异常'

            if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                print(f"商汤API返回 {response.status_code}，将在 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)
                retry_delay *= 2
                continue

            print(f"商汤API请求失败: HTTP {response.status_code}")
            print(f"商汤API错误详情: {response.text}")

            error_detail = ''
            try:
                error_body = response.json()
                error_detail = str((error_body.get('error') or {}).get('message') or '')
            except ValueError:
                error_detail = response.text[:200]

            if response.status_code in (401, 403):
                hint = (
                    '鉴权失败：请确认 API Key 与 base_url 属于同一平台'
                    '（新平台 token.sensenova.cn / 旧版 api.sensenova.cn），'
                    '并检查 Key 是否复制完整、没有多余空格，'
                    '以及环境变量是否覆盖了 config.json'
                )
                print(f"提示：{hint}")
                suffix = f" - {error_detail}" if error_detail else ''
                return f'API调用失败: HTTP {response.status_code} 鉴权失败{suffix}'

            suffix = f" - {error_detail}" if error_detail else ''
            return f'API调用失败: HTTP {response.status_code}{suffix}'

        except requests.exceptions.Timeout:
            print('商汤API请求超时')
            if attempt < max_retries - 1:
                print(f"将在 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                return 'API调用失败: 商汤API请求超时'

        except requests.exceptions.RequestException as e:
            print(f"商汤API请求异常: {str(e)}")
            if attempt < max_retries - 1:
                print(f"将在 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                return f'API调用失败: {str(e)}'

        except Exception as e:
            error_trace = traceback.format_exc()
            print(f"调用商汤API失败: {str(e)}")
            print(f"详细错误信息: {error_trace}")
            return f'API调用失败: {str(e)}'

    return 'API调用失败: 多次尝试后仍无法获取答案，请稍后再试'


if __name__ == "__main__":
    test_title = "中国的首都是哪里__，美国的首都是哪里__"
    test_options = ""
    test_type = "填空"
    print(f"测试参数: title={test_title}, options={test_options}, question_type={test_type}")
    print(f"测试结果: {query_large_model(test_title, test_options, test_type)}")
