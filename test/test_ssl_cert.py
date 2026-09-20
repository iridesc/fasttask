"""自签证书生成测试：PUBLIC_ENDPOINT 决定证书的 CN/SAN，改了要能重新生成。

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


def run_case(generate, public_host, workdir):
    os.environ["SSL_CERT_DIR"] = str(workdir)
    os.environ["SSL_KEYFILE"] = str(Path(workdir) / "key.pem")
    os.environ["SSL_CERTFILE"] = str(Path(workdir) / "cert.pem")
    if public_host is None:
        os.environ.pop("PUBLIC_ENDPOINT", None)
    else:
        os.environ["PUBLIC_ENDPOINT"] = public_host
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
    print("自签证书生成测试（PUBLIC_ENDPOINT）")
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

    # 1. 不设 PUBLIC_ENDPOINT -> 保持历史行为
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, None, d)
    check("默认（未设置 PUBLIC_ENDPOINT）CN = localhost", subject, "CN=localhost")
    check_in("默认 SAN 含 127.0.0.1", "127.0.0.1", san)
    check_in("默认 SAN 含 localhost", "localhost", san)
    check_in("默认 SAN 含容器 hostname（自动补充）", hostname, san)

    # 2. PUBLIC_ENDPOINT 带端口的 IP：CN 必须剥掉端口
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, "192.0.2.10:9014", d)
    check("PUBLIC_ENDPOINT=IP:端口 时 CN 剥掉端口", subject, "CN=192.0.2.10")
    check_in("SAN 含该 IP（不带端口）", "192.0.2.10", san)
    check(
        "证书对该 IP 校验通过",
        verify(os.environ["SSL_CERTFILE"], "192.0.2.10", is_ip=True),
        True,
    )
    check("SAN 中不含端口", "9014" not in ",".join(san), True)

    # 3. PUBLIC_ENDPOINT 不带端口的 IP
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, "192.0.2.11", d)
    check("PUBLIC_ENDPOINT=IP 时 CN", subject, "CN=192.0.2.11")
    check(
        "证书对该 IP 校验通过",
        verify(os.environ["SSL_CERTFILE"], "192.0.2.11", is_ip=True),
        True,
    )

    # 4. PUBLIC_ENDPOINT 为域名（带端口）
    d = tempfile.mkdtemp()
    subject, san = run_case(generate, "fp.example.com:8443", d)
    check("PUBLIC_ENDPOINT=域名:端口 时 CN 剥掉端口", subject, "CN=fp.example.com")
    check_in("SAN 含该域名", "fp.example.com", san)
    check(
        "证书对该域名校验通过",
        verify(os.environ["SSL_CERTFILE"], "fp.example.com"),
        True,
    )

    # 5. 改了 PUBLIC_ENDPOINT 要重新生成（否则旧证书一直生效）
    d = tempfile.mkdtemp()
    run_case(generate, "192.0.2.10:9014", d)
    subject, _ = run_case(generate, "192.0.2.12:9014", d)
    check("PUBLIC_ENDPOINT 变更后 CN 更新", subject, "CN=192.0.2.12")

    # 6. 只改端口也要重建（证书虽然同 CN，但 cert.cn 记录的完整值变了）
    d = tempfile.mkdtemp()
    run_case(generate, "192.0.2.10:9014", d)
    mtime_before = os.path.getmtime(os.environ["SSL_CERTFILE"])
    run_case(generate, "192.0.2.10:9999", d)
    mtime_after = os.path.getmtime(os.environ["SSL_CERTFILE"])
    check("仅端口变化时也重建（保证与 PUBLIC_ENDPOINT 记录一致）", mtime_before != mtime_after, True)

    # 7. 值未变时复用（不重新生成），通过 mtime 判断
    d = tempfile.mkdtemp()
    run_case(generate, "192.0.2.10:9014", d)
    mtime_before = os.path.getmtime(os.environ["SSL_CERTFILE"])
    run_case(generate, "192.0.2.10:9014", d)
    mtime_after = os.path.getmtime(os.environ["SSL_CERTFILE"])
    check("PUBLIC_ENDPOINT 未变时复用证书（不重新生成）", mtime_before == mtime_after, True)

    # 8. PUBLIC_ENDPOINT 取值校验（非法配置必须报错，而不是静默生成坏证书）
    validate = run_module.validate_public_endpoint
    for value, label in [
        ("", "空值"),
        ("192.0.2.10:9014", "IP:端口"),
        ("192.0.2.10", "IP"),
        ("fp.example.com:8443", "域名:端口"),
        ("[::1]:9014", "IPv6:端口"),
    ]:
        try:
            validate(value)
            print(f"✅ 校验通过（合法）: {label} {value!r}")
        except Exception as error:  # noqa: BLE001
            print(f"❌ 校验误拦: {label} {value!r} -> {error}")
            failures.append(f"合法值被误拦: {value!r}")

    for value, label in [
        ('"192.0.2.10:9014"', "带双引号"),
        ("'192.0.2.10:9014'", "带单引号"),
        ("192.0.2.10:90a4", "端口非数字"),
        (":9014", "缺主机"),
        ("http://192.0.2.10:9014", "带 scheme"),
        ("192.0.2.10:9014 ", "尾部空格"),
    ]:
        try:
            validate(value)
            print(f"❌ 非法值未被拦截: {label} {value!r}")
            failures.append(f"非法值未拦截: {value!r}")
        except Exception:  # noqa: BLE001
            print(f"✅ 校验拦截（非法）: {label} {value!r}")

    # 9. split_host_port 的单元行为
    split = run_module.split_host_port
    check("split_host_port('h:9014')", split("h:9014"), "h")
    check("split_host_port('h')", split("h"), "h")
    check("split_host_port('[::1]:9014')", split("[::1]:9014"), "::1")
    check("split_host_port('')", split(""), "")

    print("=" * 68)
    if failures:
        print(f"❌ 失败 {len(failures)} 项: {failures}")
        sys.exit(1)
    print("✅ 全部通过")
    print("=" * 68)


if __name__ == "__main__":
    main()
