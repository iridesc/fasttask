# 功能组 3：给存量项目补齐 MCP 元信息

> fasttask-helper 技能「补齐/优化存量项目的 MCP 元信息」功能组的操作手册。
> 用途：对**已经跑起来、但没有描述**的项目（常见于早期封装的工具），补齐两类描述性元信息——
> 任务模块 docstring 与 `setting.py` 的四个字段——让 MCP 工具能对 AI 说清楚
> 「这是什么服务 / 有哪些任务 / 每个任务干什么 / 参数怎么填」。
> 触发场景：用户说「给 XX 项目补任务注释 / 文档」「接入 MCP 后 AI 不知道这是干什么的」
> 「AI 老是选错工具、参数填错」「把旧项目的 setting.py 补一下」「给任务加 docstring」
> 「这个服务 AI 调用效果不好」。
>
> 本功能**只补描述，不动业务逻辑**。要新建项目请走功能组 1。

---

## 1. 先搞清楚：AI 到底能看到什么

MCP 里只有两个放描述的位置，FastTask 对应地拆成两层：

| 位置 | 内容 | 来源 |
|---|---|---|
| `instructions`（整个服务一份） | ① 模块身份：title / summary / description / version<br>② 平台固定文本（怎么调用、结果形态、怎么下载大结果）<br>③ 任务一览：每个任务一行摘要 | **`setting.py`** + 框架 + 每个任务 docstring 的**首段** |
| `tools[].description`（每个工具一份） | 该任务的业务说明；`create_*` / `check_*` / `run_*` 同属一个任务，**只有「主工具」（默认 `create_`）给全文**，其余给一行摘要 + 指引 | 任务模块 **docstring 全文** |

所以一个没有 docstring 的任务，AI 看到的就只有框架话术。实测对照（同一个平台、两个项目）：

```text
# 没补过的项目（平台自带示例任务）
--- create_get_circle_area
创建 get_circle_area 异步任务（默认入口）。

# 补过的项目（ez_fingerprint）
--- create_scan_fingerprint
创建 scan_fingerprint 异步任务（默认入口）。
任务说明：批量 Web 指纹采集。

对传入的一组 URL 逐个访问，识别目标的 Web 指纹并汇总返回：
- 产品信息：产品名（productName）、厂商（company）、产品分类（productCategory）
- HTTP 状态码（status_code）与页面标题（title）
- 服务 banner

典型用途：拿到一份资产清单后，批量识别这些站点跑的是什么系统、什么组件，
用于资产梳理与风险定位。

注意：
- 单个 URL 在重试后仍为空流时返回空结果，不影响同组其他 URL
- 确定性的错误（如 URL 格式非法）会直接失败并返回 traceback
- 返回结构：fingerprint_result 为数组，每项对应一个目标（...）

--- check_scan_fingerprint
查询 scan_fingerprint 任务的状态与结果。
该任务：批量 Web 指纹采集。（完整说明见 create_scan_fingerprint）
```

三种典型后果，决定了补写的重点：

1. **选错工具/不会用**——没有业务说明，AI 只能靠任务名猜。要补：这个任务做什么、什么时候该用。
2. **不会填参数**——`Params` 字段没有说明，AI 只能靠字段名猜（`timeout` 是秒还是毫秒？`ips` 要不要带端口？）。要补：单位、格式、取值范围、约束。
3. **规则被挤出可见窗口**——客户端普遍只渲染 `instructions` 的前几百字符（pi-mcp-adapter 是 300）。`project_summary` 写成一整段话，就把「默认走 create + check」这类**决定行为的规则**挤出窗口，AI 于是乱调。要改：summary 压成一句话（框架在超过 100 字符时会启动告警）。

## 2. 硬约束（先看这条，避免帮倒忙）

1. **只改描述，不改契约。** 允许动：模块 docstring、`Field(description=...)`、`setting.py` 四个字段。
   不允许动：函数签名、`Params`/`Result` 的字段名/类型/默认值、业务逻辑。
   理由：`Params` 直接生成 HTTP 接口与 MCP 工具的入参 schema，上游系统可能已经按现有契约在调，
   改它就是破坏性变更——而这次任务的目标只是让 AI 看懂，不是改接口。
2. **不编造业务语义。** 描述必须能从这个项目的代码、依赖、产物里推出来。推不出来就问用户，
   或先留 TODO。宁可少写一句，也不要写错一句——错描述比没描述更糟，AI 会照着错的去调。
3. **docstring 会被推给所有连上来的 AI 客户端。** 不要写真实客户名、内网地址、凭据样例、
   业务敏感数据。需要举例时用占位符（`example.com`、`192.0.2.10`、`<token>`）。
4. **保持项目原有风格。** 语言（中/英）、术语、`⚡标题⚡` 这类 emoji 惯例、版本号格式都照旧。
5. **先干一个任务给用户看。** 业务语义的判断有主观性，先写 1 个任务 + `setting.py` 让用户确认风格，
   再批量铺开，避免整批返工。
