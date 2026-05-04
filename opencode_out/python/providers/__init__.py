"""
Providers package — auto-discovers and loads all provider modules.
Each provider must expose: PROVIDER_NAME, MODELS, stream_chat().
"""
import importlib
import importlib.util
import pkgutil
import sys
import os
import shutil
import logging
from pathlib import Path

_providers = {}

logger = logging.getLogger(__name__)

def _ensure_providers_copied():
    """Ensure bundled providers are copied to user-accessible folder."""
    from python.agents import get_providers_dir, _BUNDLED_PROVIDERS
    
    user_dir = Path(get_providers_dir())
    bundled_dir = Path(_BUNDLED_PROVIDERS)
    
    # Always ensure user directory exists
    user_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy all bundled provider files to user directory
    if bundled_dir.exists():
        for file_path in bundled_dir.glob("*.py"):
            if file_path.name == "__init__":
                continue
            dest_path = user_dir / file_path.name
            if not dest_path.exists():
                try:
                    shutil.copy2(file_path, dest_path)
                    logger.info(f"Copied provider: {file_path.name} -> {dest_path}")
                except Exception as e:
                    logger.warning(f"Failed to copy {file_path}: {e}")

def _load_from_file(module_name: str, file_path: str):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _load_providers():
    # First ensure providers are copied to user directory
    _ensure_providers_copied()
    
    from python.agents import get_providers_dir

    bundled_dir = Path(__file__).parent
    user_dir = Path(get_providers_dir())

    # Load from both directories
    for provider_dir in [bundled_dir, user_dir]:
        if not provider_dir.exists():
            continue
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
                    logger.info(f"Loaded provider: {mod.PROVIDER_NAME} with {len(mod.MODELS)} models")
            except Exception as e:
                logger.error(f"Failed to load provider {module_name}: {e}")

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