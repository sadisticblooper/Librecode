"""
Providers package — auto-discovers and loads provider modules from
~/opencode/python/providers/ (copies bundled files there on first run).
Each provider must expose: PROVIDER_NAME, MODELS, stream_chat().
"""
import importlib.util
import pkgutil
import os

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
    providers_dir = get_providers_dir()

    for _, module_name, _ in pkgutil.iter_modules([providers_dir]):
        if module_name == "__init__":
            continue
        file_path = os.path.join(providers_dir, f"{module_name}.py")
        try:
            mod = _load_from_file(f"provider_{module_name}", file_path)
            if mod and hasattr(mod, "PROVIDER_NAME") and hasattr(mod, "MODELS"):
                _providers[mod.PROVIDER_NAME] = mod
        except Exception:
            pass

_load_providers()

def get_provider(model_id: str):
    for provider in _providers.values():
        for model in provider.MODELS:
            if model["id"] == model_id:
                return provider
    raise ValueError(f"No provider found for model_id: {model_id}")

def all_models() -> dict[str, list[dict]]:
    return {name: mod.MODELS for name, mod in _providers.items()}
