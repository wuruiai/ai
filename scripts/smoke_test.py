"""端到端冒烟测试（真实 HTTP）

启动后端 → 跑 15 项端到端测试 → 关闭后端 → 退出码反映结果。

设计原则：
    1. 全部用 Python urllib 发送请求；不依赖 curl、jq、requests 等外部工具
    2. 每个测试独立 try/except，单项失败不影响后续项
    3. 失败信息精确：打印期望/实际/响应摘要
    4. 顺序可重复：跑两次结果一致
    5. 自包含：自动启停 uvicorn，不必手动起服务

Usage:
    python -m scripts.smoke_test
    python -m scripts.smoke_test --base-url http://127.0.0.1:8123   # 复用已起服务
    python -m scripts.smoke_test --port 8123 --no-start            # 改端口
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.smoke._runner import backend_running

# ---------- HTTP helpers ----------


class HttpResp:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]):
        self.status = status
        self.body = body
        self.headers = headers

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text) if self.body else None


# 全局鉴权 token：bootstrap 注册后写入，http() 自动带上
_AUTH_TOKEN: str | None = None
_TEST_USER: str = ""
_TEST_PASS: str = ""


def http(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
    origin: str | None = None,
    token: str | None = None,
    timeout: float = 10.0,
) -> HttpResp:
    h = {"Accept": "application/json"}
    tok = token if token is not None else _AUTH_TOKEN
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    if origin is not None:
        h["Origin"] = origin
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
    elif raw_body is not None:
        data = raw_body
    if headers:
        h.update(headers)
    # 客户端 SSRF 防护：只允许 http/https，杜绝 file: 等本地文件访问
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"refuse non-http(s) url in smoke test: {url}")
    # 上一行已拒绝非 http(s) scheme，urlopen 无 file:/custom scheme 可达
    req = urllib.request.Request(url, data=data, method=method, headers=h)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return HttpResp(r.status, r.read(), dict(r.headers))
    except urllib.error.HTTPError as e:
        return HttpResp(e.code, e.read(), dict(e.headers))


# ---------- Test framework ----------


class TestResult:
    def __init__(self, name: str, passed: bool, detail: str = ""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        mark = "PASS" if self.passed else "FAIL"
        return f"  [{mark}] {self.name}" + (f"  -- {self.detail}" if self.detail else "")


class TestRunner:
    def __init__(self):
        self.results: list[TestResult] = []

    def run(self, name: str, fn: Callable[[], str | None]) -> bool:
        try:
            detail = fn() or ""
            self.results.append(TestResult(name, True, detail))
            print(TestResult(name, True, detail))
            return True
        except AssertionError as e:
            self.results.append(TestResult(name, False, f"断言失败: {e}"))
            print(TestResult(name, False, f"断言失败: {e}"))
            return False
        except Exception as e:
            self.results.append(TestResult(name, False, f"{type(e).__name__}: {e}"))
            print(TestResult(name, False, f"{type(e).__name__}: {e}"))
            return False

    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    def summary(self) -> str:
        return f"{self.passed()}/{len(self.results)} passed"


# ---------- Test definitions ----------


def test_1_health_no_token(base: str, t: TestRunner) -> None:
    def go() -> str:
        r = http("GET", f"{base}/health")
        assert r.status == 200, f"status={r.status}"
        body = r.json()
        assert body.get("status") == "ok", f"status field={body.get('status')}"
        # 关键安全断言：health 响应不得包含 api_key
        assert "api_key" not in body, f"api_key leaked: {list(body.keys())}"
        assert "DASHSCOPE_API_KEY" not in r.text, "Key substring leaked"
        return "status=ok, no api_key leak"

    t.run("1. /health 不带令牌正常返回且不回显 Key", go)


def _bootstrap_auth(base: str) -> None:
    """注册唯一测试用户并写入全局 token（首个用户自动成为 admin）。"""
    global _AUTH_TOKEN, _TEST_USER, _TEST_PASS
    _TEST_USER = "smoke_" + uuid.uuid4().hex[:10]
    _TEST_PASS = "smokepass123"  # noqa: S105 -- 冒烟测试专用临时密码，仅用于隔离测试库
    r = http(
        "POST",
        f"{base}/api/v1/auth/register",
        json_body={
            "username": _TEST_USER,
            "password": _TEST_PASS,
            "display_name": "smoke test user",
        },
        token="",
    )
    assert r.status in (200, 201), f"register status={r.status} body={r.text[:200]}"
    _AUTH_TOKEN = r.json()["token"]


def test_2_auth_login_local_origin(base: str, t: TestRunner) -> None:
    def go() -> str:
        r = http(
            "POST",
            f"{base}/api/v1/auth/login",
            json_body={"username": _TEST_USER, "password": _TEST_PASS},
            token="",
        )
        assert r.status == 200, f"login status={r.status} body={r.text[:200]}"
        body = r.json()
        assert "token" in body, f"missing token field: {list(body.keys())}"
        assert body["user"]["role"] == "admin", f"first user should be admin: {body['user']}"
        return f"login → token len={len(body['token'])}, role={body['user']['role']}"

    t.run("2a. auth/login 本地 Origin 接受（首个用户为 admin）", go)


def test_2b_auth_external_origin_rejected(base: str, t: TestRunner) -> None:
    def go() -> str:
        r = http(
            "POST",
            f"{base}/api/v1/auth/register",
            json_body={"username": "evil_" + uuid.uuid4().hex[:8], "password": "evilpass123"},
            origin="https://evil.example.com",
            token="",
        )
        # 期望：4xx（403/401），绝不能 2xx
        assert r.status in (400, 401, 403), (
            f"external origin accepted! status={r.status} body={r.text[:200]}"
        )
        return f"external origin → {r.status}"

    t.run("2b. auth/register 外部 Origin 应拒绝", go)


def test_3_documents_list(base: str, t: TestRunner) -> None:
    def go() -> str:
        r = http("GET", f"{base}/api/v1/documents/")
        assert r.status == 200, f"status={r.status}"
        body = r.json()
        assert isinstance(body, (list, dict)), f"unexpected shape: {type(body).__name__}"
        return "documents reachable"

    t.run("3. GET /api/v1/documents/ 可达", go)


def test_4_documents_create(base: str, t: TestRunner) -> None:
    def go() -> str:
        # 不传文件，仅验证 schema-level 接受 multipart（即便 422 也算路由可达）
        r = http("POST", f"{base}/api/v1/documents/")
        assert r.status in (200, 201, 400, 415, 422), f"unreachable: {r.status}"
        return f"POST status={r.status} (route reachable)"

    t.run("4. POST /api/v1/documents/ 路由可达", go)


def test_5_chat_stream_sse(base: str, t: TestRunner) -> None:
    """SSE 端到端：start / status / token / done 必到；citation 依赖是否已上传文档。"""

    def go() -> str:
        r = http(
            "POST",
            f"{base}/api/v1/chat/stream",
            json_body={"query": "水利工程是什么", "thread_id": "smoke_t1"},
            timeout=15.0,
        )
        assert r.status == 200, f"status={r.status}, body={r.text[:200]}"
        assert "text/event-stream" in r.headers.get("content-type", ""), (
            f"bad content-type: {r.headers.get('content-type')}"
        )
        text = r.text
        # 必到事件（citation 仅在有 chunks 时出现，初始空库允许缺）
        required = ("event: start", "event: status", "event: token", "event: done")
        for evt in required:
            assert evt in text, f"missing {evt!r} in:\n{text[:500]}"
        has_citation = "event: citation" in text
        return f"4 required events, citation={'yes' if has_citation else 'no (empty KB)'}"

    t.run("5. chat/stream 返回 start/status/token/done（citation 可选）", go)


def test_6_chat_stream_done_message_id(base: str, t: TestRunner) -> None:
    def go() -> str:
        r = http(
            "POST",
            f"{base}/api/v1/chat/stream",
            json_body={"query": "测试问题", "thread_id": "smoke_t2"},
            timeout=15.0,
        )
        assert r.status == 200, f"status={r.status}"
        # 找 done 事件的 data 行
        done_data: dict | None = None
        cur_event = None
        for line in r.text.split("\n"):
            if line.startswith("event: "):
                cur_event = line[7:].strip()
            elif line.startswith("data: ") and cur_event == "done":
                done_data = json.loads(line[6:])
                break
        assert done_data is not None, "no done event found"
        assert done_data.get("message_id"), f"done missing message_id: {done_data}"
        return f"done.message_id={done_data['message_id']}"

    t.run("6. chat/stream done 事件含真实 message_id", go)


def test_7_chat_stream_rejects_empty_query(base: str, t: TestRunner) -> None:
    def go() -> str:
        r = http(
            "POST",
            f"{base}/api/v1/chat/stream",
            json_body={"query": "", "thread_id": "smoke_t3"},
            timeout=5.0,
        )
        # Pydantic min_length=1 → 422
        assert r.status == 422, f"empty query should be 422, got {r.status}"
        return "empty query → 422"

    t.run("7. chat/stream 拒绝空 query", go)


def test_8_unified_chat_route(base: str, t: TestRunner) -> None:
    def go() -> str:
        # UnifiedChatRequest 的必填字段是 message（不是 query）；用错字段会 422，
        # 而旧断言恰好允许 422，导致处理器坏掉也测不出来
        r = http("POST", f"{base}/api/v1/unified-chat/", json_body={"message": "x"})
        assert r.status in (200, 201, 400, 422), f"unreachable: {r.status}"
        return f"POST status={r.status}"

    t.run("8. POST /api/v1/unified-chat/ 路由可达", go)


def test_9_unified_chat_stream(base: str, t: TestRunner) -> None:
    def go() -> str:
        r = http(
            "POST", f"{base}/api/v1/unified-chat/stream", json_body={"message": "y"}, timeout=5.0
        )
        assert r.status in (200, 400, 422), f"unreachable: {r.status}"
        return f"stream status={r.status}"

    t.run("9. POST /api/v1/unified-chat/stream 路由可达", go)


def test_10_feedback_rejects_random_uuid(base: str, t: TestRunner) -> None:
    """反馈接口必须验证外键链：任填 UUID 应当 4xx。"""

    def go() -> str:
        random_msg_id = str(uuid.uuid4())
        r = http(
            "POST",
            f"{base}/api/v1/feedback/",
            json_body={"message_id": random_msg_id, "rating": "up"},
            timeout=5.0,
        )
        # 必须拒绝（404/400/422 之一）
        assert r.status in (400, 404, 422), (
            f"random UUID accepted! status={r.status} body={r.text[:200]}"
        )
        return f"random UUID → {r.status}"

    t.run("10. feedback 拒绝任填 UUID（外键链）", go)


def test_11_diagnostics_route(base: str, t: TestRunner) -> None:
    def go() -> str:
        # 请求带尾斜杠的真实路由路径（SPA catch-all 会拦截无斜杠的 307）
        r = http("GET", f"{base}/api/v1/diagnostics/")
        assert r.status == 200, f"status={r.status} body={r.text[:200]}"
        body_text = r.text
        # 脱敏断言：响应中不得含完整 API Key（仅允许掩码）
        assert "sk-" not in body_text, "full api_key leaked in diagnostics"
        return "diagnostics reachable, no key leak"

    t.run("11. diagnostics 路由可达且不回显完整 Key", go)


def test_12_404_on_unknown_route(base: str, t: TestRunner) -> None:
    def go() -> str:
        r = http("GET", f"{base}/api/v1/does-not-exist")
        assert r.status == 404, f"status={r.status}"
        return "404"

    t.run("12. 未知路由返回 404", go)


def test_13_405_on_wrong_method(base: str, t: TestRunner) -> None:
    def go() -> str:
        # /health 仅 GET；POST 应 405
        r = http("POST", f"{base}/health")
        assert r.status == 405, f"status={r.status}"
        return "405"

    t.run("13. 错误方法返回 405（/health 不接受 POST）", go)


def test_14_415_on_bad_content_type(base: str, t: TestRunner) -> None:
    def go() -> str:
        r = http(
            "POST",
            f"{base}/api/v1/auth/login",
            raw_body=b"not json",
            headers={"Content-Type": "text/plain"},
        )
        assert r.status in (415, 422), f"status={r.status}"
        return f"non-JSON → {r.status}"

    t.run("14. 错误 Content-Type 拒绝（415/422）", go)


def test_15_health_does_not_echo_settings(base: str, t: TestRunner) -> None:
    """强安全断言：/health 响应不得泄漏任意配置敏感字段。"""

    def go() -> str:
        r = http("GET", f"{base}/health")
        assert r.status == 200
        body = r.json()
        forbidden = ("api_key", "DASHSCOPE_API_KEY", "secret", "token", "password")
        leaked = [k for k in forbidden if k in body]
        assert not leaked, f"sensitive fields leaked: {leaked}"
        return "forbidden fields clear"

    t.run("15. /health 不回显任何敏感字段", go)


# ---------- 端到端：上传 → 就绪 → 提问 ----------


def test_16_upload_pdf(base: str, t: TestRunner) -> None:
    """端到端：上传测试 TXT（用纯文本 fixture 避免 PyMuPDF 字体嵌入导致 PDF 18MB）。"""
    fixture = Path(__file__).parent.parent / "tests" / "smoke" / "fixtures" / "water_intro.txt"
    if not fixture.exists():
        raise AssertionError(f"fixture not found: {fixture}")

    def go() -> str:
        # 手工拼 multipart（urllib 不直接支持 file upload）
        boundary = "----smokeboundary" + uuid.uuid4().hex
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{fixture.name}"\r\n'
                f"Content-Type: text/plain\r\n\r\n"
            ).encode()
            + fixture.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode()
        )
        r = http(
            "POST",
            f"{base}/api/v1/documents/",
            raw_body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=30.0,
        )
        assert r.status in (200, 201), f"status={r.status} body={r.text[:200]}"
        body_json = r.json()
        assert "document_id" in body_json, f"missing document_id: {body_json}"
        test_16_upload_pdf._doc_id = body_json["document_id"]
        return f"doc_id={body_json['document_id'][:16]}... status={body_json['status']}"

    t.run("16. 上传 TXT → 拿到 document_id", go)


def test_17_wait_for_ready(base: str, t: TestRunner) -> None:
    """端到端：轮询直到 status=ready（最多 60s）。"""
    doc_id = getattr(test_16_upload_pdf, "_doc_id", None)
    if not doc_id:
        raise AssertionError("test_16 未运行或未保存 document_id")

    def go() -> str:
        import time

        for i in range(60):
            r = http("GET", f"{base}/api/v1/documents/{doc_id}", timeout=5.0)
            assert r.status == 200, f"status={r.status}"
            st = r.json()["status"]
            if st == "ready":
                return f"ready in {i + 1}s, chunks={r.json().get('chunk_count', 0)}"
            if st == "failed":
                err = r.json().get("error_msg", "")
                raise AssertionError(f"ingestion failed: {err}")
            time.sleep(1)
        raise AssertionError("ingestion did not become ready in 60s")

    t.run("17. ingestion 完成（status=ready）", go)


def test_18_chat_stream_e2e(base: str, t: TestRunner) -> None:
    """端到端：chat/stream 返回完整事件流，含 start/status/token/citation/done。"""

    def go() -> str:
        r = http(
            "POST",
            f"{base}/api/v1/chat/stream",
            json_body={"query": "水利工程的主要功能是什么", "thread_id": "smoke_e2e"},
            timeout=30.0,
        )
        assert r.status == 200, f"status={r.status} body={r.text[:200]}"
        text = r.text
        for evt in (
            "event: start",
            "event: status",
            "event: token",
            "event: citation",
            "event: done",
        ):
            assert evt in text, f"missing {evt!r}"
        # done 事件 message_id 必须是 uuid 形式
        import re

        m = re.search(r'event: done\r\ndata: (\{.*?"message_id".*?\})', text)
        assert m, "no done data with message_id"
        done = json.loads(m.group(1))
        mid = done["message_id"]
        assert re.match(r"^[0-9a-f-]{36}$", mid), f"bad message_id: {mid}"
        # token 数量
        token_count = text.count("event: token")
        return f"5 event types, {token_count} token events, message_id={mid[:8]}"

    t.run("18. chat/stream 端到端返回完整事件流", go)


def test_19_list_documents_contains_uploaded(base: str, t: TestRunner) -> None:
    """端到端：list 包含刚上传的 document_id。"""
    doc_id = getattr(test_16_upload_pdf, "_doc_id", None)
    if not doc_id:
        raise AssertionError("test_16 未运行")

    def go() -> str:
        r = http("GET", f"{base}/api/v1/documents/", timeout=5.0)
        assert r.status == 200
        body = r.json()
        ids = [d["document_id"] for d in body.get("documents", [])]
        assert doc_id in ids, f"uploaded doc {doc_id[:16]} not in list: {ids}"
        return f"total={body.get('total', 0)}"

    t.run("19. GET /documents/ 列表含刚上传的文档", go)


def test_20_delete_document(base: str, t: TestRunner) -> None:
    """端到端：删除后再次 GET 应 404。"""
    doc_id = getattr(test_16_upload_pdf, "_doc_id", None)
    if not doc_id:
        raise AssertionError("test_16 未运行")

    def go() -> str:
        r = http("DELETE", f"{base}/api/v1/documents/{doc_id}", timeout=10.0)
        assert r.status in (200, 204), f"delete status={r.status}"
        r2 = http("GET", f"{base}/api/v1/documents/{doc_id}", timeout=5.0)
        assert r2.status == 404, f"after delete, GET should be 404, got {r2.status}"
        return "deleted + 404 verified"

    t.run("20. DELETE 文档 → 再次 GET 返回 404", go)


# ---------- Main ----------


def run_all(base: str) -> int:
    t = TestRunner()
    # 企业级 RBAC：先注册唯一测试用户，后续请求自动带 token
    _bootstrap_auth(base)
    test_1_health_no_token(base, t)
    test_2_auth_login_local_origin(base, t)
    test_2b_auth_external_origin_rejected(base, t)
    test_3_documents_list(base, t)
    test_4_documents_create(base, t)
    test_5_chat_stream_sse(base, t)
    test_6_chat_stream_done_message_id(base, t)
    test_7_chat_stream_rejects_empty_query(base, t)
    test_8_unified_chat_route(base, t)
    test_9_unified_chat_stream(base, t)
    test_10_feedback_rejects_random_uuid(base, t)
    test_11_diagnostics_route(base, t)
    test_12_404_on_unknown_route(base, t)
    test_13_405_on_wrong_method(base, t)
    test_14_415_on_bad_content_type(base, t)
    test_15_health_does_not_echo_settings(base, t)
    test_16_upload_pdf(base, t)
    test_17_wait_for_ready(base, t)
    test_18_chat_stream_e2e(base, t)
    test_19_list_documents_contains_uploaded(base, t)
    test_20_delete_document(base, t)
    print()
    print(f"Result: {t.summary()}")
    return 0 if t.failed() == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="端到端冒烟测试（20 项）")
    # 强制 stdout 用 utf-8，避免 Windows GBK 终端打 Unicode 报错
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: S110 -- reconfigure 失败则保持原编码继续跑
        pass
    parser.add_argument(
        "--base-url",
        default=None,
        help="复用已起服务（跳过自动启动）；默认 None 即自动起停",
    )
    parser.add_argument("--port", type=int, default=8123, help="自动启动时使用的端口")
    parser.add_argument("--no-start", action="store_true", help="不自动启动后端，要求 --base-url")
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent

    if args.base_url:
        return run_all(args.base_url)

    if args.no_start:
        print("ERROR: --no-start 必须配合 --base-url 使用", file=sys.stderr)
        return 2

    print(f"Starting backend on 127.0.0.1:{args.port} ...")
    with backend_running(project_root, port=args.port) as base:
        print(f"Backend up at {base}")
        return run_all(base)


if __name__ == "__main__":
    sys.exit(main())
