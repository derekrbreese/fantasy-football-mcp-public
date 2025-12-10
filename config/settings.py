"""
Configuration settings for the Fantasy Football MCP server.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field

# Find project root (where .env file is located)
# This module can be imported from various places, so we need to find the project root
# Try to find the project root by looking for common markers
_caller_file = Path(__file__)
if _caller_file.parent.name == "config":
    PROJECT_ROOT = _caller_file.parent.parent.absolute()
else:
    # Fallback: assume we're at the root or one level down
    PROJECT_ROOT = _caller_file.parent.absolute()
ENV_FILE_PATH = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Yahoo API Configuration
    yahoo_client_id: str = Field(..., env="YAHOO_CLIENT_ID")
    yahoo_client_secret: str = Field(..., env="YAHOO_CLIENT_SECRET")

    # Cache Configuration
    cache_dir: Path = Field(default=Path("./.cache"), env="CACHE_DIR")
    cache_ttl_seconds: int = Field(default=3600, env="CACHE_TTL_SECONDS")

    # API Rate Limiting
    yahoo_api_rate_limit: int = Field(default=100, env="YAHOO_API_RATE_LIMIT")
    yahoo_api_rate_window_seconds: int = Field(default=3600, env="YAHOO_API_RATE_WINDOW_SECONDS")

    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_file: Path = Field(default=Path("./logs/yahoo_fantasy_football.log"), env="LOG_FILE")

    # MCP Server Configuration
    mcp_server_name: str = Field(default="yahoo-fantasy-football", env="MCP_SERVER_NAME")
    mcp_server_version: str = Field(default="1.0.0", env="MCP_SERVER_VERSION")

    # Parallel Processing
    max_workers: int = Field(default=10, env="MAX_WORKERS")
    async_timeout_seconds: int = Field(default=30, env="ASYNC_TIMEOUT_SECONDS")

    # Feature Flags
    enable_advanced_stats: bool = Field(default=True, env="ENABLE_ADVANCED_STATS")
    enable_weather_data: bool = Field(default=True, env="ENABLE_WEATHER_DATA")
    enable_injury_reports: bool = Field(default=True, env="ENABLE_INJURY_REPORTS")

    # Yahoo OAuth Configuration
    yahoo_redirect_uri: str = Field(default="https://localhost:8090", env="YAHOO_REDIRECT_URI")
    yahoo_callback_port: int = Field(default=8090, env="YAHOO_CALLBACK_PORT")
    yahoo_callback_host: str = Field(default="localhost", env="YAHOO_CALLBACK_HOST")

    class Config:
        env_file = str(ENV_FILE_PATH)  # Use absolute path to project root .env
        env_file_encoding = "utf-8"
        case_sensitive = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Ensure log directory exists
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
