# run.py
import ipaddress
import os
import shutil
import socket
import subprocess

from utils.result_storage import VALID_RESULT_TYPES
from utils.tools import load_tasks

log_prefix = "FastTask --->"


class Env:
    def __init__(
        self,
        key,
        default_value=None,
        force_default=False,
        init_func=None,
        is_print_env=True,
        optional=False,
    ):
        self.key = key
        self.default_value = default_value
        self.force_default = force_default
        self.init_func = init_func
        self.is_print_env = is_print_env
        # optional：允许默认值为空（如 S3_* 仅在 RESULT_TYPE=S3/AUTO 时必填）
        self.optional = optional

    def get_default_value(self):
        return (
            self.default_value() if callable(self.default_value) else self.default_value
        )

    def init_env(self):
        value = os.environ.get(self.key)
        if not self.get_default_value() and value is None and not self.optional:
            raise Exception(f"env {self.key} is required")

        if value is None:
            os.environ[self.key] = str(self.get_default_value() or "")
            self.print_env("use default env.")
        elif self.force_default:
            os.environ[self.key] = str(self.get_default_value())
            self.print_env("set default env.")
        else:
            self.print_env("use customer env.")

        if self.init_func:
            self.init_func(os.environ[self.key])

    def print_env(self, msg):
        if self.is_print_env:
            print(f"{log_prefix} {msg} {self.key}={os.environ[self.key]}")


def init_dir(dir_path):
    if not os.path.isdir(dir_path):
        os.makedirs(dir_path)
        print(f"{log_prefix} folder created. '{dir_path}'")


def validate_public_endpoint(value):
    """校验 PUBLIC_ENDPOINT 的取值合法；不合法直接失败。

    这里刻意**不做容错清洗**：值写错就该立刻报错，而不是静默生成一张坏证书。
    典型错误是 compose 的 list 形式里写成 ``KEY="1.2.3.4:9014"`` —— 引号会成为值的
    一部分，结果是 CN 里多个引号、IP 被当成域名（SAN 变成 DNS:1.2.3.4）。这种错误
    从启动日志上完全看不出来，只有等客户端连不上才暴露，所以必须在入口就拦下。
    """
    if not value:
        return
    illegal = [c for c in ('"', "'", " ", "\t", "/", "\\") if c in value]
    if illegal:
        raise Exception(
            f"PUBLIC_ENDPOINT 含非法字符 {illegal!r}: {value!r}\n"
            "  它应当是 host 或 host:port（如 10.24.103.95:9014）。\n"
            "  常见原因：docker compose 的 list 形式里写了 KEY=\"...\"，"
            "引号会被当成值的一部分（改用 KEY=... 或 map 形式）。"
        )
    host, sep, port = value.rpartition(":")
    if sep:
        if not port.isdigit():
            raise Exception(f"PUBLIC_ENDPOINT 的端口不是数字: {value!r}")
        if not host:
            raise Exception(f"PUBLIC_ENDPOINT 缺少主机部分: {value!r}")


def split_host_port(public_endpoint):
    """把 ``host[:port]`` 拆成纯主机部分（剥掉端口）。

    证书的 CN/SAN 不能带端口，而下载地址前缀需要保留端口，所以两处取值不同。
    不用 urlparse：``10.0.0.1:9014`` 会被它当成 scheme。
    """
    value = (public_endpoint or "").strip()
    if not value:
        return ""
    # 形如 [::1]:9014 时剥离 IPv6 字面量外的端口
    if value.startswith("["):
        return value.split("]", 1)[0].lstrip("[")
    if ":" in value:
        return value.rsplit(":", 1)[0]
    return value


def ssl_san_entries(public_endpoint):
    """生成证书的 SAN 条目：PUBLIC_ENDPOINT 本身 + 本机可达地址。

    现代 TLS 客户端（含浏览器、Node、Go）只校验 SAN、不看 CN，所以客户端会用到的
    每个地址都必须出现在 SAN 里。除 PUBLIC_ENDPOINT 外再补上本机地址，保证容器内自检、
    同容器网络内直连也能通过校验。
    """
    entries = []

    def add(entry):
        if entry not in entries:
            entries.append(entry)

    host = split_host_port(public_endpoint)
    if host:
        try:
            ipaddress.ip_address(host)
            add(f"IP:{host}")
        except ValueError:
            add(f"DNS:{host}")

    # 只取 IPv4：IPv6 在 SAN 里的写法容易踩坑，内网场景基本用不到。
    add("IP:127.0.0.1")
    add("DNS:localhost")
    hostname = socket.gethostname()
    if hostname:
        add(f"DNS:{hostname}")
        try:
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                add(f"IP:{info[4][0]}")
        except OSError:
            pass
    return entries


