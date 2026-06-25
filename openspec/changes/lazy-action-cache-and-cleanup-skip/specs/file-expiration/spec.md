# file-expiration

## ADDED Requirements

### Requirement: 用户可配置文件清理跳过路径

系统 SHALL 支持通过 `FILE_CLEANUP_SKIP_PATTERNS` 环境变量配置清理时需要额外跳过的路径，该变量为逗号分隔的相对路径列表（相对于 `FILES_DIR`）。

`cleanup_files.py` 清理进程 SHALL 读取该环境变量，将每个 pattern 解析为绝对路径后在遍历时跳过匹配项。

`FILE_CLEANUP_SKIP_PATTERNS` 未设置或为空时 SHALL NOT 影响现有跳过逻辑（`files/fasttask/` 始终被跳过）。

`FILE_CLEANUP_SKIP_PATTERNS` 中配置的路径如果不存在，SHALL NOT 报错。

#### Scenario: 配置额外跳过路径后清理

- **WHEN** `FILE_CLEANUP_SKIP_PATTERNS=".lazy_action,.disk_cache_reset.lock"`，`FILE_EXPIRATION_SECONDS=3600`
- **THEN** 清理时跳过 `files/.lazy_action/` 目录及其内容和 `files/.disk_cache_reset.lock` 文件，同时对其它过期文件正常清理

#### Scenario: 仅依赖默认跳过逻辑

- **WHEN** `FILE_CLEANUP_SKIP_PATTERNS` 未设置
- **THEN** 清理行为与之前完全一致：仅跳过 `files/fasttask/` 目录

### Requirement: lazy_action 缓存目录归入系统内部目录

系统 SHALL 将 `LAZY_ACTION_FILE_PATH` 环境变量的默认值设为 `/fasttask/files/fasttask/lazy_action/`，使 lazy_action 的磁盘缓存和锁文件归入 `files/fasttask/` 系统内部目录下。

系统 SHALL 在启动时自动创建该目录（通过 `run.py` 的 `init_dir`）。

该目录因位于 `files/fasttask/` 下，SHALL 被文件清理进程自动跳过，无需在 `FILE_CLEANUP_SKIP_PATTERNS` 中显式配置。

#### Scenario: 默认路径位于系统内部目录

- **WHEN** 未显式设置 `LAZY_ACTION_FILE_PATH` 环境变量
- **THEN** 默认值为 `/fasttask/files/fasttask/lazy_action/`，目录在启动时自动创建，且被清理进程自动跳过

#### Scenario: 显式覆盖路径

- **WHEN** 显式设置 `LAZY_ACTION_FILE_PATH=/custom/path/`
- **THEN** 系统使用 `/custom/path/`，但默认值仍被 `force_default=True` 覆盖为 `/fasttask/files/fasttask/lazy_action/`
