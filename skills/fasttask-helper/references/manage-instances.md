# 功能组 2：管理存在的实例任务

> fasttask-helper 技能「管理存在的实例任务」功能组的操作手册。
> 用途：对**已经部署运行**的 FastTask 实例做日常管理——查看实例/任务/worker 状态、查询任务结果、
> 撤销（revoke）错误下发或卡住的任务、批量终止一组任务。
> 触发场景：用户给出某实例连接信息（HOST/PORT/USER/PASSWD，通常以环境变量形式如 `*_HOST/*_PORT/
> *_USER/*_PASSWD`），要求"撤销这批任务"、"把这几个 running 任务终止掉"、"看下现在有什么任务在跑"、
> "任务状态/结果查一下"、"任务卡住了/太久了想停掉"、从上层系统拿了一批 result_id 要求终止。

---

## 1. 通用模型：先理解一个 FastTask 实例

每个运行的 FastTask 服务（一个部署实例，对应一组 HOST/PORT/USER/PASSWD 连接信息）是一个**实例**：

- 实例启动时在 Redis 中生成/持久化一个 **RUNNING_ID**（8 位随机串，key `fasttask:current_running_id`），
  例如 `AbCd1234`。
- 该实例下每个任务的 **result_id = `{RUNNING_ID}-{uuid4}`**，例如 `AbCd1234-1a2b3c4d-5e6f-4a8b-9c0d-1e2f3a4b5c6d`。
- **前缀校验**：`/check` 与 `/revoke` 都会校验传入的 `result_id` 必须以**当前实例**的 RUNNING_ID 开头，
  否则直接拒绝（返回 `invalid {result_id=} current {RUNNING_ID=}`）。
- 含义：上层系统（任务编排/调度平台，或父任务的子任务清单）里记录/返回的 `result_id`，就是下发到
  某个实例上的任务 ID；**要 revoke 它，必须打到它所属的那个实例上**，且该实例尚未更换 RUNNING_ID
  （重启后 Redis 持久化则不变，Redis 被清则旧任务全部无法管理）。

### 实例信息从哪来？

一般由用户直接给出环境变量或字段，例如：

```bash
FTT_HOST=<实例地址>
FTT_PORT=<实例端口>
FTT_USER=<用户名>
FTT_PASSWD=<用户提供的密码>
```

变量前缀不固定（随部署方命名习惯），本文统一用 `FTT_*` 占位。如果用户没说，先看是否在
`.env`/项目 AGENTS.md/默认配置里；都没有就问用户，不要到处猜。

### 连接约定

- 协议：**HTTPS**（自签证书），客户端需跳过校验（curl 加 `-k`，python 用 `verify=False`）。
- 认证：**HTTP Basic Auth**，用户名/密码即上面的 USER/PASSWD。
- API 结构见 `/openapi.json`（`https://HOST:PORT/openapi.json`，带 Basic Auth 可拉取完整 schema）。

典型端点（具体以 openapi 为准）：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/create/{task_name}` | POST | 创建任务（异步下发），返回 `{id, state, result}` |
| `/check/{task_name}` | GET | 查询任务状态/结果，参数 `result_id` |
| `/revoke` | POST | 撤销任务，body `{"result_id": "..."}` |
| `/status_info` | POST | 实例运行状态：RUNNING_ID、worker、任务统计、排队数 |
| `/openapi.json` | GET | 接口文档（含该实例加载了哪些 task_name） |

---

## 2. 看实例上有哪些任务在跑：/status_info

```bash
curl -sk -u $FTT_USER:$FTT_PASSWD -X POST https://$FTT_HOST:$FTT_PORT/status_info \
  -H "Content-Type: application/json" -d '{}'
```

返回示例：

```json
{
  "running_id": "AbCd1234",
  "username": "<user>",
  "worker_status": {},
  "task_info_total": {},
  "pending_task_count": {},
  "task_info_task_a": {},
  "task_info_task_b": {}
}
```

字段含义：
- `running_id`：当前实例的 RUNNING_ID —— **先确认它和要管理的 result_id 前缀一致**，不一致则 revoke 必失败。
- `task_info_{task_name}` / `task_info_total`：按任务名统计的状态分布（哪些任务在跑/等待/失败），
  其中 `task_a`/`task_b` 即该实例已加载的任务名。
- `pending_task_count`：各任务队列中待消费数量。
- `worker_status`：celery worker 存活情况（体量小/无 worker 时常为空对象）。

POST body 可选 `{"fields": ["worker_status", "task_info", "pending_task_count"]}` 控制返回哪些字段。

---

## 3. 查询单个任务结果：/check

```bash
curl -sk -u $FTT_USER:$FTT_PASSWD \
  "https://$FTT_HOST:$FTT_PORT/check/{task_name}?result_id={result_id}"
```

`task_name` 必须是该实例已加载的任务名（先看 openapi 或 status_info 里有哪些 `task_info_*` 前缀）。

返回 `{id, state, result_type, result}`：
- `state` ∈ `PENDING` / `STARTED` / `SUCCESS` / `FAILURE` / `REVOKED` / `RETRY`
- `SUCCESS` + `result_type=json`：`result` 为任务结构化输出
- `SUCCESS` + `result_type=s3`：结果已外置到对象存储，`result` 是引用
  （`{uri, url, size_bytes, sha256, expires_at}`）。**不要把 result 当成结果用**，
  用 `result.url` 下载后再解析（它是**相对路径**，拼上服务地址即可，无需额外凭据）；
  地址过期就重新 check 一次
- 下载外置结果的示例（**只带预签名地址，不要加 Basic 凭据，会破坏签名**）：
  `curl -sk "https://$FTT_HOST:$FTT_PORT<result.url>" -o result.json && jq . result.json`
- `FAILURE` + `result_type=text`：`result` 含 `result=... traceback=...`，可据此定位失败原因
- 若返回 `{result_id=...} not exist, current {RUNNING_ID=}` → 该 ID 不属于当前实例（前缀错 / 实例已换 Redis）

---

## 4. 撤销任务：/revoke（核心操作）

```bash
curl -sk -u $FTT_USER:$FTT_PASSWD -X POST https://$FTT_HOST:$FTT_PORT/revoke \
  -H "Content-Type: application/json" -d '{"result_id": "AbCd1234-1a2b3c4d-5e6f-4a8b-9c0d-1e2f3a4b5c6d"}'
