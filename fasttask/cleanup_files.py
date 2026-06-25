import os
import time


def _get_skip_paths(files_dir):
    """解析 FILE_CLEANUP_SKIP_PATTERNS 环境变量，返回需要跳过的绝对路径列表。

    环境变量值为逗号分隔的相对路径（相对于 FILES_DIR）。
    files/fasttask/ 始终包含在跳过列表中，不依赖环境变量配置。
    """
    fasttask_dir = os.path.abspath(os.path.join(files_dir, "fasttask"))
    skip_paths = [fasttask_dir]

    patterns = os.environ.get("FILE_CLEANUP_SKIP_PATTERNS", "").strip()
    if patterns:
        for pattern in patterns.split(","):
            pattern = pattern.strip()
            if pattern:
                skip_paths.append(os.path.abspath(os.path.join(files_dir, pattern)))

    return skip_paths


def _should_skip(path, skip_paths):
    """检查给定路径是否应该被跳过。"""
    abs_path = os.path.abspath(path)
    for skip_path in skip_paths:
        if abs_path == skip_path or abs_path.startswith(skip_path + os.sep):
            return True
    return False


def cleanup_expired_files():
    """递归扫描 files/ 目录，删除过期文件和空目录。

    跳过 files/fasttask/ 子目录（系统内部文件）以及 FILE_CLEANUP_SKIP_PATTERNS
    环境变量中配置的额外路径。
    基于文件 mtime 判断是否过期，过期后直接删除。
    文件删除后，如果所在目录为空，一并清理空目录。
    """
    if os.environ.get("FILE_CLEANUP_ENABLED", "False") != "True":
        return

    expiration_seconds = int(os.environ.get("FILE_EXPIRATION_SECONDS", 0))
    if expiration_seconds <= 0:
        return

    files_dir = os.path.abspath(os.environ.get("FILES_DIR", "./files/"))
    skip_paths = _get_skip_paths(files_dir)
    now = time.time()

    # 递归遍历，自底向上（先文件后目录），方便删空目录
    for root, _dirs, files in os.walk(files_dir, topdown=False):
        # 跳过配置的目录（包括 fasttask/ 子目录及其子孙）
        if _should_skip(root, skip_paths):
            continue

        for name in files:
            file_path = os.path.join(root, name)
            if _should_skip(file_path, skip_paths):
                continue
            if now - os.path.getmtime(file_path) > expiration_seconds:
                print(f"清理过期文件: {file_path}")
                os.remove(file_path)

        # 删除空目录（排除 files_dir 自身和跳过目录）
        if root != files_dir and not _should_skip(root, skip_paths):
            try:
                os.rmdir(root)
                print(f"清理空目录: {root}")
            except OSError:
                pass  # 目录非空，跳过


if __name__ == "__main__":
    print("文件清理进程已启动")
    cleanup_expired_files()
    while True:
        time.sleep(600)
        cleanup_expired_files()
