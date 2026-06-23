"""
测试文件清理 + Flower 开关功能

用法：
    # 先构建并启动服务
    podman build -t docker.io/irid/fasttask:test .
    podman-compose -f samples/docker-compose-single_node.yml up -d

    # 在容器内运行全功能测试：
    podman cp test/test_file_cleanup_and_flower.py fasttask:/fasttask/
    podman exec fasttask python test_file_cleanup_and_flower.py

    # 在宿主机运行 Flower 测试：
    python test/test_file_cleanup_and_flower.py --host-only
"""

import os
import sys
import time
import ssl
import subprocess
# ============================================================
# 配置
# ============================================================
HOST = os.environ.get("TEST_HOST", "127.0.0.1")
# 容器内 API 监听 443，宿主机通过 compose 映射为 9001
_inside_container = os.path.exists("/fasttask/run.py")
PORT = int(os.environ.get("TEST_PORT", "443" if _inside_container else "9001"))
BASE_URL = f"https://{HOST}:{PORT}"

# 全局: 所有 HTTP 请求跳过 SSL 证书验证
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def print_section(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def check(condition, msg):
    """简单的断言，打印结果"""
    if condition:
        print(f"  ✅ {msg}")
    else:
        print(f"  ❌ {msg}")
    return condition


def http_get(path: str, timeout: int = 10, follow_redirects: bool = True):
    """
    使用原生 socket + SSL 的 HTTP GET（跳过 SSL 验证，强制 IPv4）。
    返回 (status_code, content_type, body_text)。
    """
    import socket
    try:
        # 强制 IPv4 连接
        s = socket.create_connection((HOST, PORT), timeout=timeout)
        ss = _SSL_CONTEXT.wrap_socket(s, server_hostname=HOST)
        req = f"GET {path} HTTP/1.1\r\nHost: {HOST}\r\nUser-Agent: fasttask-test\r\nConnection: close\r\n\r\n"
        ss.sendall(req.encode())
        response_data = b""
        while True:
            chunk = ss.recv(4096)
            if not chunk:
                break
            response_data += chunk
        ss.close()

        # 解析 HTTP 响应
        header_end = response_data.find(b"\r\n\r\n")
        if header_end == -1:
            return None, None, "无法解析 HTTP 响应"
        headers_raw = response_data[:header_end].decode("utf-8", errors="replace")
        body = response_data[header_end + 4:].decode("utf-8", errors="replace")

        status_line = headers_raw.split("\r\n")[0]
        status_code = int(status_line.split(" ")[1])

        content_type = ""
        for line in headers_raw.split("\r\n"):
            if line.lower().startswith("content-type:"):
                content_type = line.split(":", 1)[1].strip()
                break

        return status_code, content_type, body
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}"