def generate_ssl_certs():
    ssl_keyfile = os.environ["SSL_KEYFILE"]
    ssl_certfile = os.environ["SSL_CERTFILE"]
    cn_marker = os.path.join(os.environ["SSL_CERT_DIR"], "cert.cn")

    # PUBLIC_ENDPOINT（客户端访问本服务的 host[:port]，与 S3_ENDPOINT 同一套写法）
    # 一处决定两件事：证书的 CN/SAN
    # 与外置结果的下载地址前缀。未设置时回落 localhost（与历史行为一致）。
    public_endpoint = os.environ.get("PUBLIC_ENDPOINT", "").strip()
    primary_cn = split_host_port(public_endpoint) or "localhost"
    marker_value = public_endpoint or "localhost"

    # 证书已存在且地址未变时复用，避免每次重启都重新生成（客户端需重新信任）。
    if os.path.isfile(ssl_keyfile) and os.path.isfile(ssl_certfile):
        try:
            with open(cn_marker, encoding="utf-8") as f:
                existing_cn = f.read().strip()
        except OSError:
            existing_cn = ""
        if existing_cn == marker_value:
            return
        # 旧证书是按另一个地址生成的（或有历史遗留），重建
        print(
            f"{log_prefix} PUBLIC_ENDPOINT changed ('{existing_cn or 'unknown'}' -> "
            f"'{marker_value}'), regenerating SSL certificates"
        )
        for stale in (ssl_keyfile, ssl_certfile):
            try:
                os.remove(stale)
            except OSError:
                pass

    san_entries = ssl_san_entries(public_endpoint)

    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-newkey",
            "rsa:4096",
            "-keyout",
            ssl_keyfile,
            "-out",
            ssl_certfile,
            "-days",
            "365",
            "-subj",
            f"/CN={primary_cn}",
            "-addext",
            f"subjectAltName={','.join(san_entries)}",
        ],
        check=True,
    )
    with open(cn_marker, "w", encoding="utf-8") as f:
        f.write(marker_value)
    print(
        f"{log_prefix} SSL certificates generated in {os.environ['SSL_CERT_DIR']} "
        f"(CN={primary_cn}, SAN={','.join(san_entries)})"
    )


def show_banner():
    print("""

          
            ⚡⚡⚡⚡⚡⚡⚡⚡⚡  F A S T T A S K  ⚡⚡⚡⚡⚡⚡⚡⚡⚡

          
""")


