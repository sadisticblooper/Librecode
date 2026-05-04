"""
Providers package — auto-discovers and loads all provider modules.
Each provider must expose: PROVIDER_NAME, MODELS, stream_chat().
"""
import importlib
import importlib.util
import pkgutil
import sys
import os
from pathlib import Path

_providers = {}

def _load_from_file(module_name: str, file_path: str):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _load_providers():
    from python.agents import get_providers_dir

    bundled_dir = Path(__file__).parent
    user_dir = Path(get_providers_dir())

    for provider_dir in [bundled_dir, user_dir]:
        for _, module_name, _ in pkgutil.iter_modules([str(provider_dir)]):
            if module_name == "__init__":
                continue
            if module_name in _providers:
                continue
            file_path = os.path.join(str(provider_dir), f"{module_name}.py")
            try:
                if provider_dir == bundled_dir:
                    mod = importlib.import_module(f"python.providers.{module_name}")
                else:
                    mod = _load_from_file(f"user_provider_{module_name}", file_path)
                if mod and hasattr(mod, "PROVIDER_NAME") and hasattr(mod, "MODELS"):
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