
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    RELABEL_BASE: str = os.getenv("RELABEL_BASE", "/srv/relabel")
    RELABEL_DATA: str = os.getenv("RELABEL_DATA", "/srv/relabel/data")
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    SECRET_KEY: str = os.getenv("SECRET_KEY", "relabel_dev_secret")
    SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "relabel_sess")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+psycopg://relabel:relabel@localhost:5432/relabel")
    RELABEL_PEPPER_FILE: str | None = os.getenv("RELABEL_PEPPER_FILE")
    RELABEL_PEPPER: str | None = os.getenv("RELABEL_PEPPER")
    RELABEL_ENABLE_DANGEROUS: int = int(os.getenv("RELABEL_ENABLE_DANGEROUS", "0"))
    # frontend dist directory
    FRONTEND_DIST: str = os.getenv("FRONTEND_DIST", os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../web/dist")))

    class Config:
        env_file = ".env"

settings = Settings()
