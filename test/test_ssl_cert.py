"""自签证书生成测试：TLS_CN 决定 CN/SAN，改了要能重新生成。

不依赖运行中的服务，直接调用 run.generate_ssl_certs()。
"""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FASTTASK_DIR = Path(__file__).resolve().parent.parent / "fasttask"


def load_generate_ssl_certs():
    # run.py 内部导入 utils.*，需要把 fasttask/ 放进 sys.path
    if str(FASTTASK_DIR) not in sys.path:
        sys.path.insert(0, str(FASTTASK_DIR))
    spec = importlib.util.spec_from_file_location(
        "fasttask_run_for_test", FASTTASK_DIR / "run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_ssl_certs


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


def main():
    print("=" * 68)
    print("自签证书生成测试（TLS_CN）")
    print("=" * 68)
    generate = load_generate_ssl_certs()
    failures = []

    def check(label, actual, expected):
        ok = actual == expected
        print(f"{'✅' if ok else '❌'} {label}")
        print(f"     实际: {actual}")
        if not ok:
            print(f"     期望: {expected}")
            failures.append(label)

    # 1. 不设 TLS_CN -> 保持历史行为
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, None, d)
    check("默认（未设置 TLS_CN）CN", subject, "CN=localhost")
    check("默认 SAN 含 localhost 与 127.0.0.1",
          sorted(san), sorted(["localhost", "127.0.0.1"]))

    # 2. TLS_CN 为 IP
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, "10.24.103.95", d)
    check("TLS_CN=IP 时 CN", subject, "CN=10.24.103.95")
    check("TLS_CN=IP 时 SAN（含该 IP + 本机回环）",
          sorted(san), sorted(["10.24.103.95", "127.0.0.1", "localhost"]))

    # 3. TLS_CN 为域名
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, "fasttask.example.com", d)
    check("TLS_CN=域名 时 CN", subject, "CN=fasttask.example.com")
    check("TLS_CN=域名 时 SAN（含该域名 + 本机回环）",
          sorted(san), sorted(["fasttask.example.com", "127.0.0.1", "localhost"]))

    # 4. 改了 TLS_CN 要重新生成（否则旧证书会一直生效）
    d = tempfile.mkdtemp()
    run_case(generate, "10.24.103.95", d)
    subject, san = run_case(generate, "10.24.103.99", d)
    check("TLS_CN 变更后 CN 更新", subject, "CN=10.24.103.99")

    # 5. CN 未变时应复用（不重新生成），通过 mtime 判断
    d = tempfile.mkdtemp()
    run_case(generate, "10.24.103.95", d)
    mtime_before = os.path.getmtime(os.environ["SSL_CERTFILE"])
    run_case(generate, "10.24.103.95", d)
    mtime_after = os.path.getmtime(os.environ["SSL_CERTFILE"])
    check("TLS_CN 未变时复用证书（不重新生成）",
          mtime_before == mtime_after, True)

    print("=" * 68)
    if failures:
        print(f"❌ 失败 {len(failures)} 项: {failures}")
        sys.exit(1)
    print("✅ 全部通过")
    print("=" * 68)


if __name__ == "__main__":
    main()
