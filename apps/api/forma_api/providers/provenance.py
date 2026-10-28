"""Response identity, excluding credentials and provider-private headers."""


def response_provenance(config: dict, body: dict, output_tokens: int) -> dict:
    return {
        "requestedModel": config["model_id"],
        "resolvedModel": body.get("model"),
        "provider": config.get("provider", "openrouter"),
        "resolvedProvider": body.get("provider"),
        "endpoint": config.get("base_url") or "https://openrouter.ai/api/v1",
        "configVersion": config.get("version"),
        "responseId": body.get("id"),
        "outputTokenLimit": output_tokens,
        "contextLength": config.get("context_length"),
        "usage": body.get("usage") or {},
        "identityKnown": bool(body.get("model")),
    }


def require_qualified_identity(config: dict, result: dict) -> dict:
    """Prevent a qualified connection from silently executing a substituted model."""
    if result.get("protocolRetry") and not result.get("calls"):
        # Malformed provider output is an internal no-action correction turn.
        # It does not constitute an executable response from a substitute model.
        return result
    if not (config.get("qualification") or {}).get("identityStable"):
        return result
    provenance = result.get("provenance") or {}
    requested = provenance.get("requestedModel")
    resolved = provenance.get("resolvedModel")
    if not requested or requested != resolved:
        from .openrouter import ModelFailure

        raise ModelFailure(
            "model_identity",
            "The provider did not return the qualified model identity. The run was preserved for review.",
        )
    return result
