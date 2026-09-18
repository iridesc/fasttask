"""结果存储层验收测试。

覆盖：
  1. S3 凭据派生 / 显式覆盖
  2. 对象 key 的日期前缀
  3. 结果去向判定（JSON / S3 / AUTO 阈值）
  4. 结果落存储：小结果内联、大结果外置
  5. 预签名下载 + sha256 校验（裸 URL，无凭据）
  6. 生产端 Result 结构校验（fail-fast）
  7. 存储层 payload 结构
  8. 任务包装模板生成与导入方式
  9. 启动期环境校验（check_result_storage_envs）
 10. bucket 自检与过期对象清理

依赖：一个 S3 兼容对象存储（MinIO / versitygw / Ceph 均可）。
      S3 相关依赖需要已安装：pip install -r fasttask/requirements.txt

用法：
    export TEST_S3_ENDPOINT=127.0.0.1:9000
    export TEST_S3_ACCESS_KEY=testuser
    export TEST_S3_SECRET_KEY=secret
    export TEST_S3_SECURE=False
    python test/test_result_storage.py
"""

import ast
import hashlib
import json
import os
import pathlib
import sys
import textwrap
import urllib.request
from datetime import datetime, timedelta, timezone

FASTTASK_DIR = pathlib.Path(__file__).resolve().parent.parent / "fasttask"
sys.path.insert(0, str(FASTTASK_DIR))

S3_ENDPOINT = os.environ.get("TEST_S3_ENDPOINT", "127.0.0.1:9000")
S3_ACCESS_KEY = os.environ.get("TEST_S3_ACCESS_KEY", "testuser")
S3_SECRET_KEY = os.environ.get("TEST_S3_SECRET_KEY", "secret")
S3_SECURE = os.environ.get("TEST_S3_SECURE", "False")
S3_BUCKET = os.environ.get("TEST_S3_BUCKET", "fasttask-result-selftest")

os.environ.update(
    {
        "S3_ENDPOINT": S3_ENDPOINT,
        "S3_BUCKET": S3_BUCKET,
        "S3_SECURE": S3_SECURE,
        "S3_VERIFY_SSL": os.environ.get("TEST_S3_VERIFY_SSL", "True"),
        "S3_REGION": os.environ.get("TEST_S3_REGION", "us-east-1"),
        "S3_ACCESS_KEY": S3_ACCESS_KEY,
        "S3_SECRET_KEY": S3_SECRET_KEY,
        "TASK_QUEUE_PASSWD": "selftest-cluster-secret",
        "RESULT_TO_S3_TRIES": "3",
        "S3_PRESIGN_EXPIRES": "3600",
    }
)

from utils import result_storage as rs  # noqa: E402

failures = []


def section(title):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}{'' if cond else f'  -> {detail}'}")
    if not cond:
        failures.append(name)


# --------------------------------------------------------------------------- #
section("1. S3 凭据：派生与覆盖")


class FakeResult:
    """最小 Pydantic 替身，用于验证生产端 Result 校验路径。"""

    def __init__(self, raw):
        self.raw = raw

    @classmethod
    def model_validate(cls, raw):
        if "must_have" not in raw:
            raise ValueError("must_have is required")
        return cls(raw)

    def model_dump(self, mode=None):
        return self.raw


os.environ["S3_ACCESS_KEY"] = ""
os.environ["S3_SECRET_KEY"] = ""
derived_a = rs.derive_s3_credentials()
derived_b = rs.derive_s3_credentials()
check("派生 access_key 固定为 fasttask", derived_a[0] == "fasttask", derived_a)
check("派生结果稳定", derived_a == derived_b)

os.environ["TASK_QUEUE_PASSWD"] = "another-secret"
check("换集群密钥即换派生凭据", rs.derive_s3_credentials() != derived_a)
os.environ["TASK_QUEUE_PASSWD"] = "selftest-cluster-secret"

os.environ["S3_ACCESS_KEY"] = S3_ACCESS_KEY
os.environ["S3_SECRET_KEY"] = S3_SECRET_KEY
check(
    "显式配置优先于派生",
    rs.derive_s3_credentials() == (S3_ACCESS_KEY, S3_SECRET_KEY),
    rs.derive_s3_credentials(),
)

# --------------------------------------------------------------------------- #
section("2. 对象 key 的日期前缀")
os.environ["S3_PREFIX"] = "results"
key = rs.build_object_key("abc-123", at=datetime(2026, 9, 17, tzinfo=timezone.utc))
check("带 prefix 的 key", key == "results/20260917/abc-123.json", key)
os.environ["S3_PREFIX"] = ""
key = rs.build_object_key("abc-123", at=datetime(2026, 1, 2, tzinfo=timezone.utc))
check("无 prefix 的 key", key == "20260102/abc-123.json", key)