```

- body 模型：`ResultIDParams`，唯一字段 `result_id`（必需）。
- 服务端逻辑：先校验 `result_id.startswith(当前 RUNNING_ID)`，随后对 celery `AsyncResult` 执行
  `revoke(terminate=True)`（强杀正在跑的进程）。
- 返回 `ActionResp {status, result, message}`。`status` 只在"未知状态"时是 `FAILURE`，其余撤销均受理成功。

**message 对照表**（判断撤销是否立刻生效）：

| 返回 message | 任务当时状态 | 含义 |
|---|---|---|
| `task ended or revoked already` | SUCCESS/FAILURE/REVOKED | 本来就已结束或被撤，无需处理 |
| `task is still pending, will revoked later` | PENDING | 还在排队未开始，撤销已登记、稍后生效 |
| `task started, revoking now` | STARTED | 正在执行，正在 terminate 强杀 |
| `task retrying, revoking now` | RETRY | 重试中，正在终止 |
| `invalid {result_id=} current {RUNNING_ID=}` | — | 前缀不属于当前实例，**撤销失败**（status=FAILURE），检查打错实例/实例换过 Redis |

> **"撤销成功" ≠ "立刻 REVOKED"**：PENDING 状态撤销是异步的（worker 消费到撤销信号后才标记）。
> 想确认最终生效，revoke 后稍等（如 5 秒）再 `/check`，看到 `state=REVOKED` 才算真正终止。

---

## 5. 实战案例：批量撤销一批 running 子任务

> 来源：某上层系统（任务编排/调度平台）对一批目标逐个下发子任务到某实例，这些子任务全部一直
> `running`（msg="运行中..."），需要整批终止。

**背景数据结构**：上层系统返回的子任务清单中，每个子任务形如（字段名以实际系统为准）：

```json
{
  "urls": ["https://example-host:443"],
  "state": "running",
  "msg": "运行中...",
  "tries": 0,
  "result_id": "AbCd1234-1a2b3c4d-5e6f-4a8b-9c0d-1e2f3a4b5c6d"
}
```

**操作步骤**：

1. 从子任务清单中提取全部 `result_id`（注意前缀都是同一 RUNNING_ID）。
2. 用 status_info 确认目标实例的 `running_id` 与这些 result_id 前缀一致。
3. 逐个调用 `/revoke`（可并发，效率更高），记录每个的返回 `status` 与 `message`。
4. 等待几秒，抽样 `/check` 复核是否已变为 `REVOKED`。

**批量撤销脚本模板**（python，可直接套用）：

```python
import requests, urllib3, json
urllib3.disable_warnings()

BASE = f"https://{FTT_HOST}:{FTT_PORT}"
AUTH = (FTT_USER, FTT_PASSWD)

result_ids = [
    "AbCd1234-1a2b3c4d-5e6f-4a8b-9c0d-1e2f3a4b5c6d",
    # ... 从子任务清单提取的完整列表
]

# 1. 逐个撤销
for rid in result_ids:
    r = requests.post(f"{BASE}/revoke", json={"result_id": rid},
                      auth=AUTH, verify=False, timeout=20)
    body = r.json()
    print(f"{rid[:32]}... HTTP {r.status_code} status={body.get('status')} msg={body.get('message')}")

# 2. 等几秒后抽样复核（task_name 用该实例已加载的任务名，如 status_info 返回的 task_info_* 前缀）
import time; time.sleep(5)
for rid in result_ids[:5]:
    r = requests.get(f"{BASE}/check/{TASK_NAME}", params={"result_id": rid},
                     auth=AUTH, verify=False, timeout=20)
    print(f"{rid[:32]}... state={r.json().get('state')}")
```

**实测结果（一次真实批量撤销）**：15/15 revoke 返回 `SUCCESS`（message 均为
`task is still pending, will revoked later`，即任务尚在排队）；等待后 check 复核全部
`state=REVOKED, result=revoked`。

---

## 6. 注意事项与排查

- **打错实例**：revoke/check 返回 `invalid ... current RUNNING_ID=` → result_id 前缀与当前实例
  running_id 不一致。核对 HOST/PORT 是否指对了任务实际运行的实例。
- **PENDING 撤销不立即 REVOKED**：正常，等 worker 消费撤销信号；可稍后再 check。
- **撤销已结束任务**：返回 `task ended or revoked already`，status=SUCCESS，无副作用。
- **想查某任务失败原因**：用 `/check` 看 `FAILURE` 的 `result`（含 traceback），别只盯着 state。
- **上层联动**：撤销实例子任务后，若上层系统（编排/调度平台）仍在等回调，子任务在上层侧会一直
  `running`；如需整链停掉，要在上层系统侧一并处理。
- **curl 自签证书**：必须 `-k`；python requests 必须 `verify=False`，否则 TLS 报错。
