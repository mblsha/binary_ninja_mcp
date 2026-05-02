from dataclasses import dataclass
from typing import Optional


@dataclass
class ServerConfig:
    host: str = "localhost"
    port: int = 9009
    fallback_ports: tuple[int, ...] = (
        9000,
        9001,
        9002,
        9003,
        9004,
        9005,
        9006,
        9007,
        9008,
    )
    debug: bool = False
    auto_start: bool = True  # Add auto-start configuration


@dataclass
class BinaryNinjaConfig:
    api_version: Optional[str] = None
    log_level: str = "INFO"


class Config:
    def __init__(self):
        self.server = ServerConfig()
        self.binary_ninja = BinaryNinjaConfig()
