"""Environment configuration. Never serialize this module's settings to clients."""
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_key: str
    publishable_key: str
    encryption_key: str
    environment: str
    executor: str
    origins: tuple[str, ...]
    secure_cookies: bool
    free_only: bool
    nemotron_testing: bool
    resource_budgets_enabled: bool
    runtime_version: str

    @property
    def configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key and self.publishable_key)


@lru_cache
def settings() -> Settings:
    hosted = os.getenv("VERCEL") == "1"
    return Settings(
        supabase_url=os.getenv("SUPABASE_URL", os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")).rstrip("/"),
        supabase_key=os.getenv("SUPABASE_SECRET_KEY", ""),
        publishable_key=os.getenv("SUPABASE_PUBLISHABLE_KEY", os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "")),
        encryption_key=os.getenv("MODEL_ENCRYPTION_KEY", ""),
        environment=os.getenv("FORMA_ENVIRONMENT", os.getenv("VERCEL_ENV", "development")),
        executor="vercel",
        origins=tuple(x.strip().rstrip("/") for x in os.getenv("FORMA_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if x.strip()),
        secure_cookies=hosted or os.getenv("FORMA_SECURE_COOKIES") == "true",
        free_only=os.getenv("OPENROUTER_FREE_ONLY", "false") == "true",
        nemotron_testing=os.getenv("OPENROUTER_NEMOTRON_TESTING") == "true",
        # Test deployments may opt out of Forma's internal monthly reservation
        # ledger. Provider and platform quotas still apply; correctness and
        # cleanup guards remain active.
        resource_budgets_enabled=os.getenv("FORMA_RESOURCE_BUDGETS", "true").lower() == "true",
        runtime_version=os.getenv("CAD_RUNTIME_VERSION", "forma-python-v2"),
    )
