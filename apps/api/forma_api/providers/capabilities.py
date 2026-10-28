"""Capabilities and identity rules for supported model transports.

The endpoint identifies the transport. Model competence is established by the
engineering evaluation, not inferred from this protocol profile.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProviderCapabilities:
    name: str
    web_search: bool
    requires_explicit_context: bool
    pinned_model: bool


def profile(config: dict) -> ProviderCapabilities:
    provider = config.get("provider", "openrouter")
    if provider == "openrouter":
        return ProviderCapabilities("openrouter", True, False, True)
    host = urlsplit(config.get("base_url") or "").hostname
    if provider == "openai_compatible" and host in {
        "api.tokenfactory.nebius.com",
        "api.tokenfactory.us-central1.nebius.com",
    }:
        return ProviderCapabilities("nebius", False, True, True)
    return ProviderCapabilities("openai_compatible", False, True, True)


def validate_candidate(config: dict) -> None:
    """Reject router aliases and unknown input capacity before qualification."""
    model_id = str(config.get("model_id") or "").strip()
    if not model_id or model_id in {"openrouter/auto", "openrouter/free"}:
        raise ValueError("Select a specific model, not an automatic model router.")
    capabilities = profile(config)
    if capabilities.requires_explicit_context and not config.get("context_length"):
        raise ValueError("Enter the selected model's documented context length.")


def validate_qualification(config: dict, receipts: list[dict]) -> dict:
    """Require a stable resolved model on both turns of a staged connection test."""
    requested = str(config["model_id"])
    resolved = [receipt.get("resolvedModel") for receipt in receipts]
    if len(resolved) != 2 or any(not isinstance(value, str) or not value for value in resolved):
        raise ValueError("The provider did not identify the model in both responses.")
    if any(value != requested for value in resolved):
        raise ValueError(
            "The provider returned a different model than the selected one. "
            "Choose its exact model ID and test again."
        )
    return {
        "profile": profile(config).name,
        "requestedModel": requested,
        "resolvedModel": requested,
        "identityStable": True,
    }