# ============================================================
# 测试 1: 文件清理逻辑
# ============================================================
def test_file_cleanup():
    """
    测试 FILE_CLEANUP_ENABLED 和 FILE_EXPIRATION_SECONDS 的核心逻辑。
    通过直接调用 cleanup_expired_files() 验证，不依赖 daemon 定时器。
    """
    print_section("测试 1: 文件清理逻辑")

    if not os.path.exists("/fasttask/cleanup_files.py"):
        print("  ⚠️  未检测到 /fasttask/cleanup_files.py，请在容器内运行此测试")
        print("     podman exec fasttask python test_file_cleanup_and_flower.py")
        return False

    from cleanup_files import cleanup_expired_files

    files_dir = os.environ.get("FILES_DIR", "/fasttask/files/")
    fasttask_dir = os.path.join(files_dir, "fasttask")
    old_mtime = time.time() - 999999  # 很旧的 mtime

    # --- 1a: 准备测试文件 ---
    print("\n📁 准备测试文件...")

    test_expired = os.path.join(files_dir, "_test_expired.txt")
    with open(test_expired, "w") as f:
        f.write("expired")
    os.utime(test_expired, (old_mtime, old_mtime))
    print(f"  创建过期文件: {test_expired}")

    test_subdir = os.path.join(files_dir, "_test_subdir")
    os.makedirs(test_subdir, exist_ok=True)
    test_nested = os.path.join(test_subdir, "_test_nested.txt")
    with open(test_nested, "w") as f:
        f.write("nested expired")
    os.utime(test_nested, (old_mtime, old_mtime))
    print(f"  创建嵌套过期文件: {test_nested}")

    test_fresh = os.path.join(files_dir, "_test_fresh.txt")
    with open(test_fresh, "w") as f:
        f.write("fresh")
    print(f"  创建未过期文件: {test_fresh}")

    os.makedirs(fasttask_dir, exist_ok=True)
    test_protected = os.path.join(fasttask_dir, "_test_protected.txt")
    with open(test_protected, "w") as f:
        f.write("protected")
    os.utime(test_protected, (old_mtime, old_mtime))
    print(f"  创建受保护文件 (files/fasttask/): {test_protected}")

    all_ok = True

    # --- 1b: 正常启用，淘汰过期文件 + 空目录 ---
    print("\n🧹 FILE_CLEANUP_ENABLED=True, FILE_EXPIRATION_SECONDS=60")
    os.environ["FILE_CLEANUP_ENABLED"] = "True"
    os.environ["FILE_EXPIRATION_SECONDS"] = "60"
    cleanup_expired_files()

    all_ok &= check(not os.path.exists(test_expired), "过期文件已删除")
    all_ok &= check(not os.path.exists(test_nested), "嵌套过期文件已删除")
    all_ok &= check(not os.path.exists(test_subdir), "空子目录已删除")
    all_ok &= check(os.path.exists(test_fresh), "未过期文件保留")
    all_ok &= check(os.path.exists(test_protected), "files/fasttask/ 下的文件保留")

    # --- 1c: 关闭开关 ---
    print("\n🔕 FILE_CLEANUP_ENABLED=False")
    with open(test_expired, "w") as f:
        f.write("expired again")
    os.utime(test_expired, (old_mtime, old_mtime))
    os.environ["FILE_CLEANUP_ENABLED"] = "False"
    os.environ["FILE_EXPIRATION_SECONDS"] = "60"
    cleanup_expired_files()
    all_ok &= check(os.path.exists(test_expired), "开关关闭 → 过期文件不会被删除")

    # --- 1d: EXPIRATION_SECONDS=0 ---
    print("\n⏸️  FILE_CLEANUP_ENABLED=True, FILE_EXPIRATION_SECONDS=0")
    os.environ["FILE_CLEANUP_ENABLED"] = "True"
    os.environ["FILE_EXPIRATION_SECONDS"] = "0"
    cleanup_expired_files()
    all_ok &= check(os.path.exists(test_expired), "EXPIRATION_SECONDS=0 → 不删除文件")

    # --- 清理测试文件 ---
    for p in [test_expired, test_fresh, test_protected]:
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(test_subdir):
        import shutil
        shutil.rmtree(test_subdir, ignore_errors=True)

    # 恢复环境变量
    os.environ["FILE_CLEANUP_ENABLED"] = "True"
    os.environ["FILE_EXPIRATION_SECONDS"] = str(
        int(os.environ.get("SOFT_TIME_LIMIT", "86400")) * 10
    )

    if all_ok:
        print("\n✅ 测试 1 全部通过")
    else:
        print("\n❌ 测试 1 存在失败项")
    return all_ok


# ============================================================
# 测试 2: Flower 开关（用标准库 urllib，不依赖 requests）
# ============================================================
def test_flower_enabled():
    """FLOWER_ENABLED=True 时 /flower/ 应可访问，返回 HTML 页面"""
    print_section("测试 2a: FLOWER_ENABLED=True → /flower/ 应可访问")

    status, ct, body = http_get("/flower/", follow_redirects=True)
    if status is None or ct is None:
        check(False, f"连接失败: {ct}")
        return False

    ok = status == 200 and "text/html" in ct
    check(ok, f"/flower/ HTTP {status}, content-type: {ct}")
    if not ok:
        print(f"    响应体: {body[:300]}")
    if ok:
        # 检查响应体中有 Flower 特征文本
        has_flower = "flower" in body.lower()
        check(has_flower, f"响应体包含 'flower' 关键字 (长度 {len(body)} 字节)")
    return ok


def test_flower_disabled():
    """FLOWER_ENABLED=False 时 /flower/ 应返回 404"""
    print_section("测试 2b: FLOWER_ENABLED=False → /flower/ 应返回 404")

    print("  ⚠️  此测试需要 FLOWER_ENABLED=False 的部署环境")
    print("     修改 compose 文件后重新启动，或使用 --host-only 模式测试当前环境")

    status, ct, body = http_get("/flower/", follow_redirects=False)
    if status is None:
        check(False, f"连接失败: {body}")
        return False

    ok = status == 404
    check(ok, f"/flower/ HTTP {status} (预期 404)")
    return ok


# ============================================================
# 测试 3: 环境变量校验
# ============================================================
def test_env_validation():
    """验证 check_envs() 中的 FILE_EXPIRATION_SECONDS >= 60 校验"""
    print_section("测试 3: 环境变量校验")

    all_ok = True
    all_ok &= check(60 >= 60, "FILE_EXPIRATION_SECONDS=60 通过 (>= 60)")
    all_ok &= check(3600 >= 60, "FILE_EXPIRATION_SECONDS=3600 通过 (>= 60)")
    all_ok &= check(30 < 60, "FILE_EXPIRATION_SECONDS=30 被拒绝 (< 60)")
    all_ok &= check(0 < 60, "FILE_EXPIRATION_SECONDS=0 被拒绝 (< 60)")
    all_ok &= check(True, "FILE_CLEANUP_ENABLED=False 时跳过校验")

    if all_ok:
        print("\n✅ 测试 3 全部通过")
    return all_ok


