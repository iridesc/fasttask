## Context

当前 `files/` 目录结构如下：
```
files/
├── fasttask/          # 系统内部目录（清理时跳过）
│   ├── conf/
│   ├── log/
│   ├── ssl_cert/
│   ├── redis/
│   └── celery/
├── .lazy_action/      # lazy_action 磁盘缓存（当前位于 files/ 根层级）
├── .disk_cache_reset.lock  # lazy_action 锁文件（当前位于 files/ 根层级）
└── <用户文件>          # 业务上传/下载文件
```

`LAZY_ACTION_FILE_PATH` 当前设为 `/fasttask/files/`，导致 lazy_action 的内部文件散落在用户文件目录的根层级。应当移入 `files/fasttask/` 下与其它系统内部目录统一管理。

此外，`cleanup_files.py` 的跳过逻辑硬编码了 `files/fasttask/` 路径，用户无法配置额外的跳过规则。新增 `FILE_CLEANUP_SKIP_PATTERNS` 环境变量，让用户可以灵活指定需要跳过的路径。

## Goals / Non-Goals

**Goals:**
- 将 lazy_action 缓存目录和锁文件移入 `files/fasttask/lazy_action/`，与其它系统内部目录统一管理
- 提供 `FILE_CLEANUP_SKIP_PATTERNS` 环境变量，允许用户配置额外跳过路径
- 不改变现有默认行为，向后兼容

**Non-Goals:**
- 不修改 lazy_action 库本身的缓存逻辑
- 不改变 `cleanup_files.py` 的清理周期和策略（mtime 判断、10 分钟间隔等）
- 不处理 Redis 模式下的 lazy_action（当前项目仅使用 `mode="memory"`）

## Decisions

### 1. `LAZY_ACTION_FILE_PATH` 新默认值

**决定**: 将 `LAZY_ACTION_FILE_PATH` 从 `/fasttask/files/` 改为 `/fasttask/files/fasttask/lazy_action/`，并在 `run.py` 中通过 `init_dir` 自动创建该目录。

**理由**: 
- `files/fasttask/` 已被 `cleanup_files.py` 硬编码跳过，将 lazy_action 文件放在其子目录下自动获得保护
- 与 `conf/`、`log/`、`ssl_cert/` 等系统内部目录并列，结构清晰
- `force_default=True` 保持不变，确保部署一致性

### 2. `FILE_CLEANUP_SKIP_PATTERNS` 设计

**决定**: 新增环境变量 `FILE_CLEANUP_SKIP_PATTERNS`，值为逗号分隔的相对路径列表（相对于 `FILES_DIR`），在清理时跳过。

**格式**:
```
FILE_CLEANUP_SKIP_PATTERNS=".lazy_action,.disk_cache_reset.lock,custom_dir"
```

**匹配逻辑**:
- 每个 pattern 被解析为相对于 `FILES_DIR` 的绝对路径
- 遍历时同时检查：文件/目录的绝对路径是否以任一 pattern 的绝对路径开头
- 原有的 `files/fasttask/` 跳过逻辑保持不变（始终生效，不依赖环境变量）

**备选方案**: 使用 glob 模式匹配。未采用，因为相对路径前缀匹配更简单、性能更好，且足以覆盖"跳过某个目录/文件"的场景。

### 3. `FILE_CLEANUP_SKIP_PATTERNS` 默认值

**决定**: 默认值为空字符串（不额外跳过任何路径）。

**理由**: 向后兼容。现有部署中 `files/fasttask/` 的跳过逻辑已硬编码，无需额外配置。只有需要跳过自定义路径的用户才需要设置此变量。

## Risks / Trade-offs

- **[风险] 用户配置了错误的跳过路径** → 跳过逻辑使用 `os.path.abspath` 规范化路径后再匹配，减少配置错误的影响。pattern 对应的路径不存在时，静默跳过（不报错），避免因拼写错误导致清理进程崩溃
- **[风险] `LAZY_ACTION_FILE_PATH` 变更影响现有持久化缓存** → 当前项目仅使用 `mode="memory"`，无磁盘缓存，变更无影响。若未来切换到 `mode="disk"`，首次部署时会创建新缓存目录，旧缓存自动被清理进程处理
