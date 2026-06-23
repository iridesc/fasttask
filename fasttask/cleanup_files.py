import os
import time


def cleanup_expired_files():
    """递归扫描 files/ 目录，删除过期文件和空目录。

    跳过 files/fasttask/ 子目录（系统内部文件）。
    基于文件 mtime 判断是否过期，过期后直接删除。
    文件删除后，如果所在目录为空，一并清理空目录。
    """
    if os.environ.get("FILE_CLEANUP_ENABLED", "False") != "True":
        return

    expiration_seconds = int(os.environ.get("FILE_EXPIRATION_SECONDS", 0))
    if expiration_seconds <= 0:
        return

    files_dir = os.path.abspath(os.environ.get("FILES_DIR", "./files/"))
    fasttask_dir = os.path.abspath(os.path.join(files_dir, "fasttask"))
    now = time.time()

    # 递归遍历，自底向上（先文件后目录），方便删空目录
    for root, _dirs, files in os.walk(files_dir, topdown=False):
        # 跳过 fasttask/ 子目录及其子孙
        if os.path.abspath(root).startswith(fasttask_dir + os.sep) or os.path.abspath(root) == fasttask_dir:
            continue

        for name in files:
            file_path = os.path.join(root, name)
            if now - os.path.getmtime(file_path) > expiration_seconds:
                print(f"清理过期文件: {file_path}")
                os.remove(file_path)

        # 删除空目录（排除 files_dir 自身）
        if root != files_dir:
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
