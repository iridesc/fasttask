# cleanup-skip-patterns

## Purpose

文件清理跳过规则的可配置化。允许用户通过环境变量 `FILE_CLEANUP_SKIP_PATTERNS` 指定清理时需要额外跳过的文件或目录路径。

## ADDED Requirements

### Requirement: 通过环境变量配置清理跳过路径

系统 SHALL 支持通过 `FILE_CLEANUP_SKIP_PATTERNS` 环境变量配置需要在文件清理时额外跳过的路径。

该环境变量的值 SHALL 为逗号分隔的相对路径列表，每个路径相对于 `FILES_DIR` 解析。

当 `FILE_CLEANUP_SKIP_PATTERNS` 未设置或为空字符串时，系统 SHALL 仅跳过 `files/fasttask/` 目录（已有硬编码逻辑）。

系统 SHALL 在清理遍历时，将配置的每个 pattern 解析为绝对路径，若文件/目录的绝对路径以任一 pattern 的绝对路径开头，则跳过该文件/目录。

系统 SHALL NOT 因 pattern 对应路径不存在而报错（静默跳过不存在的 pattern）。

已删除文件的空父目录清理逻辑 SHALL 不受 skip patterns 影响（空目录仍会被清理，除非该目录本身匹配 skip pattern）。

#### Scenario: 跳过配置的目录

- **WHEN** `FILE_CLEANUP_SKIP_PATTERNS="custom_cache"`，且 `FILES_DIR=/fasttask/files/`
- **THEN** `/fasttask/files/custom_cache/` 及其所有子目录和文件在清理时被跳过

#### Scenario: 跳过配置的文件

- **WHEN** `FILE_CLEANUP_SKIP_PATTERNS=".lock_file"`，且 `FILES_DIR=/fasttask/files/`
- **THEN** `/fasttask/files/.lock_file`（若为文件）在清理时被跳过；若该路径实际为目录，则该目录及其内容均被跳过

#### Scenario: 多路径跳过

- **WHEN** `FILE_CLEANUP_SKIP_PATTERNS=".lazy_action,.disk_cache_reset.lock"`
- **THEN** `.lazy_action/` 目录（及其内容）和 `.disk_cache_reset.lock` 文件均在清理时被跳过

#### Scenario: 未配置时仅跳过系统目录

- **WHEN** `FILE_CLEANUP_SKIP_PATTERNS` 未设置或为空字符串
- **THEN** 仅跳过 `files/fasttask/` 目录及其内容（与现有行为一致）

#### Scenario: pattern 路径不存在时静默忽略

- **WHEN** `FILE_CLEANUP_SKIP_PATTERNS="nonexistent_dir"`
- **THEN** 系统不报错，正常执行清理逻辑，不跳过任何额外路径
