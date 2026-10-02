import os
from pydantic import BaseModel, Field

class RedisConfig(BaseModel):
    host: str = Field(default_factory=lambda: os.getenv("REDIS_HOST", "localhost"))
    port: int = Field(default_factory=lambda: int(os.getenv("REDIS_PORT", 6379)))
    db: int = Field(default=0)
    pubsub_channel: str = "agent_pipeline_events"

    @property
    def url(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"