env_type_to_envs = {
    "common": [
        Env("NODE_TYPE"),
        Env("SOFT_TIME_LIMIT", default_value=24 * 60 * 60),
        Env("FILE_CLEANUP_ENABLED", "True"),
        Env(
            "FILE_EXPIRATION_SECONDS",
            default_value=lambda: int(os.environ.get("SOFT_TIME_LIMIT")) * 10,
        ),
        Env(
            "TIME_LIMIT",
            default_value=lambda: int(os.environ.get("SOFT_TIME_LIMIT")) + 60,
        ),
        Env(
            "VISIBILITY_TIMEOUT",
            default_value=lambda: int(os.environ.get("TIME_LIMIT")) + 60,
        ),
        Env("RESULT_EXPIRES", f"{3 * 24 * 60 * 60}"),
        # 结果存储层：JSON（默认，内联在 Celery backend）/ S3 / AUTO（超阈值走对象存储）
        Env("RESULT_TYPE", "JSON"),
        Env("RESULT_AUTO_TO_S3_SIZE", str(1024 * 1024)),
        Env("RESULT_TO_S3_TRIES", "3"),
        # 对象存储：模块内置，均为内部实现约定（端口/桶名/地址等），
        # 改了只会出错，因此全部 force_default。
        # 行为参数（上传重试次数、AUTO 阈值）不在此列，允许用户按需调整。
        Env("S3_PORT", "9000", force_default=True),
        Env("S3_BUCKET", "fasttask-results", force_default=True),
        Env("S3_ENDPOINT", default_value="", optional=True, force_default=True),
        Env("S3_PREFIX", default_value="", optional=True, force_default=True),
        Env("S3_REGION", "us-east-1", force_default=True),
        Env("S3_SECURE", "False", force_default=True),
        Env("S3_VERIFY_SSL", "True", force_default=True),
        Env(
            "S3_PRESIGN_EXPIRES",
            default_value=lambda: int(os.environ.get("SOFT_TIME_LIMIT")),
            force_default=True,
        ),
        Env(
            "LOADED_TASKS",
            default_value=lambda: ",".join(
                load_tasks(from_folder="tasks", to_folder="loaded_tasks")
            ),
            force_default=True,
        ),
        Env("FASTTASK_DIR", "/fasttask", force_default=True, is_print_env=False),
        Env(
            "FILES_DIR",
            "/fasttask/files",
            force_default=True,
            init_func=init_dir,
            is_print_env=False,
        ),
        Env(
            "FASTTASK_FILES_DIR",
            "/fasttask/files/fasttask",
            force_default=True,
            init_func=init_dir,
            is_print_env=False,
        ),
        Env(
            "S3_DATA_DIR",
            default_value=lambda: os.path.join(
                os.environ["FASTTASK_FILES_DIR"], "s3"
            ),
            force_default=True,
            init_func=init_dir,
            is_print_env=False,
        ),
        Env(
            "LOG_DIR",
            "/fasttask/files/fasttask/log",
            force_default=True,
            init_func=init_dir,
            is_print_env=False,
        ),
        Env(
            "CONF_DIR",
            "/fasttask/files/fasttask/conf",
            force_default=True,
            init_func=init_dir,
            is_print_env=False,
        ),
        Env(
            "SSL_CERT_DIR",
            "/fasttask/files/fasttask/ssl_cert",
            force_default=True,
            init_func=init_dir,
            is_print_env=False,
        ),
        Env(
            "REDIS_DIR",
            "/fasttask/files/fasttask/redis",
            force_default=True,
            init_func=init_dir,
            is_print_env=False,
        ),
        Env(
            "SSL_KEYFILE",
            "/fasttask/files/fasttask/ssl_cert/key.pem",
            force_default=True,
            is_print_env=False,
        ),
        # 客户端访问本服务的地址（可带端口，如 10.24.103.95:9014 或 fp.example.com）。
        # 一处配置同时决定两件事：自签证书的 CN/SAN，以及外置结果返回的下载地址前缀。
        # 默认空 = 证书用 localhost、下载地址按请求头推导（与历史行为一致）。
        # 部署在以 IP/域名访问的环境下应当显式设置，否则证书主机名校验会失败。
        Env(
            "PUBLIC_ENDPOINT",
            default_value="",
            optional=True,
            init_func=validate_public_endpoint,
        ),
        Env(
            "SSL_CERTFILE",
            "/fasttask/files/fasttask/ssl_cert/cert.pem",
            force_default=True,
            is_print_env=False,
        ),
        Env(
            "CELERY_DIR",
            "/fasttask/files/fasttask/celery",
            force_default=True,
            init_func=init_dir,
            is_print_env=False,
        ),
        Env(
            "LAZY_ACTION_FILE_PATH",
            "/fasttask/files/fasttask/lazy_action",
            force_default=True,
            init_func=init_dir,
            is_print_env=False,
        ),
        Env("WORKER_POOL", "prefork"),
        Env("WORKER_CONCURRENCY", os.cpu_count()),
        Env("DEBUG", "False"),
        Env("FLOWER_ENABLED", "False"),
    ],
    "single_node": [
        # com default
        Env("MASTER_HOST", "0.0.0.0", force_default=True),
        Env("TASK_QUEUE_PORT", "6379", force_default=True),
        Env("TASK_QUEUE_PASSWD", "passwd", force_default=True),
        # master default
        # 2 个 worker：压缩等 CPU 型工作在线程池里跑，单 worker 时仍会相互争抢
        Env("UVICORN_WORKERS", 2),
        Env("API_DOCS", "True"),
        Env("API_STATUS_INFO", "True"),
        Env("API_MCP", "True"),
        Env("API_RUN", "True"),
        Env("API_CREATE", "True"),
        Env("API_CHECK", "True"),
        Env("API_REVOKE", "True"),
        Env("API_FILE_DOWNLOAD", "True"),
        Env("API_FILE_UPLOAD", "True"),
        Env("RESPONSE_COMPRESS", "True"),
        Env("RESPONSE_COMPRESS_LEVEL", 5),
        Env("FLOWER_PORT", "5555", force_default=True),
        Env("FLOWER_UNAUTHENTICATED_API", "True", force_default=True),
        Env("FLOWER_MAX_TASKS", "1000"),
        Env("WORKER_TAG", "worker"),
    ],
    "distributed_master": [
        Env("MASTER_HOST", "0.0.0.0", force_default=True),
        Env("TASK_QUEUE_PORT", "6379", force_default=True),
        Env("TASK_QUEUE_PASSWD"),
        Env("UVICORN_WORKERS", 2),
        Env("API_DOCS", "True"),
        Env("API_STATUS_INFO", "True"),
        Env("API_MCP", "True"),
        Env("API_RUN", "True"),
        Env("API_CREATE", "True"),
        Env("API_CHECK", "True"),
        Env("API_REVOKE", "True"),
        Env("API_FILE_DOWNLOAD", "True"),
        Env("API_FILE_UPLOAD", "True"),
        Env("RESPONSE_COMPRESS", "True"),
        Env("RESPONSE_COMPRESS_LEVEL", 5),
        Env("FLOWER_PORT", "5555", force_default=True),
        Env("FLOWER_UNAUTHENTICATED_API", "True", force_default=True),
        Env("FLOWER_MAX_TASKS", "1000"),
    ],
    "distributed_worker": [
        Env("MASTER_HOST"),
        Env("TASK_QUEUE_PORT"),
        Env("TASK_QUEUE_PASSWD"),
        Env("WORKER_TAG", "worker"),
    ],
}


