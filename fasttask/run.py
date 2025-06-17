# run.py
import os
import subprocess
import uuid

from load_tasks import load_tasks

log_prefix = "fasttask ---->>"


class Env:
    def __init__(
        self,
        key,
        default_value=None,
        force_default=False,
        init_func=None,
        is_print_env=True,
    ):
        self.key = key
        self.default_value = default_value
        self.force_default = force_default
        self.init_func = init_func
        self.is_print_env = is_print_env

    def get_default_value(self):
        return (
            self.default_value() if callable(self.default_value) else self.default_value
        )

    def init_env(self):
        value = os.environ.get(self.key)
        if not self.get_default_value() and value is None:
            raise Exception(f"env {self.key} is required")

        if value is None:
            os.environ[self.key] = str(self.get_default_value())
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


def generate_ssl_certs():
    ssl_keyfile = os.environ["SSL_KEYFILE"]
    ssl_certfile = os.environ["SSL_CERTFILE"]
    if not os.path.isfile(ssl_keyfile) or not os.path.isfile(ssl_certfile):
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
                "/CN=localhost",
            ],
            check=True,
        )
        print(
            f"{log_prefix} SSL certificates generated in {os.environ['SSL_CERT_DIR']}"
        )


def export_default_env(env_key, env_value, force=False):
    if force or not os.getenv(env_key):
        os.environ[env_key] = env_value
        print(
            f"{log_prefix} {'force set' if force else 'set default'} {env_key}={env_value}"
        )


def show_banner():
    print("""
                                  /\\_/\\
                                 ( o.o )
                                 > ^ <   F A S T T A S K
                              ///|||||\\\\\\
                             ⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡
                             >>  B O O T I N G  U P  <<
                             ⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡

    """)


env_type_to_envs = {
    "common": [
        Env("RUNNING_ID", str(uuid.uuid4()), force_default=True),
        Env("NODE_TYPE"),
        Env("SOFT_TIME_LIMIT", default_value=30 * 60),
        Env(
            "TIME_LIMIT",
            default_value=lambda: int(os.environ.get("SOFT_TIME_LIMIT")) + 60,
        ),
        Env(
            "VISIBILITY_TIMEOUT",
            default_value=lambda: int(os.environ.get("TIME_LIMIT")) + 60,
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
            "SSL_KEYFILE",
            "/fasttask/files/fasttask/ssl_cert/key.pem",
            force_default=True,
            is_print_env=False,
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
        Env("WORKER_POOL", "prefork"),
        Env("WORKER_CONCURRENCY", os.cpu_count()),
    ],
    "single_node": [
        # com default
        Env("MASTER_HOST", "0.0.0.0", force_default=True),
        Env("TASK_QUEUE_PORT", "6379", force_default=True),
        Env("TASK_QUEUE_PASSWD", "passwd", force_default=True),
        # master default
        Env("UVICORN_WORKERS", os.cpu_count()),
        Env("API_DOCS", "True"),
        Env("API_REDOC", "True"),
        Env("API_STATUS_INFO", "True"),
        Env("API_RUN", "True"),
        Env("API_CREATE", "True"),
        Env("API_CHECK", "True"),
        Env("API_REVOKE", "True"),
        Env("API_FILE_DOWNLOAD", "True"),
        Env("API_FILE_UPLOAD", "True"),
        Env("RESULT_EXPIRES", f"{24 * 60 * 60}"),
    ],
    "distributed_master": [
        Env("MASTER_HOST", "0.0.0.0", force_default=True),
        Env("TASK_QUEUE_PORT", "6379", force_default=True),
        Env("TASK_QUEUE_PASSWD"),
        Env("UVICORN_WORKERS", os.cpu_count()),
        Env("API_DOCS", "True"),
        Env("API_REDOC", "True"),
        Env("API_STATUS_INFO", "True"),
        Env("API_RUN", "True"),
        Env("API_CREATE", "True"),
        Env("API_CHECK", "True"),
        Env("API_REVOKE", "True"),
        Env("API_FILE_DOWNLOAD", "True"),
        Env("API_FILE_UPLOAD", "True"),
    ],
    "worker": [
        Env("MASTER_HOST"),
        Env("TASK_QUEUE_PORT"),
        Env("TASK_QUEUE_PASSWD"),
        Env("RESULT_EXPIRES", f"{24 * 60 * 60}"),
    ],
}


def start():
    os.execv(
        "/usr/bin/supervisord",
        [
            "/usr/bin/supervisord",  # 必须包含可执行文件路径
            "-c",
            f"supervisord_{os.environ['NODE_TYPE']}.conf",
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
        for env in env_type_to_envs["worker"]:
            env.init_env()
        check_envs()

        show_banner()
        start()
    else:
        show_banner()
        raise Exception(f"{os.environ['NODE_TYPE']} is not supported")


if __name__ == "__main__":
    main()