6. **改完必须重启服务。** docstring 与 `setting.py` 都是服务启动时读取的，不重启 AI 看到的还是旧的。

## 3. 阶段 0：读懂这个项目在干什么

补描述的前提是**真的搞懂业务**。按这个顺序读，每步都能拿到确定的信息：

| 读什么 | 能得到什么 |
|---|---|
| `tasks/*.py` **函数体**（不是 docstring，可能压根没有） | 任务真实做什么：调什么外部工具、拼什么命令、怎么组织结果 |
| `tasks/packages/*` | 共享逻辑、外部 CLI 封装、数据源与字典 |
| `Dockerfile` | 装了哪些外部二进制、工具目录（`/tool-bin`、`/ez-bin`…）→ 决定能力边界 |
| `compose*.y*ml` | 上游怎么用它：`ENABLED_TASKS`、`API_*`、`RESULT_TYPE`、`PUBLIC_ENDPOINT` |
| `AGENTS.md` / `README` / `git log --oneline -20` | 业务背景、历史改动、踩过的坑（往往是描述的最好素材） |
| 可选：`files/` 下的历史产物、上层编排系统 | 真实输入输出长什么样、什么量级（决定要不要提醒"结果很大"） |

产出：先口头跟用户对齐一段「这个服务是什么 + 每个任务干什么 + 谁在调它」，
确认无误后再动笔——这一步花 10 分钟，能省掉整批返工。

## 4. 阶段 1：盘点现状（跑审计脚本）

```bash
python3 <skill>/scripts/audit_mcp_metadata.py <项目目录>          # 人读报告
python3 <skill>/scripts/audit_mcp_metadata.py <项目目录> --json    # 机器读
```

脚本用 AST 静态解析，**不 import 项目代码**（没有副作用、不依赖项目环境、不挑 Python 版本），
输出三件事：`setting.py` 缺哪些字段、每个任务有没有 docstring、`Params`/`Result` 哪些字段缺描述。
退出码 1 表示还有待办，可以当门禁用。

```text
== setting.py（→ MCP instructions 的模块身份） ==
  ✓ project_title: ⚡CDN Checker⚡
  ✗ project_summary: 缺失或为空
  ✗ project_description: 缺失或为空
  ✓ project_version: 0.1.0

== tasks/（→ MCP tools[].description） ==
  ✗ find_cdn_domains: 缺 docstring
      字段描述：2 个，缺 Params.domains, Result.cdn_domain_infos
  ✓ scan_something: 批量扫描…。  [380 字符]
      字段描述：3 个，全部有描述

== 待办（N） ==
  ...
```

**先把这个清单给用户过一遍**（尤其是"哪些任务缺 docstring"这种要动业务语义的部分），
再进入补写。补写过程中可以反复跑它销账，最后要跑到「待办：无」。

## 5. 阶段 2：写任务 docstring

模板（首段 + 空行 + 详细说明，注意两段会被用到不同地方）：

```python
"""<一句话：这个任务做什么、给谁用>。

<详细说明，可以分几块：>
- 做什么：输入什么 → 产出什么，处理流程的关键步骤
- 典型用途：谁在什么场景下调它、上下游是什么
- 边界行为：失败/降级怎么表现、哪些情况返回空结果、耗时量级
- 返回结构：关键字段的含义（结构复杂时值得写，AI 不用拿到结果再猜）
"""
```

写法要求：

- **首段 = 任务一览的一行。** 遇到空行就截断，所以首段别换行、别罗列功能，建议 ≤ 120 字符。
  它和一行的空间要能让 AI 快速区分「这几个任务该用哪个」。
- **详细说明回答「什么时候用 / 什么时候别用」。** 这是 AI 真正需要的信息：多个任务像兄弟时，
  它是靠边界描述的差异来选的。
- **返回结构复杂就写。** 比如「`fingerprint_result` 为数组，每项对应一个目标（target / status_code / ...）」，
  能省掉 AI 拿到结果再摸索的一轮。
- **长度：** 300~800 字符是好区间（ez_fingerprint 那份 380 字符，够用）。低于 100 字符通常说明信息不足，
  超过 2000 字符会挤占上下文。
- **别复述函数名。** 「get_circle_area：获取圆的面积」这类复述等于没写；写「输入半径，返回面积（πr²，
  保留 6 位小数）」才有信息量。

## 6. 阶段 3：补 `Params` / `Result` 的字段描述

只写**AI 无法从字段名猜到**的信息：

```python
class Params(BaseModel):
    urls: list[str] = Field(description="待探测的完整 URL 列表，含协议（http:// 或 https://）")
    timeout: int = Field(default=10, description="单目标超时（秒），默认 10")
    record_types: list[str] = Field(default=["A"], description="DNS 记录类型，可选 A / AAAA / CNAME / MX / TXT")
```

- 值得写：单位（秒/毫秒/字节）、格式（要不要带协议/端口）、可选值枚举、取值范围、默认值的语义、
  元素个数限制、与其它参数的联动。