# --------------------------------------------------------------------------- #
section("3. 结果去向判定（RESULT_TYPE / 阈值）")
os.environ["RESULT_AUTO_TO_S3_SIZE"] = "1000"
os.environ["RESULT_TYPE"] = "AUTO"
check("AUTO: 等于阈值 -> 内联", rs.should_offload(1000) is False)
check("AUTO: 超过阈值 -> 外置", rs.should_offload(1001) is True)
os.environ["RESULT_TYPE"] = "S3"
check("S3: 一律外置", rs.should_offload(1) is True)
os.environ["RESULT_TYPE"] = "JSON"
check("JSON: 一律内联", rs.should_offload(10**9) is False)

# --------------------------------------------------------------------------- #
section("4. 结果落存储")
rs.ensure_bucket()  # 后续用例依赖桶存在
os.environ["RESULT_TYPE"] = "JSON"
inline = rs.finalize_task_result({"area": 3.14}, "selftest-inline")
check("JSON 模式内联", rs.detect_stored_result_type(inline) == rs.ResultType.json, inline)

os.environ["RESULT_TYPE"] = "AUTO"
small = rs.finalize_task_result({"small": True}, "selftest-small")
check("AUTO 小结果内联", rs.detect_stored_result_type(small) == rs.ResultType.json, small)

big_payload = {"data": "x" * 5000, "items": list(range(20))}
stored = rs.finalize_task_result(big_payload, "selftest-big")
check("AUTO 大结果外置", rs.detect_stored_result_type(stored) == rs.ResultType.s3, stored)
check("uri 前缀正确", str(stored.get("uri", "")).startswith(f"s3://{S3_BUCKET}/"), stored)
check("size_bytes 已记录", stored.get("size_bytes", 0) > 5000, stored)

section("5. 存储层 payload 结构")
check(
    "字段集合固定",
    set(stored) == {rs.STORAGE_MARKER, "key", "uri", "size_bytes", "sha256"},
    sorted(stored),
)
check("标记字段取值", stored.get(rs.STORAGE_MARKER) == rs.ResultType.s3.value, stored)

# --------------------------------------------------------------------------- #
section("6. 预签名下载 + sha256 校验")
reference = rs.build_s3_result_response(stored)
check("生成预签名地址", bool(reference.get("url")), reference)
check("返回 expires_at", bool(reference.get("expires_at")), reference)
# url 是相对路径：服务端不需要知道自己的对外地址，由客户端拼上服务地址
check("url 是相对路径", reference["url"].startswith("/"), reference["url"][:60])

download_url = f"http://{S3_ENDPOINT}{reference['url']}"
try:
    with urllib.request.urlopen(download_url, timeout=15) as response:
        downloaded = response.read()
    check("裸 URL 下载成功（无凭据）", True)
    check("字节数与 size_bytes 一致", len(downloaded) == reference["size_bytes"])
    check("sha256 一致", hashlib.sha256(downloaded).hexdigest() == reference["sha256"])
    check("内容一致", json.loads(downloaded.decode("utf-8")) == big_payload)
except Exception as error:  # noqa: BLE001
    check("裸 URL 下载成功（无凭据）", False, repr(error))

# --------------------------------------------------------------------------- #
section("7. 生产端 Result 结构校验（fail-fast）")
os.environ["RESULT_TYPE"] = "JSON"
ok = rs.finalize_task_result({"must_have": 1}, "selftest-valid", result_model=FakeResult)
check("合法结构通过", ok == {"must_have": 1}, ok)
try:
    rs.finalize_task_result({"other": 1}, "selftest-invalid", result_model=FakeResult)
    check("非法结构被拦截", False, "未抛出异常")
except ValueError as error:
    check("非法结构被拦截", "must_have" in str(error), error)

# --------------------------------------------------------------------------- #
section("8. 任务包装模板生成与导入方式")
os.environ["SOFT_TIME_LIMIT"] = os.environ.get("SOFT_TIME_LIMIT", "86400")
os.environ["TIME_LIMIT"] = os.environ.get("TIME_LIMIT", "86500")
from utils.tools import task_file_template  # noqa: E402

code = task_file_template.format(
    task_name="selftest_task", soft_time_limit="86400", time_limit="86500"
)
try:
    ast.parse(code)
    check("模板代码语法正确", True)
except SyntaxError as error:
    check("模板代码语法正确", False, error)
check("模板调用收口函数", "finalize_task_result" in code)
check("模板取用任务的 Result 模型", 'getattr(_task_module, "Result", None)' in code)

