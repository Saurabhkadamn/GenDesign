"""Stable model gateway used by graph/domain code.

Provider-specific transport is selected from the saved connection, so graph
nodes do not need to know whether a request is sent to OpenRouter or another
OpenAI-compatible endpoint.
"""
from .providers.openrouter import *  # noqa: F401,F403
