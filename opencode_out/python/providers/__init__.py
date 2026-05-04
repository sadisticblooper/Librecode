"""
Providers package — auto-discovers and loads all provider modules.
Each provider must expose: PROVIDER_NAME, MODELS, stream_chat().
"""
import importlib
import pkgutil
import sys
from pathlib import Path

_providers = {}

def _load_providers():
    """Discover and load all provider modules dynamically."""
    provider_dir = Path(__file__).parent
    for _, module_name, _ in pkgutil.iter_modules([str(provider_dir)]):
        if module_name == "__init__":
            continue
        # Import using the full python.providers.{name} path
        mod = importlib.import_module(f"python.providers.{module_name}")
        _providers[mod.PROVIDER_NAME] = mod

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