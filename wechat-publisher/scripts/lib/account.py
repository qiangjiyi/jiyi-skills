"""Account resolution from CLI / source / env."""

from __future__ import annotations

import re

from .errors import PublishError


def env_key(account: str, suffix: str) -> str:
    """Map an account alias to its env var name: 'my-account' → 'WECHAT_MY_ACCOUNT_APP_ID'."""
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", account).strip("_").upper()
    return f"WECHAT_{normalized}_{suffix}"


def configured_accounts(env: dict[str, str]) -> list[str]:
    raw = env.get("WECHAT_ACCOUNTS", "")
    accounts = [x.strip() for x in raw.split(",") if x.strip()]
    if accounts:
        return accounts
    if env.get("WECHAT_APP_ID") or env.get("WECHAT_ACCESS_TOKEN"):
        return ["default"]
    return []


def resolve_account(cli_account: str | None, source: dict, env: dict[str, str]) -> str:
    if cli_account:
        return cli_account
    source_account = source.get("account")
    if isinstance(source_account, str) and source_account.strip():
        return source_account.strip()
    accounts = configured_accounts(env)
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise PublishError("no WeChat account configured in env")
    raise PublishError(
        f"multiple accounts configured ({', '.join(accounts)}); set source account or --account"
    )


def account_value(env: dict[str, str], account: str, suffix: str) -> str:
    """Get WECHAT_<account>_<suffix>, falling back to WECHAT_<suffix> for 'default'."""
    if account != "default":
        value = env.get(env_key(account, suffix), "")
        if value:
            return value
    return env.get(f"WECHAT_{suffix}", "")
