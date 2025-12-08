from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class Settings(BaseSettings):
    # Default to Railway internal/public URLs; can be overridden por .env ou variáveis de ambiente
    DATABASE_URL: str = "postgresql://postgres:eGToQQQrasBZetMsloLDrKdPMvtYctNv@postgres.railway.internal:5432/railway"
    DATABASE_PUBLIC_URL: str | None = "postgresql://postgres:eGToQQQrasBZetMsloLDrKdPMvtYctNv@centerbeam.proxy.rlwy.net:10079/railway"
    JWT_SECRET: str = "a_very_secret_key_that_should_be_changed"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    
    # Railway environment detection
    ENVIRONMENT: str = "development"
    PORT: int = 8000

    model_config = SettingsConfigDict(env_file=".env")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Auto-detect Railway environment
        if os.getenv("RAILWAY_ENVIRONMENT"):
            self.ENVIRONMENT = "production"
            self.PORT = int(os.getenv("PORT", 8000))
        
        # Preferir DATABASE_PUBLIC_URL em ambiente local (fora da Railway)
        if self.ENVIRONMENT != "production":
            public_url = os.getenv("DATABASE_PUBLIC_URL") or self.DATABASE_PUBLIC_URL
            if public_url:
                self.DATABASE_URL = public_url

        # Ensure DATABASE_URL uses asyncpg
        if self.DATABASE_URL and not self.DATABASE_URL.startswith("postgresql+asyncpg://"):
            if self.DATABASE_URL.startswith("postgresql://"):
                self.DATABASE_URL = self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

settings = Settings()
