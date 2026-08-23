# kjfwd-bot

[![CI](https://github.com/hzwzwzw/kjfwd-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/hzwzwzw/kjfwd-bot/actions/workflows/ci.yml)

面向微信群电脑软硬件答疑的 bowxt Agent 插件。它负责问题分类、动态 conversation、发送人感知、
skills、联网搜索、多模态上下文和回答生成；微信消息接入、实例配置、consumer 身份、进程生命周期、
状态与日志由 bowxt 统一管理。

## 架构边界

正常部署只有一个控制面：bowxt。

```text
官方 Linux 微信
      │ 可见 UI：AT-SPI 读取 + XTest 键鼠 + 剪贴板
      ▼
bowxt 数据面
  - 单一 UI worker
  - 消息存储、图片、发送队列
  - durable consumer / lease / ACK / NACK
      │
      ▼
bowxt Agent 控制面
  - 安装插件发现
  - 实例配置与密钥
  - consumer 分配
  - 启动、停止、重启、自动启动
  - 状态与分 Agent 日志
      │
      ▼
kjfwd-bot 插件实例
  - 只处理分配给自己的群消息
  - 调用模型、搜索和 skills
  - 返回 ReplyAction / ForwardAction
```

职责划分：

| 能力 | bowxt | kjfwd-bot |
| --- | --- | --- |
| 操作微信窗口、读取控件树、发送键鼠 | 负责 | 禁止 |
| 消息持久化、图片抓取、发送队列 | 负责 | 通过 Agent API 使用 |
| 插件安装发现、实例配置、密钥保存 | 负责 | 只声明 manifest 和配置结构 |
| consumer 身份、启停、自启动、进程状态 | 负责 | 接受控制面注入 |
| 群监听策略、问题分类、conversation | 不处理 | 负责 |
| LLM、搜索、skills、回答生成 | 不处理 | 负责 |
| Agent stdout/stderr 和业务日志展示 | 汇总展示 | 产生日志 |

kjfwd-bot 对控制面只提供三个入口：

- `bowxt-agent.json`：插件安装清单；
- `app.py`：由 bowxt 调用的受管运行入口；
- `config.example.json`：WebIM 创建实例时使用的默认业务配置。

bowxt 启动实例时注入 `BOWXT_MANAGED=1`、`BOWXT_BASE_URL`、`BOWXT_AGENT_ID`、
`BOWXT_CONSUMER` 和 `BOWXT_AGENT_DATA_DIR`。其中实例 ID 就是 durable consumer，优先级高于
配置文件中的兼容字段。不要在多个实例之间复用实例 ID。

插件清单声明 30 秒停止宽限期，使最长 20 秒的消息长轮询、ACK/NACK 和历史数据库关闭能在 bowxt
重启或停止实例时正常完成；超时后才由控制面强制结束进程。

`kjfwd-bot --standalone` 只用于 bowxt Agent 控制面不可用时的故障回退，不是推荐部署方式。

## 安全边界

整个链路只操作官方 Linux 微信的可见界面：只读 AT-SPI 控件树，使用 XTest 键鼠和系统剪贴板。
不使用微信协议、直接发包、进程注入、数据库解密、深层控件写入、OCR 或缓存目录扫描。图片只通过
可见图片查看器和 `Ctrl+C` 获取。

Web/API 默认只绑定 `127.0.0.1`。请只用于自己的账号和已授权的小规模会话，不要用于群发、营销或
绕过微信限制。

## 推荐部署：由 bowxt 托管

要求：Linux amd64、Docker Engine、Python 3.10+，以及可用手机确认登录的微信账号。

### 1. 安装插件源码

将两个仓库放在同一父目录。kjfwd-bot 不需要创建 venv，也不需要手工启动进程：

```bash
mkdir wechat-agents && cd wechat-agents
git clone https://github.com/hzwzwzw/bowxt.git
git clone https://github.com/hzwzwzw/kjfwd-bot.git
```

默认 `bowxt/manage.sh`（包括 `scripts/init.sh`）和 `compose.yaml` 都会检测/挂载
`../kjfwd-bot`，把它只读安装到受信任插件目录。若目录不相邻，在 `bowxt/.env` 指定绝对路径：

```dotenv
BOWXT_AGENT_PLUGIN_HOST_DIR=/absolute/path/to/kjfwd-bot
```

这一步就是 kjfwd-bot 的安装入口。插件代码保持只读；实例配置、密钥、历史和运行数据写入
`bowxt-home` volume，不写回源码目录。

### 2. 启动 bowxt 并登录微信

```bash
cd bowxt
cp .env.example .env
```

配置当前微信账号在群里的昵称：

```dotenv
BOWXT_MY_NAMES=kirotta
BOWXT_SYNC_MODE=unread
BOWXT_UIA_SENDER=1
VNC_SCOPE=window
```

构建并启动：

```bash
./scripts/init.sh
```

打开：

- WebIM 与 Agent 控制面：<http://127.0.0.1:8787/>
- 微信 noVNC：<http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale&reconnect=1&reconnect_delay=1000>

第一次在 noVNC 中扫码并用手机确认登录。登录状态保存在 `bowxt-home` volume；关闭浏览器或断开
noVNC 不会停止 bowxt 和 Agent。

跨主机访问请使用 SSH 隧道，不要把端口直接暴露到公网：

```bash
ssh -L 8787:127.0.0.1:8787 -L 6080:127.0.0.1:6080 USER@DOCKER_HOST
```

### 3. 在 WebIM 创建实例

进入 WebIM 的“Agent”页：

1. 点击“添加 Agent”，选择“柯基服务队答疑 Agent”；
2. 设置稳定且唯一的实例 ID，例如 `kjfwd-prod`；
3. 设置显示名称，例如“客户群答疑”；
4. 在配置 JSON 中填写群聊和业务策略；
5. 在密钥区填写模型 API Key、Base URL、模型名称和可选 Brave Key；
6. 建议开启“bowxt 启动时自动运行”，保存后点击“启动”。

实例 ID 由 bowxt 作为 consumer 注入。不要在配置 JSON 中手工维护 `bowxt.base_url` 或
`bowxt.consumer`；`config.example.json` 也不再提供这两个控制面字段。

最小业务配置：

```json
{
  "groups": [
    {
      "name": "博特泰斯特",
      "bot_nickname": "kirotta",
      "listen_mode": "question_only",
      "always_reply_to_mentions": true,
      "reply_groups": ["博特泰斯特"]
    }
  ],
  "bowxt": {
    "claim_timeout_seconds": 20,
    "lease_seconds": 180,
    "batch_size": 8,
    "require_sender": true,
    "replay_existing": false,
    "send_timeout_seconds": 45
  },
  "image_context": {
    "enabled": true,
    "max_images": 4,
    "require_viewer_clipboard": true
  },
  "search": {"enabled": false},
  "documents": {
    "root_path": "documents",
    "max_document_bytes": 262144
  },
  "reply_debounce": {"delay_seconds": 3}
}
```

密钥由 bowxt 保存并以环境变量注入，不放入配置 JSON：

| 密钥 | 用途 |
| --- | --- |
| `API_KEY` | 模型 API Key |
| `BASE_URL` | OpenAI 兼容 API 地址 |
| `MODEL` | 模型名称 |
| `BRAVE_KEY` | Brave Search Key；关闭搜索时可不填 |

Kimi K2.6 国内 API 可在密钥区设置 `BASE_URL=https://api.moonshot.cn/v1`、
`MODEL=kimi-k2.6`，并在 JSON 中设置：

```json
{
  "llm": {
    "temperature": 0.6,
    "thinking_enabled": false
  }
}
```

K2.6 关闭思考时温度必须为 `0.6`。

### 4. 运维与升级

常规运维全部在 WebIM 完成：

- “启动/停止/重启”控制实例生命周期；
- “配置”修改 JSON、密钥和自动启动；
- “查看日志”只显示该实例的生命周期与业务日志；
- “会话信息”自定义面板按“群聊 → 完整 conversation ID → 最近聊天记录”展示当前活跃会话；
- bowxt 重启后自动恢复勾选了自动启动的实例；
- 实例配置、密钥和历史位于 `bowxt-home`，升级插件不会覆盖。

升级 kjfwd-bot：

```bash
cd kjfwd-bot
git pull --ff-only
```

然后在 WebIM 停止并重新启动实例。若 manifest 版本或默认配置有变化，刷新 WebIM 后即可看到；已有
实例配置不会被默认值覆盖。

## 配置语义

### 消息触发

- `mention_only`：仅回复 `is_at_me=true` 的消息；
- `all_messages`：每条非空入站消息都进入回答链路；
- `question_only`：由 LLM 结合群名、发送人和最近群聊判断是否为客户求助；
- `always_reply_to_mentions=true`：明确 @ Agent 昵称时必须回答，未 @ 消息仍遵循 `listen_mode`；
- `reply_groups`：默认回复来源群，也可转发到一个或多个参考群。

`bot_nickname` 用于清理正文中的 @ 文本；是否真的 @ 当前账号以 bowxt 的 `is_at_me` 为准。

### 投递与发送人

- `replay_existing=false`：新实例首次运行把已有消息作为基线，只处理之后的新消息；
- `batch_size`：一次最多领取的消息数；同周期到达的多条消息会一起进入处理链路；
- bowxt 通过 lease + ACK/NACK 至少一次投递，kjfwd-bot 再用 `bowxt:<seq>` 和稳定 `client_id` 去重；
- `require_sender=true`：群消息会等待 bowxt 通过可见资料卡补齐发送人，再调用 Agent；
- `pending` 只表示进入发送队列，`sent` 表示找到微信 UI 回显，`unverified` 不等于对端已读。

多个 Agent 的监听范围应明确划分。如果两个不同实例都订阅同一会话，它们会各自收到消息并可能各自
回复；bowxt 不会替业务层猜测应该由谁回答。

### 连续消息与 conversation

`reply_debounce.delay_seconds` 建议设置为 `3`–`5`。同一 conversation 的新消息会刷新计时，最终只
为最新快照生成一次回复；不同 conversation 互不覆盖。

运行期间，插件每 2 秒检查一次自己的会话历史；内容变化时通过 bowxt 声明式面板 API 发布“会话
信息”。面板沿用 `conversation_pool.active_ttl_seconds` 和 `max_active` 对“活跃”的定义，每个
conversation 展示最近 8 条具体记录，较早记录仍保存在实例历史库中并显示省略数量。该刷新不领取
微信消息，也不触发 UI 切换或键鼠操作。

发送人参与分类、conversation 路由和低信息追问续接，但不会被程序猜测为客户或工作人员。
`/new [问题]`、`/clear [问题]` 开启新上下文，`/help` 返回说明，`/search 问题` 强制搜索。

### 图片上下文

`image_context.enabled=false` 时图片只以 `[图片]` 和元数据进入文本上下文。启用后，Agent 从 bowxt
下载经可见查看器和 `Ctrl+C` 获取的清晰 PNG，校验 SHA-256 后交给多模态模型。

推荐保持 `require_viewer_clipboard=true`，避免把低清窗口截图交给模型。图片在文字之前或之后均可
加入同一发送人的上下文；`trigger_images=false` 时单独图片不会立刻触发回答。

### skills 与提醒

- `skills_path` 下增加 UTF-8 Markdown 即可添加知识 skill；
- `/skill名 问题` 可显式选择 skill；
- `message_reminder` 可把长时间无人接续的问题提醒到指定群；
- `history.database_path`、图片缓存等相对路径位于该实例的独立数据目录。

### 工具与文档库

kjfwd-bot 向主 Agent 提供三个只读工具：

- `get_history(duration)`：读取当前整个群在 `1h`、`1d` 等时间窗口内已被 bowxt 持久化的全部消息；工具自动锁定当前群，模型不能跨群查询。
- `list_doc()`：列出 kjfwd-bot 自己的 Markdown 文档目录树。
- `read_doc(path)`：按 `list_doc` 返回的准确相对路径读取文档。

文档库完全属于 kjfwd-bot，不由 bowxt 提供文档 API。源文档位于插件的
`documents/` 目录，可用子目录分类，只读取可见的 UTF-8 `.md` 文件。受管实例启动时，bowxt
仅把该插件资源复制到实例目录；业务层的目录树、路径校验和文档读取都由
kjfwd-bot 完成。修改文档后重启 Agent 实例即可刷新资源快照。

仓库预置两份调试文档：`documents/调试/文档库接口自检.md` 和
`documents/示例/蓝屏排查示例.md`。`max_document_bytes` 限制单文档大小，路径穿越、隐藏文件和符号链接都会被拒绝。

## 故障回退：独立进程模式

仅当 bowxt Web/Agent 控制面无法管理子进程、但 bowxt 消息 API 仍可用时，才使用此模式。它不会获得
WebIM 的完整生命周期管理和自动启动能力。

先在 WebIM 停止对应受管实例，确保没有进程使用同一 consumer。然后：

```bash
cd kjfwd-bot
python3 -m venv .venv
.venv/bin/pip install -e ../bowxt
.venv/bin/pip install -e .
cp config.example.json config.json
```

创建本地 `.env`：

```dotenv
API_KEY=your-llm-key
BASE_URL=https://api.example.com/v1
MODEL=your-model
BRAVE_KEY=your-brave-key
BOWXT_BASE_URL=http://127.0.0.1:8787
BOWXT_CONSUMER=kjfwd-prod
```

显式启动 fallback：

```bash
.venv/bin/kjfwd-bot --standalone --config config.json --env .env
```

不带 `--standalone` 的外部启动会被拒绝。恢复受管模式时，先停止该进程，再在 WebIM 启动相同实例；
固定 consumer 可以续接原投递进度。绝不能让独立进程和受管实例同时使用同一 consumer。

## 开发接口

业务代码不得创建第二个微信 UI 客户端。kjfwd-bot 内部保留以下稳定边界：

| 接口 | 用途 |
| --- | --- |
| `kjfwd_bot.transport.MessageEvent` | 含 sender、@、图片和稳定 source_key 的入站事件 |
| `ReplyAction` / `ForwardAction` | 同群回复或跨群输出；`client_id` 保证幂等 |
| `KJFWDHandler.handle()` | 分类、conversation 路由、上下文冻结和排队 |
| `HistoryStore` | 消息、conversation、trigger 去重和快照 |
| `PromptBuilder` | 发送人标注、skills、ambiguous 防误归因和多模态 parts |
| `MessageClassifier` / `Router` | 可替换的分类和动态会话 Protocol |
| `GetHistoryTool` | 限定当前群的 bowxt 持久化历史查询 |
| `MarkdownDocumentLibrary` | kjfwd-bot 自管 Markdown 目录树、路径校验和读取 |
| `ListDocumentsTool` / `ReadDocumentTool` | 模型的文档列表与阅读边界 |
| `build_handler()` / `run()` | 插件运行入口内部的业务组装；不是生命周期控制面 |

通用的新 Agent 应使用 bowxt `AgentClient` 和插件 manifest 接入，不要复制微信 UI 自动化代码。完整
投递协议见 bowxt 的 `AGENT_API.md`。

## 测试

单元测试不会连接微信或真实 LLM：

```bash
PYTHONPATH=../bowxt/src:. python -m unittest discover -s tests -v
PYTHONPATH=../bowxt/src:. python tests/ci_bowxt_simulation_e2e.py
```

GitHub Actions 的必过任务包含 Python 3.10/3.12 单元测试、wheel 安装测试，以及一条真实 consumer
线程驱动的跨项目链路：bowxt 模拟群消息和清晰 PNG 图片进入 kjfwd-bot，发送人和组织进入模型
上下文，随后回复经 bowxt 发送并完成 ACK。模型服务由确定性的本地 OpenAI 兼容端点替代，PR 不消耗
外部额度。`main` 分支推送、手动运行和每周定时任务另用仓库 Secrets 调用当前真实模型，检查服务商
兼容性；外部贡献者的 PR 不会接触这些 Secrets。

真实外部服务测试需要显式启用：

```bash
KJFWD_RUN_LLM_TEST=1 PYTHONPATH=. python -m unittest tests.test_llm_integration -v
KJFWD_RUN_SEARCH_TEST=1 PYTHONPATH=. python -m unittest tests.test_search_integration -v
KJFWD_RUN_AGENT_TEST=1 PYTHONPATH=. python -m unittest tests.test_agent_integration -v
```

真实群验收应使用固定实例 ID 建立基线，再发送新问题；检查该实例日志中的 `message_accepted` 和
`reply_delivered`。不要在生产群临时启用 `replay_existing=true`，否则可能处理历史消息。

## 已知限制

- 微信只渲染可见窗口附近的消息，bowxt 不是完整历史导出器；`get_history` 的“完整”指指定时间窗口内 bowxt 已持久化的全部记录，不包含微信 UI 从未渲染、bowxt 从未观测到的历史；
- 发送人资料卡补全会短暂打开资料卡并用 Esc 安全退出；
- 当前打开的会话可能没有红点，bowxt unread 模式会原地读取当前会话；
- kjfwd-bot 内部 LLM worker 按群串行，不同群可并行；
- 微信 UI 没有对端送达或已读回执。

许可证：AGPL-3.0-or-later。
