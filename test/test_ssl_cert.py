"""自签证书生成测试：TLS_CN（含多值）决定 CN/SAN，改了要能重新生成。

不依赖运行中的服务，直接调用 run.generate_ssl_certs()。
"""

import importlib.util
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

FASTTASK_DIR = Path(__file__).resolve().parent.parent / "fasttask"


def load_run_module():
    # run.py 内部导入 utils.*，需要把 fasttask/ 放进 sys.path
    if str(FASTTASK_DIR) not in sys.path:
        sys.path.insert(0, str(FASTTASK_DIR))
    spec = importlib.util.spec_from_file_location(
        "fasttask_run_for_test", FASTTASK_DIR / "run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cert_info(certfile):
    """返回 (subject, SAN 列表)。"""
    out = subprocess.run(
        ["openssl", "x509", "-in", certfile, "-noout", "-subject", "-ext", "subjectAltName"],
        capture_output=True, text=True, check=True,
    ).stdout
    subject = ""
    san = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("subject="):
            subject = line.split("=", 1)[1]
            continue
        # 形如 "DNS:localhost, IP Address:127.0.0.1"；
        # 注意 "IP Address:" 自带冒号，必须用正则整体提取
        found = re.findall(r"(?:DNS|IP Address):([^,]+)", line)
        if found:
            san = [x.strip() for x in found]
    return subject, san


def run_case(generate, tls_cn, workdir):
    os.environ["SSL_CERT_DIR"] = str(workdir)
    os.environ["SSL_KEYFILE"] = str(Path(workdir) / "key.pem")
    os.environ["SSL_CERTFILE"] = str(Path(workdir) / "cert.pem")
    if tls_cn is None:
        os.environ.pop("TLS_CN", None)
    else:
        os.environ["TLS_CN"] = tls_cn
    generate()
    return cert_info(os.environ["SSL_CERTFILE"])


def verify(certfile, hostname, is_ip=False):
    flag = "-verify_ip" if is_ip else "-verify_hostname"
    result = subprocess.run(
        ["openssl", "verify", flag, hostname, "-CAfile", certfile, certfile],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def main():
    print("=" * 68)
    print("自签证书生成测试（TLS_CN）")
    print("=" * 68)
    run_module = load_run_module()
    generate = run_module.generate_ssl_certs
    hostname = socket.gethostname()
    failures = []

    def check(label, actual, expected):
        ok = actual == expected
        print(f"{'✅' if ok else '❌'} {label}")
        if not ok:
            print(f"     实际: {actual}")
            print(f"     期望: {expected}")
            failures.append(label)

    def check_in(label, needle, haystack):
        ok = needle in haystack
        print(f"{'✅' if ok else '❌'} {label}")
        if not ok:
            print(f"     SAN 中缺少: {needle}")
            print(f"     实际 SAN: {haystack}")
            failures.append(label)

    # 1. 不设 TLS_CN -> 保持历史行为
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, None, d)
    check("默认（未设置 TLS_CN）CN = localhost", subject, "CN=localhost")
    check_in("默认 SAN 含 127.0.0.1", "127.0.0.1", san)
    check_in("默认 SAN 含 localhost", "localhost", san)
    check_in("默认 SAN 含容器 hostname（自动补充）", hostname, san)

    # 2. TLS_CN 为单个 IP
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, "192.0.2.10", d)
    check("TLS_CN=单个 IP 时 CN", subject, "CN=192.0.2.10")
    check_in("SAN 含该 IP", "192.0.2.10", san)
    check("证书对该 IP 校验通过", verify(os.environ["SSL_CERTFILE"], "192.0.2.10", is_ip=True), True)

    # 3. TLS_CN 为单个域名
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, "fasttask.example.com", d)
    check("TLS_CN=域名 时 CN", subject, "CN=fasttask.example.com")
    check_in("SAN 含该域名", "fasttask.example.com", san)
    check("证书对该域名校验通过", verify(os.environ["SSL_CERTFILE"], "fasttask.example.com"), True)

    # 4. TLS_CN 多值：CN 取第一个，SAN 含全部
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, "192.0.2.10,fasttask.example.com,198.51.100.20", d)
    check("多值时 CN 取第一个", subject, "CN=192.0.2.10")
    check_in("SAN 含第一个（IP）", "192.0.2.10", san)
    check_in("SAN 含第二个（域名）", "fasttask.example.com", san)
    check_in("SAN 含第三个（另一个 IP）", "198.51.100.20", san)
    check("多值下第一个 IP 校验通过", verify(os.environ["SSL_CERTFILE"], "192.0.2.10", is_ip=True), True)
    check("多值下域名校验通过", verify(os.environ["SSL_CERTFILE"], "fasttask.example.com"), True)
    check("多值下第三个 IP 校验通过", verify(os.environ["SSL_CERTFILE"], "198.51.100.20", is_ip=True), True)

    # 5. 多值里的空白要容忍
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, " 192.0.2.10 , fasttask.example.com ", d)
    check("多值含空格时 CN 去空白", subject, "CN=192.0.2.10")
    check_in("多值含空格时 SAN 仍正确", "fasttask.example.com", san)

    # 6. 改了 TLS_CN 要重新生成（否则旧证书一直生效）
    d = tempfile.mkdtemp()
    run_case(generate, "192.0.2.10", d)
    subject, _ = run_case(generate, "192.0.2.11", d)
    check("TLS_CN 变更后 CN 更新", subject, "CN=192.0.2.11")

    # 7. 多值增删也要触发重建
    d = tempfile.mkdtemp()
    run_case(generate, "192.0.2.10", d)
    _, san = run_case(generate, "192.0.2.10,fasttask.example.com", d)
    check_in("TLS_CN 追加值后 SAN 更新", "fasttask.example.com", san)

    # 8. CN 未变时应复用（不重新生成），通过 mtime 判断
    d = tempfile.mkdtemp()
    run_case(generate, "192.0.2.10", d)
    mtime_before = os.path.getmtime(os.environ["SSL_CERTFILE"])
    run_case(generate, "192.0.2.10", d)
    mtime_after = os.path.getmtime(os.environ["SSL_CERTFILE"])
    check("TLS_CN 未变时复用证书（不重新生成）", mtime_before == mtime_after, True)

    print("=" * 68)
    if failures:
        print(f"❌ 失败 {len(failures)} 项: {failures}")
        sys.exit(1)
    print("✅ 全部通过")
    print("=" * 68)


if __name__ == "__main__":
    main()