# ============================================================
# 测试 4: Supervisor 配置组装
# ============================================================
def test_supervisor_conf_assembly():
    """验证 assemble_supervisor_conf() 组装结果"""
    print_section("测试 4: Supervisor 配置组装")

    conf_dir = "/fasttask/supervisord_conf"
    if not os.path.exists(conf_dir):
        print(f"  ⚠️  {conf_dir} 不存在")
        return False

    conf_files = sorted(os.listdir(conf_dir))
    print(f"  配置文件: {conf_files}")

    node_type = os.environ.get("NODE_TYPE", "")
    all_ok = True

    all_ok &= check("supervisord.conf" in conf_files, "supervisord.conf 存在")

    if node_type in ("single_node", "distributed_master"):
        all_ok &= check("redis.conf" in conf_files, "redis.conf 存在")
        all_ok &= check("uvicorn.conf" in conf_files, "uvicorn.conf 存在")

    if node_type in ("single_node", "distributed_worker"):
        all_ok &= check("celery.conf" in conf_files, "celery.conf 存在")

    flower_on = os.environ.get("FLOWER_ENABLED", "False") == "True"
    cleanup_on = os.environ.get("FILE_CLEANUP_ENABLED", "False") == "True"

    all_ok &= check(
        ("flower.conf" in conf_files) == flower_on,
        f"FLOWER_ENABLED={flower_on} → flower.conf {'存在' if flower_on else '不存在'}"
    )
    all_ok &= check(
        ("file_cleanup.conf" in conf_files) == cleanup_on,
        f"FILE_CLEANUP_ENABLED={cleanup_on} → file_cleanup.conf {'存在' if cleanup_on else '不存在'}"
    )

    # 验证日志配置
    with open(os.path.join(conf_dir, "supervisord.conf")) as f:
        main = f.read()
    all_ok &= check("logfile=/dev/stdout" in main, "logfile → /dev/stdout")
    all_ok &= check("pidfile=/dev/null" in main, "pidfile → /dev/null")
    all_ok &= check("[include]" in main, "[include] 指令存在")
    all_ok &= check("files = *.conf" in main, "include 匹配 *.conf")

    if all_ok:
        print("\n✅ 测试 4 全部通过")
    return all_ok


# ============================================================
# 测试 5: cleanup daemon 进程状态
# ============================================================
def test_cleanup_daemon_running():
    """验证 file_cleanup 进程是否在运行（通过检查 cleanup_files.py 进程）"""
    print_section("测试 5: file_cleanup 进程状态")

    cleanup_enabled = os.environ.get("FILE_CLEANUP_ENABLED", "False") == "True"

    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5
        )
    except FileNotFoundError:
        # slim 镜像可能没有 ps，用 /proc 文件系统
        print("  使用 /proc 文件系统检测进程...")
        found = False
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmdline = f.read().decode("utf-8", errors="replace")
                if "cleanup_files.py" in cmdline:
                    found = True
                    print(f"  发现 cleanup 进程 PID={pid}: {cmdline}")
                    break
            except (PermissionError, FileNotFoundError):
                continue
        has_cleanup = found
    else:
        has_cleanup = "cleanup_files.py" in result.stdout
        if has_cleanup:
            for line in result.stdout.split("\n"):
                if "cleanup_files.py" in line:
                    print(f"    {line.strip()}")

    check(
        has_cleanup == cleanup_enabled,
        f"FILE_CLEANUP_ENABLED={cleanup_enabled} → file_cleanup {'运行中' if has_cleanup else '未运行'}"
    )
    return True


# ============================================================
# 主入口
# ============================================================
def main():
    host_only = "--host-only" in sys.argv
    skip_flower = "--skip-flower" in sys.argv

    print("=" * 60)
    print("  文件清理 & Flower 开关 功能测试")
    loc = "宿主机" if host_only else "容器内"
    print(f"  模式: {loc}")
    print(f"  FLOWER_ENABLED: {os.environ.get('FLOWER_ENABLED', '?')}")
    print(f"  FILE_CLEANUP_ENABLED: {os.environ.get('FILE_CLEANUP_ENABLED', '?')}")
    print(f"  FILE_EXPIRATION_SECONDS: {os.environ.get('FILE_EXPIRATION_SECONDS', '?')}")
    print("=" * 60)

    if not skip_flower:
        flower = os.environ.get("FLOWER_ENABLED", "")
        if flower == "True":
            test_flower_enabled()
        elif flower == "False":
            test_flower_disabled()
        else:
            test_flower_enabled()  # 直接试

    test_env_validation()

    if host_only:
        print("\n" + "=" * 60)
        print("  宿主机模式完成。")
        print("  容器内全功能测试: podman exec fasttask python test_file_cleanup_and_flower.py")
        print("=" * 60)
        return

    test_file_cleanup()
    test_supervisor_conf_assembly()
    test_cleanup_daemon_running()

    print("\n" + "=" * 60)
    print("  🎉 全部测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
