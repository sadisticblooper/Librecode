import importlib.util
import pkgutil
import os

_providers = {}
_loaded = False

def _load_from_file(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _load_providers():
    from python.agents import get_providers_dir
    d = get_providers_dir()
    for _, name, _ in pkgutil.iter_modules([d]):
        fp = os.path.join(d, f"{name}.py")
        try:
            mod = _load_from_file(f"provider_{name}", fp)
            if mod and hasattr(mod, "PROVIDER_NAME") and hasattr(mod, "MODELS"):
                _providers[mod.PROVIDER_NAME] = mod
        except Exception:
            pass

def _ensure_loaded():
    global _loaded
    if not _loaded:
        _loaded = True
        _load_providers()

def get_provider(model_id):
    _ensure_loaded()
    for p in _providers.values():
        for m in p.MODELS:
            if m["id"] == model_id:
                return p
    raise ValueError(f"No provider for model: {model_id}")

def all_models():
    _ensure_loaded()
    return {name: mod.MODELS for name, mod in _providers.items()}