sandbox = pathlib.Path("/tmp/fasttask-template-selftest/tasks_home")
(sandbox / "tasks").mkdir(parents=True, exist_ok=True)
(sandbox / "tasks" / "__init__.py").unlink(missing_ok=True)  # 保持命名空间包
(sandbox / "tasks" / "with_result.py").write_text(
    textwrap.dedent(
        """
        class Result:
            pass


        def with_result():
            return {"ok": True}
        """
    )
)
(sandbox / "tasks" / "without_result.py").write_text(
    textwrap.dedent(
        """
        def without_result():
            return {"ok": True}
        """
    )
)
sys.path.insert(0, str(sandbox))
from tasks import with_result as _with_result  # noqa: E402
from tasks import without_result as _without_result  # noqa: E402

check("命名空间包导入函数", callable(getattr(_with_result, "with_result")))
check("能取到 Result 模型", getattr(_with_result, "Result", None) is not None)
check("缺 Result 时兜底为 None", getattr(_without_result, "Result", None) is None)

# --------------------------------------------------------------------------- #
section("9. 启动期环境校验")
import run  # noqa: E402


def expect_fail(envs, keyword):
    original = {k: os.environ.get(k) for k in envs}
    os.environ.update(envs)
    try:
        run.check_result_storage_envs()
    except Exception as error:  # noqa: BLE001
        check(f"拦截: {keyword[:42]}", keyword in str(error), error)
    else:
        check(f"拦截: {keyword[:42]}", False, "未抛出异常")
    finally:
        for k, v in original.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


os.environ["RESULT_TYPE"] = "JSON"
_saved_for_json_check = {
    key: os.environ.get(key) for key in ("S3_ENDPOINT", "S3_BUCKET")
}
for _key in ("S3_ENDPOINT", "S3_BUCKET"):
    os.environ.pop(_key, None)
try:
    run.check_result_storage_envs()
    check("JSON 模式无需 S3 配置", True)
except Exception as error:  # noqa: BLE001
    check("JSON 模式无需 S3 配置", False, error)
finally:
    for _key, _value in _saved_for_json_check.items():
        if _value is not None:
            os.environ[_key] = _value

expect_fail(
    {
        "RESULT_TYPE": "AUTO",
        "S3_ENDPOINT": "x:9000",
        "S3_BUCKET": "b",
        "RESULT_EXPIRES": "900",
        "FILE_EXPIRATION_SECONDS": "600",
        "S3_PRESIGN_EXPIRES": "60",
    },
    "RESULT_EXPIRES must be less than FILE_EXPIRATION_SECONDS",
)
expect_fail(
    {
        "RESULT_TYPE": "AUTO",
        "S3_ENDPOINT": "x:9000",
        "S3_BUCKET": "b",
        "RESULT_EXPIRES": "100",
        "FILE_EXPIRATION_SECONDS": "600",
        "S3_PRESIGN_EXPIRES": "600",
    },
    "S3_PRESIGN_EXPIRES must be less than FILE_EXPIRATION_SECONDS",
)
expect_fail(
    {
        "RESULT_TYPE": "AUTO",
        "S3_ENDPOINT": "x:9000",
        "S3_BUCKET": "b",
        "RESULT_EXPIRES": "100",
        "FILE_EXPIRATION_SECONDS": "600",
        "S3_PRESIGN_EXPIRES": "60",
        "RESULT_TO_S3_TRIES": "0",
    },
    "RESULT_TO_S3_TRIES must be >= 1",
)

# 非法值必须直接报错，不能被静默当成 JSON 处理
expect_fail({"RESULT_TYPE": "S33"}, "RESULT_TYPE must be one of")
expect_fail({"RESULT_TYPE": "MINIO"}, "RESULT_TYPE must be one of")
expect_fail({"RESULT_TYPE": "s3x"}, "RESULT_TYPE must be one of")
expect_fail(
    {
        "RESULT_TYPE": "AUTO",
        "S3_ENDPOINT": "x:9000",
        "S3_BUCKET": "b",
        "RESULT_TO_S3_TRIES": "abc",
    },
    "RESULT_TO_S3_TRIES must be an integer",
)
expect_fail(
    {
        "RESULT_TYPE": "AUTO",
        "S3_ENDPOINT": "x:9000",
        "S3_BUCKET": "b",
        "RESULT_TO_S3_TRIES": "3",
        "S3_PORT": "70000",
    },
    "S3_PORT must be within 1-65535",
)
expect_fail(
    {
        "RESULT_TYPE": "AUTO",
        "S3_ENDPOINT": "x:9000",
        "S3_BUCKET": "b",
        "RESULT_TO_S3_TRIES": "3",
        "S3_PORT": "9000",
        "S3_PRESIGN_EXPIRES": "0",
    },
    "S3_PRESIGN_EXPIRES must be > 0",
)
expect_fail(
    {
        "RESULT_TYPE": "AUTO",
        "S3_ENDPOINT": "x:9000",
        "S3_BUCKET": "b",
        "RESULT_TO_S3_TRIES": "3",
        "S3_PORT": "9000",
        "S3_PRESIGN_EXPIRES": "60",
        "RESULT_AUTO_TO_S3_SIZE": "-1",
    },
    "RESULT_AUTO_TO_S3_SIZE must be >= 0",
)