def assemble_supervisor_conf():
    """根据 NODE_TYPE 和环境变量，从模板目录组装最终配置到 supervisord_conf/。"""
    template_dir = "supervisord_template_conf"
    conf_dir = "supervisord_conf"
    node_type = os.environ["NODE_TYPE"]

    # 清理并重建配置目录
    if os.path.exists(conf_dir):
        shutil.rmtree(conf_dir)
    os.makedirs(conf_dir)

    # 复制主配置
    shutil.copy(os.path.join(template_dir, "supervisord.conf"), conf_dir)

    # 按节点类型复制服务配置
    if node_type in ("single_node", "distributed_master"):
        shutil.copy(os.path.join(template_dir, "redis.conf"), conf_dir)
        shutil.copy(os.path.join(template_dir, "uvicorn.conf"), conf_dir)
        if os.environ.get("FLOWER_ENABLED", "False") == "True":
            shutil.copy(os.path.join(template_dir, "flower.conf"), conf_dir)
        # 内嵌对象存储：仅提供 API 的节点需要（worker 只用客户端连过来）
        if os.environ.get("RESULT_TYPE", "JSON").strip().upper() in ("S3", "AUTO"):
            shutil.copy(os.path.join(template_dir, "s3.conf"), conf_dir)

    if node_type in ("single_node", "distributed_worker"):
        shutil.copy(os.path.join(template_dir, "celery.conf"), conf_dir)

    # file_cleanup：开启文件清理，或结果外置到对象存储（需要清理过期对象）时启用
    if os.environ.get("FILE_CLEANUP_ENABLED", "False") == "True" or (
        os.environ.get("RESULT_TYPE", "JSON").strip().upper() in ("S3", "AUTO")
    ):
        shutil.copy(os.path.join(template_dir, "file_cleanup.conf"), conf_dir)


def start():
    assemble_supervisor_conf()
    os.execv(
        "/usr/bin/supervisord",
        [
            "/usr/bin/supervisord",  # 必须包含可执行文件路径
            "-c",
            "supervisord_conf/supervisord.conf",
        ],
    )


