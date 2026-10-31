# AI 题库

一个基于 Flask 的智能题库 API 系统，支持多种 AI 模型（商汤日日新/火山引擎/阿里百炼/硅基流动）自动回答问题。

## 功能特点

- 支持多种题型（单选、多选、判断、填空）
- 集成多个AI模型接口
- MySQL 数据库缓存题目答案
- RESTful API 设计
- 跨域支持

## 环境要求

- Python 3.8+
- MySQL 5.7+（仅 `main.py` 需要）
- pip 包管理器

## 安装依赖(加了镜像)

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

如果只使用商汤日日新，安装以下包即可（不需要 dashscope / volcengine SDK）：

```bash
pip install flask flask-cors requests python-dotenv -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 配置

在 `config.json` 文件中设置以下配置：

- `host`: API服务器地址
- `port`: API服务器端口
- `provider`: AI 提供商，可选 `sensenova`（商汤，默认）、`bailian`（阿里百炼）、`volcengine`（火山引擎）、`flow`（硅基流动）
- `api_key`: 对应提供商的 API 密钥（`sensenova` 也可单独写在 `sensenova.api_key`）
- `sensenova`: 商汤日日新的接口配置
- `mysql`: MySQL 连接配置（仅 `main.py` 需要）

所有 AI 实现都通过 `utils/ai.py` 分发，切换模型时只需要改 `provider`，不需要改代码。

## 商汤日日新配置

### 1. 获取 API Key

商汤目前有两套接口，按你账号实际开通的平台选择：

| 平台 | 控制台 | 接口地址 | 常用模型 |
|---|---|---|---|
| 新平台（推荐） | https://platform.sensenova.cn/console/keys | `https://token.sensenova.cn/v1/chat/completions` | `sensenova-6.8-flash-lite` |
| 旧版 SenseCore | https://console.sensecore.cn/aistudio/management/api-key | `https://api.sensenova.cn/compatible-mode/v2/chat/completions` | `SenseNova-V6-5-Pro`、`SenseChat-5` |

> 两套平台的 Key 不通用，请在对应控制台创建。具体可用模型以控制台模型列表为准。

### 2. 修改 `config.json`

新平台（默认配置，直接填 Key 即可）：

```json
{
    "provider": "sensenova",
    "sensenova": {
        "api_key": "sk-你的商汤Key",
        "base_url": "https://token.sensenova.cn/v1/chat/completions",
        "model": "sensenova-6.8-flash-lite",
        "temperature": 0.1,
        "max_tokens": 4096,
        "max_tokens_field": "max_tokens",
        "reasoning_effort": "high",
        "timeout": 120
    }
}
```

旧版 SenseCore 兼容模式（如果你只有旧平台账号）：

```json
{
    "provider": "sensenova",
    "sensenova": {
        "api_key": "旧版控制台的 API Key",
        "base_url": "https://api.sensenova.cn/compatible-mode/v2/chat/completions",
        "model": "SenseNova-V6-5-Pro",
        "temperature": 0.1,
        "max_tokens": 4096,
        "max_tokens_field": "max_completion_tokens",
        "reasoning_effort": "medium",
        "timeout": 120
    }
}
```

配置项说明：

- `temperature`：建议 `0.1`，降低随机性
- `max_tokens`：最大输出长度；开启推理时会占用一部分预算，建议不低于 4096
- `max_tokens_field`：新平台用 `max_tokens`，旧版兼容模式用 `max_completion_tokens`
- `reasoning_effort`：推理强度，可选 `low` / `medium` / `high` / `max` / `none`；不需要可设为空字符串
- `timeout`：请求超时秒数，推理模型建议保留 120 秒

也可以不把 Key 写进 `config.json`，改用环境变量。读取优先级为：

```
环境变量 SENSENOVA_API_KEY  >  config.json 的 sensenova.api_key
```

**方式一：临时环境变量（只对当前终端窗口有效）**

Git Bash / Linux / macOS：

```bash
export SENSENOVA_API_KEY="sk-你的商汤Key"
python main_non_database.py
```

PowerShell：

```powershell
$env:SENSENOVA_API_KEY="sk-你的商汤Key"
python main_non_database.py
```

CMD：

```cmd
set SENSENOVA_API_KEY=sk-你的商汤Key
python main_non_database.py
```

**方式二：永久环境变量（Windows）**

```powershell
setx SENSENOVA_API_KEY "sk-你的商汤Key"
```

执行后需要**重新打开一个终端**再启动服务。

**方式三：使用 `.env` 文件（推荐，跨平台）**

把 `.env.example` 复制为 `.env`，填入 Key：

```bash
cp .env.example .env
```

`.env` 内容：

```dotenv
SENSENOVA_API_KEY=sk-你的商汤Key
```

然后直接启动即可，不需要 `export`。`.env` 已加入 `.gitignore`，不会被提交。该方式需要安装 `python-dotenv`。

> `config.json` 中如果填了 Key，也请不要提交到公开仓库。

### 3. 先单独测试商汤接口

填好 Key 后，可以直接运行适配器自检，不需要启动 Flask：

```bash
python utils/sensenova.py
```

正常时会输出类似 `{"answer":"北京###华盛顿"}` 的内容。

### 3. 启动

```bash
# 无数据库，最简单
python main_non_database.py

# 有 MySQL 缓存
python main.py
```

启动后终端会打印 API 配置，前端脚本使用 `http://127.0.0.1:5000/api/query` 即可。

## 如何运行

__无数据库的版本__
```bash
python main_non_database.py
```

__有数据库的版本（需要先配置并启动 MySQL）__
```bash
python main.py
```

## 测试

```bash
python test_api.py
```
