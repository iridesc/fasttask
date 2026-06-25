## 1. 修改 LAZY_ACTION_FILE_PATH 默认值

- [x] 1.1 在 `run.py` 的 `env_type_to_envs["common"]` 中，将 `LAZY_ACTION_FILE_PATH` 的默认值从 `/fasttask/files/` 改为 `/fasttask/files/fasttask/lazy_action/`，并添加 `init_func=init_dir` 以自动创建目录

## 2. 新增 FILE_CLEANUP_SKIP_PATTERNS 环境变量支持

- [x] 2.1 在 `cleanup_files.py` 中读取 `FILE_CLEANUP_SKIP_PATTERNS` 环境变量（逗号分隔），解析为相对于 `FILES_DIR` 的绝对路径列表
- [x] 2.2 在 `cleanup_files.py` 的 `os.walk` 遍历循环中，对每个文件/目录检查是否匹配 skip patterns，匹配则跳过
- [x] 2.3 确保 `files/fasttask/` 的硬编码跳过逻辑保持不变（始终生效，不依赖环境变量）

## 3. 测试验证

- [x] 3.1 验证 `LAZY_ACTION_FILE_PATH` 默认值变更后，`/fasttask/files/fasttask/lazy_action/` 目录在启动时自动创建
- [x] 3.2 验证 `FILE_CLEANUP_SKIP_PATTERNS` 配置后，指定路径在文件清理时被正确跳过
- [x] 3.3 验证 `FILE_CLEANUP_SKIP_PATTERNS` 未设置时，清理行为与之前一致
