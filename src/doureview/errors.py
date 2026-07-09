class DouReviewError(Exception):
    """所有 DouReview 异常的基类"""
    pass


class NotAGitRepoError(DouReviewError):
    """当前目录不是 git 仓库"""
    pass


class EmptyDiffError(DouReviewError):
    """diff 为空，没有变更内容"""
    pass


class DiffTooLargeError(DouReviewError):
    """diff 超过行数上限，已被截断（非致命，仅告警）"""
    pass


class LLMError(DouReviewError):
    """LLM 调用失败（超时、网络等）"""
    pass


class LLMAuthError(LLMError):
    """API Key 无效或未配置"""
    pass


class LLMRateLimitError(LLMError):
    """API 速率限制"""
    pass


class ReportParseError(DouReviewError):
    """LLM 响应无法解析为结构化报告"""
    pass
