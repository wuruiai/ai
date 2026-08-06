"""统一的对外错误文案与稳定错误码（G10.6 异常脱敏）。

内部异常细节（路径、连接串、供应商错误原文、堆栈等）绝不外泄给客户端——
对端只能看到稳定 code + 通用文案；真实异常由服务端 `logger.exception` 记录，
既保证可诊断，又不给攻击者提供侦察信息。
"""

# 稳定错误码：orchestrator / Agent 链路未预期异常
ERROR_CODE_ORCHESTRATOR = "ORCHESTRATOR_ERROR"

# 通用对外文案（不透传异常原文）
GENERIC_ERROR_MESSAGE = "系统处理请求时遇到问题，请稍后再试。"
