"""Deterministic safety policy for read-only UI exploration."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEPTHS = ("quick", "standard", "thorough")
_MUTATING_ACTIONS = re.compile(
    r"\b(submit|save|execute|approve|delete|remove|import|export|upload|publish|confirm)\b|"
    r"提交|保存|执行|批准|审批|删除|导入|导出|上传|发布|确认",
    re.IGNORECASE,
)
_SENSITIVE_PURPOSES = re.compile(
    r"password|passwd|secret|token|credential|cookie|account|user.?name|email|phone|"
    r"密码|密钥|令牌|凭证|账号|账户|用户名|邮箱|手机",
    re.IGNORECASE,
)
_NETWORK_ERRORS = re.compile(
    r"failed to fetch|network.?error|request aborted|net::|websocket|connection (?:failed|refused)|"
    r"网络(?:错误|异常|失败)|请求(?:中止|失败)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExplorationPolicy:
    """Policy decisions shared by the browser runner and focused tests."""

    depth: str = "standard"

    def __post_init__(self) -> None:
        if self.depth not in DEPTHS:
            raise ValueError(f"Unsupported exploration depth: {self.depth!r}")

    @property
    def synthetic_probing_enabled(self) -> bool:
        return self.depth == "thorough"

    def allows_action(self, *, control_type: str, label: str) -> bool:
        if control_type.lower() in {"button", "submit", "menu_item"}:
            return _MUTATING_ACTIONS.search(label.strip()) is None
        return True

    def allows_key(self, key: str, *, synthetic_probe: bool = False) -> bool:
        return not (synthetic_probe and key.casefold() in {"enter", "return"})

    def is_probe_candidate(
        self,
        *,
        input_type: str,
        value: str,
        purpose: str,
        disabled: bool = False,
        readonly: bool = False,
    ) -> bool:
        return (
            self.synthetic_probing_enabled
            and input_type.casefold() in {"", "text", "search"}
            and value == ""
            and bool(purpose.strip())
            and not disabled
            and not readonly
            and _SENSITIVE_PURPOSES.search(purpose) is None
        )

    def keep_observation(
        self,
        text: str,
        *,
        dynamic: bool = False,
        user_supplied: bool = False,
        credential: bool = False,
    ) -> bool:
        normalized = " ".join(text.split())
        return bool(normalized) and not (
            dynamic
            or user_supplied
            or credential
            or _NETWORK_ERRORS.search(normalized)
        )

    def synthetic_probe_decision(self, *, network_isolation_available: bool) -> tuple[bool, str | None]:
        if not self.synthetic_probing_enabled:
            return False, None
        if not network_isolation_available:
            return (
                False,
                "Synthetic input probe skipped: active Playwright network isolation is unavailable.",
            )
        return True, None
