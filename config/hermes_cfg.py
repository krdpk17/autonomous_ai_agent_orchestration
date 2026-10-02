import os
from pydantic import BaseModel, Field

class HermesConfig(BaseModel):
    base_url: str = Field(default_factory=lambda: os.getenv("HERMES_BASE_URL", "https://openrouter.ai/api/v1"))
    api_key: str = Field(default_factory=lambda: os.getenv("HERMES_API_KEY", ""))
    model_name: str = Field(default_factory=lambda: os.getenv("HERMES_MODEL", "nousresearch/hermes-3-llama-3.1-8b"))
    temperature: float = 0.2
    max_tokens: int = 4096