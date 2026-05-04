"""
Providers package — auto-discovers and loads all provider modules.
Each provider must expose: PROVIDER_NAME, MODELS, stream_chat().
"""
import importlib
import pkgutil
import sys
import os
from pathlib import Path

_providers = {}

def _load_providers():
    """Discover and load all provider modules dynamically."""
    # Always also scan the user-facing opencode_out/python/providers directory
    import python.storage as storage_mod
    user_providers_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "providers")
    extra_paths = [str(user_providers_dir)] if os.path.isdir(user_providers_dir) else []

    for provider_dir in [Path(__file__).parent, *extra_paths]:
        for _, module_name, _ in pkgutil.iter_modules([str(provider_dir)]):
            if module_name == "__init__":
                continue
            if module_name in _providers:
                continue
            try:
                mod = importlib.import_module(f"python.providers.{module_name}")
                _providers[mod.PROVIDER_NAME] = mod
            except Exception:
                pass

_load_providers()

def get_provider(model_id: str):
    """Return the provider module that owns the given model_id."""
    for provider in _providers.values():
        for model in provider.MODELS:
            if model["id"] == model_id:
                return provider
    raise ValueError(f"No provider found for model_id: {model_id}")

def all_models() -> dict[str, list[dict]]:
    """Return {provider_name: MODELS list} for all providers."""
    return {name: mod.MODELS for name, mod in _providers.items()}