def check_envs():
    SOFT_TIME_LIMIT = int(os.environ.get("SOFT_TIME_LIMIT"))
    TIME_LIMIT = int(os.environ.get("TIME_LIMIT"))
    VISIBILITY_TIMEOUT = int(os.environ.get("VISIBILITY_TIMEOUT"))

    if SOFT_TIME_LIMIT >= TIME_LIMIT:
        raise Exception("TIME_LIMIT must be greater than SOFT_TIME_LIMIT")

    if TIME_LIMIT >= VISIBILITY_TIMEOUT:
        raise Exception("VISIBILITY_TIMEOUT must be greater than TIME_LIMIT")

    if os.environ.get("FILE_CLEANUP_ENABLED", "False") == "True":
        expiration = int(os.environ.get("FILE_EXPIRATION_SECONDS", 0))
        if expiration < 60:
            raise Exception(
                f"FILE_EXPIRATION_SECONDS must be at least 60 seconds, got {expiration}"
            )

    check_result_storage_envs()


def _int_env(key, default):
    """读取整数环境变量；无法解析时直接报错（不静默用默认值）。"""
    raw = os.environ.get(key, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise Exception(f"{key} must be an integer, got {raw!r}") from None


def check_result_storage_envs():
    """结果存储层的前置校验。

    非法配置一律直接报错：静默兑底（例如把拼错的 ``RESULT_TYPE`` 当成 JSON）
    会让“以为开了外置、实际没开”这类问题极难排查。

    ``S3_ENDPOINT`` / ``S3_BUCKET`` 具备默认值（指向内嵌对象存储），无需强制配置。
    """
    result_type = os.environ.get("RESULT_TYPE", "JSON").strip().upper()
    if result_type not in VALID_RESULT_TYPES:
        raise Exception(
            f"RESULT_TYPE must be one of {VALID_RESULT_TYPES}, got {result_type!r}"
        )

    if result_type == "JSON":
        # 不启用对象存储：S3_* 不生效，不校验
        return

    tries = _int_env("RESULT_TO_S3_TRIES", 3)
    if tries < 1:
        raise Exception(f"RESULT_TO_S3_TRIES must be >= 1, got {tries}")

    auto_size = _int_env("RESULT_AUTO_TO_S3_SIZE", 1024 * 1024)
    if auto_size < 0:
        raise Exception(f"RESULT_AUTO_TO_S3_SIZE must be >= 0, got {auto_size}")

    s3_port = _int_env("S3_PORT", 9000)
    if not 0 < s3_port < 65536:
        raise Exception(f"S3_PORT must be within 1-65535, got {s3_port}")

    file_expiration = _int_env("FILE_EXPIRATION_SECONDS", 0)

    # 预签名有效期不得超过对象保留期，否则会出现“URL 有效但对象已删”的悬空地址
    presign_expires = _int_env("S3_PRESIGN_EXPIRES", 0)
    if presign_expires <= 0:
        raise Exception(f"S3_PRESIGN_EXPIRES must be > 0, got {presign_expires}")
    if presign_expires >= file_expiration:
        raise Exception(
            "S3_PRESIGN_EXPIRES must be less than FILE_EXPIRATION_SECONDS: "
            f"S3_PRESIGN_EXPIRES={presign_expires} "
            f"FILE_EXPIRATION_SECONDS={file_expiration}"
        )

    # 结果引用（Redis）必须早于对象清理（对象存储）失效，
    # 否则 check 会返回一个指向已被删除对象的引用。
    result_expires = _int_env("RESULT_EXPIRES", 0)
    if result_expires >= file_expiration:
        raise Exception(
            "RESULT_EXPIRES must be less than FILE_EXPIRATION_SECONDS: "
            f"RESULT_EXPIRES={result_expires} "
            f"FILE_EXPIRATION_SECONDS={file_expiration}"
        )


def main():
    for env in env_type_to_envs["common"]:
        env.init_env()

    if os.environ["NODE_TYPE"] == "single_node":
        for env in env_type_to_envs["single_node"]:
            env.init_env()
        check_envs()

        generate_ssl_certs()
        show_banner()
        start()
    elif os.environ["NODE_TYPE"] == "distributed_master":
        for env in env_type_to_envs["distributed_master"]:
            env.init_env()
        check_envs()
        generate_ssl_certs()
        show_banner()
        start()
    elif os.environ["NODE_TYPE"] == "distributed_worker":
        for env in env_type_to_envs["distributed_worker"]:
            env.init_env()
        check_envs()

        show_banner()
        start()
    else:
        show_banner()
        raise Exception(f"{os.environ['NODE_TYPE']} is not supported")


if __name__ == "__main__":
    main()