- 不必写：把字段名翻译一遍（`description="urls 列表"`）、复述类型与必填性（schema 已经在表达这些）。
- **不要顺手改 `default` 或类型**：那会改接口行为。只加 `description`。

## 7. 阶段 4：改 `setting.py`

```python
# fasttask setting
project_title = "⚡项目名⚡"            # 保留项目原有的 emoji 惯例
project_summary = "一句话定位：做什么、给谁用"   # ≤ 100 字符，会被启动告警检查
project_description = (
    "详细说明：能力范围、典型场景、边界（不做什么）、任务清单（任务名 + 一句话）。"
)
project_version = "0.1.0"              # 与原来保持一致，除非用户要求 bump
```

- **`project_summary` 是最重要的一行**：它出现在 `instructions` 最前面，是 AI 打开这个服务看到的
  第一句话。写成「一句话定位」而不是功能罗列。超过 100 字符启动会告警，因为会挤掉调用规则。
- **`project_description` 里值得列任务清单**（每个任务的 `任务名 — 一句话`）。它排在调用规则之后，
  是 AI 了解「这个服务一共有哪些能力」的兜底入口。
- 四个字段都为空时 `instructions` 里就没有模块身份，AI 只能从工具名反推服务是干什么的——
  这就是本功能组要解决的核心问题。

## 8. 阶段 5：验收（看 AI 真正会看到什么）

`<skill>/scripts/preview_mcp_descriptions.py` 会**用线上同一套代码**把 `instructions` 与每个工具的
描述原样打印出来（含 `instructions` 前 300 字符的可见窗口），并对「缺 docstring」「规则被挤出窗口」
「一个工具都没注册」给出提醒。它还会自动从运行中的 uvicorn 进程读真实环境变量——
`podman exec` 拿到的只是 compose 里那几个变量，`API_RUN` / `ENABLED_TASKS` / `RESULT_TYPE` 这些
由 `run.py` 启动时补齐的值只有进程里才有。

**A. 服务已经在跑**（最准，读真实环境）

```bash
podman cp <skill>/scripts/preview_mcp_descriptions.py <容器>:/tmp/
podman exec -w /fasttask <容器> python /tmp/preview_mcp_descriptions.py
podman exec -w /fasttask <容器> python /tmp/preview_mcp_descriptions.py --json   # 给脚本消费
```

**B. 改完还没部署**（用项目自己的镜像挂载代码预览，不用重建镜像）

```bash
cd <项目目录>
podman run --rm \
  -v "$PWD":/project:ro \
  -v <skill>/scripts:/scripts:ro \
  <项目镜像> \
  python /scripts/preview_mcp_descriptions.py --project-dir /project --platform-dir /fasttask
```

> 用项目**自己的镜像**（`podman images | grep <项目名>`），别用基础镜像——项目特有的 pip 依赖
> （grpc、chromium 绑定等）只在项目镜像里。找不到可用镜像时，退回 A 或先构建。

退出码：`0` = 描述齐全；`1` = 有提醒；`2` = 跑不起来（会打印原因）。有提醒时逐条销账，
最后再跑一次直到 0。

补充验收手段：

- **真实客户端**：`claude mcp add --transport http <name> https://<host>:<port>/mcp --header "Authorization: Basic $(echo -n 'user:passwd' | base64)"`，
  然后让模型 `describe` 一下工具，看它理解得对不对（这是最终验收标准）。
- **Swagger**：`/docs` 里的 title/summary/description 同样来自 `setting.py`，顺手能看。
- **审计脚本复跑**：`audit_mcp_metadata.py` 待办归零。

## 9. 阶段 6：提交

- 改动只应有：任务文件的 docstring / `Field(description=...)`、`setting.py`、必要时 `AGENTS.md`。
- commit 信息写明「仅补描述，无行为变更」，方便 review 时快速跳过逻辑审查。
- 一个项目一个 commit；批量做多个项目时不要混在一个提交里。

## 10. 常见坑

| 现象 | 原因 |
|---|---|
| 改完 AI 看到的没变 | 服务没重启；docstring 与 `setting.py` 是启动时读的 |
| 工具的说明只有框架话术 | 任务模块 docstring 写在**函数**上而不是**模块**顶部（框架读的是模块 docstring） |
| `check_*` / `run_*` 只有一行摘要 | 正常：全文只在主工具（默认 `create_*`）上，避免同一份说明重复三遍占上下文 |
| AI 还是乱调 create/run | `project_summary`/`description` 太长，把调用规则挤出了 `instructions` 前 300 字符——用 preview 脚本看窗口内容 |
| 任务一览里读不出区别 | 首段太长或写成功能罗列；首段应该是一句「做什么」 |
| 补完描述后接口出问题 | 大概率顺手改了 `default` / 字段类型 / 字段名——回滚这些改动，只保留 `description` |
| 预览脚本报 `No module named ...` | 用的不是项目自己的镜像（缺项目依赖），或没传 `--project-dir` / `--platform-dir` |
