from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://sentinel:sentinel@db:5432/sentinel_ai"
    cors_origins: str = "http://localhost:5173"
    rss_feed_url: str = "https://news.google.com/rss/search?q=%E5%8E%9F%E7%A5%9E+OR+%E5%B4%A9%E5%9D%8F%EF%BC%9A%E6%98%9F%E7%A9%B9%E9%93%81%E9%81%93+OR+%E7%BB%9D%E5%8C%BA%E9%9B%B6+OR+%E9%B8%A3%E6%BD%AE+OR+%E6%98%8E%E6%97%A5%E6%96%B9%E8%88%9F+OR+%E7%BB%88%E6%9C%AB%E5%9C%B0+OR+%E5%B0%91%E5%A5%B3%E5%89%8D%E7%BA%BF2+OR+%E7%8E%8B%E8%80%85%E8%8D%A3%E8%80%80+OR+%E7%AC%AC%E4%BA%94%E4%BA%BA%E6%A0%BC+OR+%E6%97%A0%E7%95%8F%E5%A5%91%E7%BA%A6+OR+%E4%B8%89%E8%A7%92%E6%B4%B2%E8%A1%8C%E5%8A%A8&hl=zh-CN&gl=CN&ceid=CN%3Azh-Hans"
    rss_source_name: str = "Google News · Target Games"
    topics_config_path: Path = Path("/app/config/topics.yaml")
    sources_config_path: Path = Path("/app/config/sources.yaml")
    bilibili_cookie: str = ""
    bilibili_videos_per_account: int = 5
    bilibili_comments_per_video: int = 15
    bilibili_comment_pages: int = 25
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
