## Why

当前 `LAZY_ACTION_FILE_PATH` 设置为 `/fasttask/files/`，这意味着 lazy_action 的磁盘缓存目录 `.lazy_action/` 和锁文件 `.disk_cache_reset.lock` 会直接出现在用户文件目录的根层级，与用户上传/下载的业务文件混在一起。此外，`cleanup_files.py` 的文件清理逻辑硬编码了跳过 `files/fasttask/` 的规则，但用户无法配置额外的跳过路径，缺乏灵活性。

## What Changes

- 将 `LAZY_ACTION_FILE_PATH` 从 `/fasttask/files/` 改为 `/fasttask/files/fasttask/lazy_action/`，使 lazy_action 的缓存和锁文件归入系统内部目录
- 在 `run.py` 中新增 `LAZY_ACTION_FILE_PATH` 对应目录的自动创建（`init_dir`）
- 新增环境变量 `FILE_CLEANUP_SKIP_PATTERNS`，支持用户配置清理时需要额外跳过的文件/目录模式（相对于 `FILES_DIR` 的路径，逗号分隔）
- `cleanup_files.py` 读取 `FILE_CLEANUP_SKIP_PATTERNS` 并在遍历时跳过匹配的路径

## Capabilities

### New Capabilities

- `cleanup-skip-patterns`: 文件清理跳过规则的可配置化，用户通过环境变量指定需要跳过的文件/目录

### Modified Capabilities

- `file-expiration`: 新增 `FILE_CLEANUP_SKIP_PATTERNS` 环境变量支持，扩展清理跳过逻辑

## Impact

- [run.py:193](fasttask/run.py#L193): `LAZY_ACTION_FILE_PATH` 默认值变更，新增 `init_dir` 回调
- [cleanup_files.py](fasttask/cleanup_files.py): 新增 `FILE_CLEANUP_SKIP_PATTERNS` 读取与跳过逻辑
- 对现有用户无破坏性影响：`LAZY_ACTION_FILE_PATH` 设为 `force_default=True`，用户自定义值会被覆盖（与现有行为一致）
