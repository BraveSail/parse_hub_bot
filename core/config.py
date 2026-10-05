import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url

load_dotenv()


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(...)
    api_id: str = Field(...)
    api_hash: str = Field(...)
    bot_proxy: str | None = Field(default=None)
    data_path: Path = Field(default=Path("data"))
    language: str = Field(default="zh-hans")
    cache_max_entries: int = Field(default=30000, ge=0, description="缓存最大条数, 0 为不限制")
    cache_disabled: bool = Field(default=False, description="禁用缓存")

    rate_limit_enabled: bool = Field(default=False, description="启用解析速率限制")
    rate_limit_burst: int = Field(default=5, ge=0, description="突发请求阈值, 0 为不限制")
    rate_limit_burst_window: float = Field(default=60, gt=0, description="突发请求统计窗口, 单位秒")
    rate_limit_cooldown: float = Field(default=180, gt=0, description="触发限速后的冷却时间, 单位秒")
    rate_limit_throttle: int = Field(default=1, ge=0, description="冷却期内允许解析次数, 0 为禁止解析")
    rate_limit_throttle_window: float = Field(default=5, gt=0, description="冷却期内允许解析次数对应的统计窗口, 单位秒")
    # chat_id 在 RATE_LIMIT_BURST_WINDOW 秒内达到 RATE_LIMIT_BURST 次解析后，进入 RATE_LIMIT_COOLDOWN 秒冷却期;
    # 冷却期内每 RATE_LIMIT_THROTTLE_WINDOW 秒最多允许 RATE_LIMIT_THROTTLE 次解析.

    download_dir: Path = Path("downloads")

    database_url: str = Field(default="sqlite+aiosqlite:///data/db/database.db")

    guest_whitelist_group_id: int = Field(
        default=0,
        description=(
            "白名单群 ID: 只有与 bot 同在**这一个**群的用户才能使用 inline 与 guest 查询; "
            "0 表示不限制。只查这一个群 (不遍历 bot 所在的全部群), 所以不会因群多而 flood。"
        ),
    )

    #: 初始白名单: 可用管理命令 (/add /list /purge) 的用户 ID, **逗号分隔**。
    #:
    #: 这是"永远允许"的一层 —— 写在 .env 里, 不用改代码; 运行时加的人存 DB 表
    #: ``admin_users``, 两处取并集。**第一人必须从这里来**, 否则 /add 谁也调不了。
    admin_users: str = Field(
        default="",
        description="初始白名单用户 ID (逗号分隔), 之后用 /add 追加",
    )

    debug: bool = Field(default=False)
    debug_skip_cleanup: bool = Field(default=False, description="跳过资源清理")

    demo_mode: bool = Field(default=False, description="启用演示模式")

    @property
    def admin_user_ids(self) -> list[int]:
        """解析 ``admin_users``: 逗号或空白分隔, 非数字项跳过。

        宽容解析 —— 配置写错一个片段不该让整个 bot 起不来 (与"平台配置留空是合法状态"
        同一原则)。
        """
        out: list[int] = []
        for part in re.split(r"[,\s]+", self.admin_users or ""):
            part = part.strip()
            if part.lstrip("-").isdigit():
                out.append(int(part))
        return out

    def model_post_init(self, __context: Any) -> None:
        """模型初始化后的操作"""
        self.sessions_path.mkdir(parents=True, exist_ok=True)
        self.config_path.mkdir(parents=True, exist_ok=True)

        url = make_url(self.database_url)
        if url.get_backend_name() == "sqlite" and url.database:
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    @property
    def sessions_path(self) -> Path:
        return self.data_path / "sessions"

    @property
    def config_path(self) -> Path:
        return self.data_path / "config"

    @property
    def bot_session_name(self) -> str:
        return f"bot_{self.bot_token.split(':')[0]}"

    @field_validator("data_path", mode="before")
    @classmethod
    def data_path_init(cls, v: str | Path) -> Path:
        p = Path(v) if isinstance(v, str) else v
        p.mkdir(exist_ok=True, parents=True)
        return p


class WatchdogSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        env_prefix="WD_",
    )
    is_running: bool = Field(default=False)
    """运行中"""
    restart_count: int = Field(default=0)
    """重启次数"""
    disconnect_count: int = Field(default=0)
    """断开连接次数"""
    max_disconnect_count: int = Field(default=3)
    """最大断开连接次数, 超过后重启"""
    remove_session_after_restart: int = Field(default=3)
    """重启失败几次后删除会话文件"""
    max_restart_count: int = Field(default=6)
    """意外断开连接时，最大重启次数"""
    exit_flag: bool = Field(default=False)
    """退出标志"""

    def update_bot_restart_count(self) -> None:
        self.restart_count += 1
        os.environ["WD_RESTART_COUNT"] = str(self.restart_count)

    def reset_bot_restart_count(self) -> None:
        self.restart_count = 0
        os.environ["WD_RESTART_COUNT"] = "0"

    def update_bot_disconnect_count(self) -> None:
        self.disconnect_count += 1
        os.environ["WD_DISCONNECT_COUNT"] = str(self.disconnect_count)

    def reset_bot_disconnect_count(self) -> None:
        self.disconnect_count = 0
        os.environ["WD_DISCONNECT_COUNT"] = "0"


bs = BotSettings()  # type: ignore[call-arg]
ws = WatchdogSettings()
