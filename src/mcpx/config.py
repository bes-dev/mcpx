from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, Field

CONFIG_DIR = Path.home() / ".config" / "mcpx"
CONFIG_FILE = CONFIG_DIR / "config.json"


class ServerConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_file: str | None = None
    timeout: int = 30


class LLMConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"


class AppConfig(BaseModel):
    servers: dict[str, ServerConfig] = Field(default_factory=dict)
    global_timeout: int = 30
    cache_schemas: bool = True
    llm: LLMConfig = Field(default_factory=LLMConfig)


class ConfigManager:
    def __init__(self) -> None:
        self._config = self._load()

    @property
    def config(self) -> AppConfig:
        return self._config

    def _load(self) -> AppConfig:
        if CONFIG_FILE.exists():
            return AppConfig.model_validate_json(CONFIG_FILE.read_text())
        return AppConfig()

    def _save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(self._config.model_dump_json(indent=2))

    def add_server(self, alias: str, server: ServerConfig) -> None:
        self._config.servers[alias] = server
        self._save()

    def remove_server(self, alias: str) -> bool:
        if alias in self._config.servers:
            del self._config.servers[alias]
            self._save()
            return True
        return False

    def get_server(self, alias: str) -> ServerConfig | None:
        return self._config.servers.get(alias)

    def list_servers(self) -> dict[str, ServerConfig]:
        return self._config.servers

    def set_llm(self, llm: LLMConfig) -> None:
        self._config.llm = llm
        self._save()

    def resolve_env(self, server: ServerConfig) -> dict[str, str]:
        """Merge env vars: inline env > local .env > configured env_file."""
        env: dict[str, str] = {}
        if server.env_file:
            env.update(dotenv_values(server.env_file))
        local_env = Path(".env")
        if local_env.exists():
            env.update(dotenv_values(local_env))
        env.update(server.env)
        return env