# JSON 模式下 S3_* 不生效，不应因无关配置妨碍启动
os.environ.update({"RESULT_TYPE": "JSON", "S3_PORT": "not-a-port"})
try:
    run.check_result_storage_envs()
    check("JSON 模式不校验 S3 相关配置", True)
except Exception as error:  # noqa: BLE001
    check("JSON 模式不校验 S3 相关配置", False, error)

section("9b. 对象存储地址/桶名的默认推导（内嵌场景开箱即用）")
_origin = {
    key: os.environ.get(key)
    for key in ("S3_ENDPOINT", "S3_BUCKET", "NODE_TYPE", "MASTER_HOST", "S3_PORT")
}

os.environ.pop("S3_ENDPOINT", None)
os.environ["S3_PORT"] = "9000"
os.environ["NODE_TYPE"] = "single_node"
check(
    "master / single_node 默认指向本机",
    rs.get_s3_endpoint() == "127.0.0.1:9000",
    rs.get_s3_endpoint(),
)

os.environ["NODE_TYPE"] = "distributed_worker"
os.environ["MASTER_HOST"] = "fasttask-master"
check(
    "worker 默认指向 MASTER_HOST",
    rs.get_s3_endpoint() == "fasttask-master:9000",
    rs.get_s3_endpoint(),
)

os.environ["S3_ENDPOINT"] = "custom-endpoint:1234"
check("显式配置优先", rs.get_s3_endpoint() == "custom-endpoint:1234", rs.get_s3_endpoint())

os.environ["S3_BUCKET"] = "custom-bucket"
check("桶名取自环境变量", rs.get_bucket() == "custom-bucket", rs.get_bucket())

sample_key = "demo/key.json"
os.environ["S3_ENDPOINT"] = S3_ENDPOINT  # 明确回到测试端点
signed_url = rs.get_s3_client().presigned_get_object(
    rs.get_bucket(), sample_key, expires=timedelta(seconds=60)
)
check(
    "签名以内部地址计算（Host 由代理转发时统一重写）",
    signed_url.startswith(f"http://{S3_ENDPOINT}/"),
    signed_url[:90],
)

for key, value in _origin.items():
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value

# --------------------------------------------------------------------------- #
section("10. bucket 自检与过期对象清理")
os.environ["RESULT_TYPE"] = "AUTO"
os.environ["RESULT_AUTO_TO_S3_SIZE"] = "1"
os.environ["S3_PREFIX"] = "cleanup-selftest"
os.environ["S3_BUCKET"] = S3_BUCKET

client = rs.get_s3_client()
rs.ensure_bucket()
# 清掉上次运行可能残留的对象，保证断言可重复
for _obj in client.list_objects(S3_BUCKET, prefix="cleanup-selftest", recursive=True):
    client.remove_object(S3_BUCKET, _obj.object_name)
check("ensure_bucket 幂等（bucket 存在）", client.bucket_exists(S3_BUCKET))
rs.ensure_bucket()
check("重复调用不报错", True)

keys = []
for index in range(3):
    payload = rs.finalize_task_result({"n": index}, f"cleanup-obj-{index}")
    keys.append(payload["key"])
check("待清理对象已上传", len(keys) == 3, keys)

removed = rs.cleanup_expired_objects(0)
check("过期对象全部清理", removed == 3, removed)
remaining = list(
    client.list_objects(S3_BUCKET, prefix="cleanup-selftest", recursive=True)
)
check("清理后无残留", remaining == [], remaining)

rs.finalize_task_result({"n": 9}, "cleanup-obj-keep")
removed = rs.cleanup_expired_objects(3600)
check("保留期内不误删", removed == 0, removed)

# --------------------------------------------------------------------------- #
print(f"\n{'=' * 62}")
if failures:
    print(f"❌ 失败 {len(failures)} 项：{failures}")
    print("=" * 62)
    sys.exit(1)
print("结果存储层验收全部通过 ✅")
print("=" * 62)
