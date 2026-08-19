# kjfwd-bot

面向微信群电脑软硬件答疑的 Agent。它保留原 `kjfwd` 的分类、动态 conversation、skills、
搜索和回复逻辑，把 Windows/wx4py 消息通道替换为 Linux 下的
[`bowxt`](https://github.com/hzwzwzw/bowxt) Agent API，并增加发送人感知和可配置图片上下文。

`kjfwd-bot` 与微信桌面进程分开运行：bowxt 独占微信 UI，Agent 只通过本机 HTTP 消费持久化
消息并提交发送任务。因此打开、关闭或断开 VNC/noVNC 都不影响 Agent；VNC 只用于登录和人工监看。

## 安全边界

整个链路只操作官方 Linux 微信的可见界面：只读 AT-SPI 控件树，使用 XTest 键鼠和系统剪贴板
完成用户可见的操作。不使用微信协议、直接发包、进程注入、数据库解密、深层控件写入、OCR 或
缓存目录扫描。图片只通过可见图片查看器和 `Ctrl+C` 获取。请只用于自己的账号和已授权的小规模
会话，不要用于群发、营销或绕过微信限制。

## 从空环境到运行

要求：Linux amd64、Docker Engine、Python 3.10+，以及可用手机确认登录的微信账号。

### 1. 启动微信容器

```bash
git clone https://github.com/hzwzwzw/bowxt.git
cd bowxt
cp .env.example .env
```

至少配置当前账号在群里可能被 @ 的昵称；多个名称用逗号分隔：

```dotenv
BOWXT_MY_NAMES=kirotta
BOWXT_SYNC_MODE=unread
BOWXT_UIA_SENDER=1
VNC_SCOPE=window
```

然后构建并启动：

```bash
./scripts/init.sh
```

访问下面两个地址：

- Web IM 与 Agent 日志：<http://127.0.0.1:8787/>
- 微信单窗口 noVNC：<http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale&reconnect=1&reconnect_delay=1000>

第一次在 noVNC 中扫码并用手机确认登录。登录状态保存在 Docker volume `bowxt-home`。noVNC 在
登录窗口销毁、主窗口创建时会自动重连。确认服务和微信状态：

```bash
./manage.sh ready
curl -fsS http://127.0.0.1:8787/api/status
./manage.sh add-chat 博特泰斯特 group
```

默认端口只绑定 `127.0.0.1`。跨主机时使用 SSH 隧道，不要直接暴露 8787/6080：

```bash
ssh -L 8787:127.0.0.1:8787 -L 6080:127.0.0.1:6080 USER@DOCKER_HOST
```

### 2. 安装 Agent

在与 `bowxt` 同一父目录下：

```bash
git clone https://github.com/hzwzwzw/kjfwd-bot.git
cd kjfwd-bot
python3 -m venv .venv
.venv/bin/pip install -e ../bowxt
.venv/bin/pip install -e .
cp config.example.json config.json
```

创建不会提交到 Git 的 `.env`：

```dotenv
API_KEY=your-llm-key
BASE_URL=https://api.example.com/v1
MODEL=your-model
BRAVE_KEY=your-brave-key
```

如果关闭 `search.enabled`，无需 `BRAVE_KEY`。Kimi K2.6 国内 API 的配置示例为：

```json
{
  "llm": {
    "api_key_env": "API_KEY",
    "base_url": "https://api.moonshot.cn/v1",
    "model": "kimi-k2.6",
    "temperature": 0.6,
    "thinking_enabled": false
  }
}
```

K2.6 关闭思考时温度必须为 `0.6`。若不希望绑定具体厂商，可继续使用
`base_url_env` 和 `model_env`。

### 3. 配置监听群并启动

最小的同群动态答疑配置：

```json
{
  "groups": [
    {
      "name": "博特泰斯特",
      "bot_nickname": "kirotta",
      "listen_mode": "question_only",
      "reply_groups": ["博特泰斯特"]
    }
  ],
  "bowxt": {
    "base_url": "http://127.0.0.1:8787",
    "consumer": "kjfwd-bot",
    "require_sender": false,
    "replay_existing": false
  },
  "image_context": {
    "enabled": true,
    "max_images": 4,
    "max_image_bytes": 10485760
  },
  "search": {"enabled": false}
}
```

完整字段见 `config.example.json`。启动：

```bash
.venv/bin/kjfwd-bot --config config.json --env .env
```

Agent 日志会同时出现在终端和 bowxt Web IM 的“Agent 日志”页。停止 Agent 不会停止微信容器；
停止或重启 noVNC 也不会停止 Agent。

## 配置语义

### 消息触发

- `mention_only`：仅回复 `StoredMessage.is_at_me=true` 的消息。
- `all_messages`：每条非空入站消息都进入回答链路。
- `question_only`：LLM 结合群名、当前发送人和最近群聊判断是否像客户求助；真人队员的指导、
  闲聊和无明确求助默认不回复。
- `reply_groups`：默认回复来源群；也可把结果转发到一个或多个参考群。

`bot_nickname` 用于从正文中清理机器人的 @ 文本；是否真的 @ 当前账号以 bowxt 的
`is_at_me` 为准，不再读取 wx4py 原始控件。

### bowxt 投递

- `consumer` 是持久化消费身份。保持名称不变即可在 Agent 重启后续接进度；改名会建立一套新进度。
- `replay_existing=false` 时，新 consumer 首次启动把现有消息作为基线，只处理之后的新消息，避免误回历史。
- 一次 claim 最多领取 `batch_size` 条，同一周期同时到达的消息会全部进入处理链路。
- bowxt 通过 lease + ACK/NACK 提供至少一次投递；Agent 内部再以 `bowxt:<seq>` 和固定
  `client_id` 去重，重试不会重复提交同一回复。
- 回复只有在 bowxt 离开 `pending` 后才记为已发送；`sent` 表示微信 UI 找到回显，
  `unverified` 表示执行了可见发送但没有可靠回显，不等于对端已读。
- `require_sender=true` 会暂缓昵称尚未补齐的群消息。资料卡读取失败时可能长期等待，通常建议保持 false，
  让未知发送人以保守逻辑继续处理。

### 图片上下文

`image_context.enabled=false` 时，图片只以 `[图片]` 和元数据进入文本上下文，兼容纯文本模型。
启用后，Agent 从 bowxt 的受控图片端点下载已通过可见查看器获取的 PNG，校验 SHA-256，保存到
`cache_path`，再按 OpenAI 兼容格式把 `image_url` data URL 和文本一起传给模型。

`max_images` 限制一次请求携带的最近图片数；`max_image_bytes` 同时限制下载和模型输入。图片尚在
bowxt 抓取队列时会 NACK 延迟重试，连续失败后降级为 `[图片]`，不会无限阻塞整个 Agent。

`trigger_images=false`（默认）表示图片本身只进入上下文，等待后续文字问题触发；设置为 true 后，
若 `lookback_seconds` 内恰好只有一个可续接的活跃 conversation，单独到达的图片也会作为该会话的
下一轮触发多模态回答。存在多个候选时进入 ambiguous，不会武断归到某个人的话题。

### 发送人感知的动态 conversation

相比旧版，消息表新增 `sender/message_type/image_*` 字段，prompt 中每条群消息显示真实昵称；
发送人缺失时明确标为“身份未知”。发送人被用于三层判断：

1. `question_only` 分类器获得当前发送人和最近 8 条带发送人的群聊，减少把队员指导误判为客户求助。
2. 普通 conversation 路由把当前发送人和候选会话的逐条发送人一并交给路由模型。
3. “还是不行/下一步”等低信息追问，优先续接同一发送人最近的 conversation；如果只有其他人的
   活跃会话，则进入 ambiguous，不会直接串到别人名下。

发送人只是承接证据，不是客户/队员身份标签；程序不会根据昵称猜测角色。

### 历史、指令与 skills

- 消息和 conversation 保存在 `history.database_path` 指向的 SQLite。
- `/new [问题]`、`/clear [问题]` 开启新上下文；`/help` 返回说明；`/search 问题` 强制搜索。
- 向 `skills/` 增加 UTF-8 Markdown 即可添加知识 skill；`/skill名 问题` 显式选择。
- `reply_debounce.delay_seconds` 可合并同一 conversation 短时间内的连续触发。
- `message_reminder` 可把长时间无人接续的问题提醒到指定群。

## 开发接口

业务代码不应创建第二个微信 UI 客户端。推荐直接使用 bowxt 的稳定进程外接口：

```python
from bowxt import AgentClient, ChatType

client = AgentClient("my-agent", base_url="http://127.0.0.1:8787")
group = client.ensure_chat("答疑群", ChatType.GROUP)

for delivery in client.claim(chat_ids=[group.id], limit=8, timeout=20):
    message = delivery.message
    try:
        result = your_agent(message.content, sender=message.sender)
        queued = client.reply_text(delivery, result)
        client.wait_delivery(queued, timeout=45)
    except Exception as exc:
        client.nack(delivery, exc, retry_delay=5)
    else:
        client.ack(delivery)
```

本仓库为扩展 Agent 保留以下边界：

| 接口 | 用途 |
| --- | --- |
| `kjfwd_bot.transport.MessageEvent` | 与微信实现无关的入站事件，含 sender、@、图片和稳定 source_key |
| `ReplyAction` / `ForwardAction` | 同群回复或跨群输出；`client_id` 保证幂等 |
| `KJFWDHandler.handle()` | 分类、conversation 路由、上下文冻结和排队 |
| `HistoryStore` | 持久消息、conversation、trigger 去重和快照 |
| `PromptBuilder` | 发送人标注、skills、ambiguous 防误归因和多模态 parts |
| `MessageClassifier` / `Router` | 可替换的分类与动态会话 Protocol |
| `build_handler()` / `run()` | 组装默认实现或运行完整服务 |

自定义分类器应实现：

```python
def should_reply(*, group_name, content, sender=None, recent_messages=()) -> bool: ...
```

自定义路由器应实现：

```python
def route(*, group_name, request, candidates, recent_messages, sender=None): ...
```

旧的两参数分类器和不接收 `sender` 的路由器仍可由 handler 兼容调用，便于渐进迁移。

## 测试

单元测试不会连接微信或真实 LLM：

```bash
PYTHONPATH=../bowxt/src:. python -m unittest discover -s tests -v
```

真实 LLM、搜索和完整 Agent 测试需要显式打开环境变量：

```bash
KJFWD_RUN_LLM_TEST=1 PYTHONPATH=. python -m unittest tests.test_llm_integration -v
KJFWD_RUN_SEARCH_TEST=1 PYTHONPATH=. python -m unittest tests.test_search_integration -v
KJFWD_RUN_AGENT_TEST=1 PYTHONPATH=. python -m unittest tests.test_agent_integration -v
```

真实微信群验收应先用固定 consumer 建立基线，再发送一条新的问题消息；检查 Web IM Agent 日志中的
`message_accepted`、`reply_delivered`，以及微信中同群回复。不要把 `replay_existing` 临时改为 true
来测试生产群，否则 Agent 可能处理历史消息。

## 已知限制

- 微信只渲染可见窗口附近的消息，bowxt 不是完整历史导出器。
- 发送人资料卡补全是可见操作，会短暂打开资料卡并用 Esc 安全退出；可在 bowxt 关闭该功能。
- 微信可能不给当前打开的会话显示红点；bowxt `unread` 模式仍会原地读取当前会话，但不会轮询跳转。
- `kjfwd-bot` 的内部 LLM worker 按群串行，避免同一群回复乱序；不同群可并行。
- 微信 UI 没有对端送达或已读回执。

许可证：AGPL-3.0-or-later。
