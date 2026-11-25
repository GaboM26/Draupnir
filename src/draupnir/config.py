"""Runtime settings. The only module that reads environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    sec_user_agent: str | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "draupnir.sqlite"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    def require_sec_user_agent(self) -> str:
        """SEC fair-access policy requires a contact User-Agent on every request."""
        if not self.sec_user_agent:
            raise RuntimeError(
                "DRAUPNIR_SEC_USER_AGENT is not set (e.g. 'Draupnir you@example.com'); see .env.example"
            )
        return self.sec_user_agent


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DRAUPNIR_DATA_DIR", "data")).resolve(),
        sec_user_agent=os.environ.get("DRAUPNIR_SEC_USER_AGENT") or None,
    )
