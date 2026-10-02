from pathlib import Path
from dotenv import load_dotenv
from config.redis_cfg import RedisConfig
from config.hermes_cfg import HermesConfig

# Load environment variables once at root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

class Settings:
    ROOT_DIR: Path = ROOT_DIR
    WORKSPACE_DIR: Path = ROOT_DIR / "workspace"
    redis: RedisConfig = RedisConfig()
    hermes: HermesConfig = HermesConfig()

settings = Settings()