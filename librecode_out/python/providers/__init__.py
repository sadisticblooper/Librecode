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

def _provider_for_model(model_id: str):
    """Return the provider module for a model, or None."""
    _ensure_loaded()
    for p in _providers.values():
        for m in p.MODELS:
            if m["id"] == model_id:
                return p
    return None

def get_provider(model_id):
    p = _provider_for_model(model_id)
    if p is None:
        raise ValueError(f"No provider for model: {model_id}")
    return p

def all_models():
    _ensure_loaded()
    return {name: mod.MODELS for name, mod in _providers.items()}

def get_model_ctx(model_id: str, default: int = 128_000) -> int:
    """Return the context window size for a given model id."""
    _ensure_loaded()
    for p in _providers.values():
        for m in p.MODELS:
            if m["id"] == model_id:
                return m.get("ctx", default)
    return default

def compaction_buffer(ctx: int) -> int:
    """Variable safety buffer matching OpenCode's approach."""
    if ctx >= 1_000_000:
        return 30_000
    if ctx >= 200_000:
        return 20_000
    if ctx >= 100_000:
        return 15_000
    return 10_000  # 30k-60k models

def get_reasoning_effort(model_id: str) -> str | None:
    """
    Return the reasoning_effort value to send for this model, or None if
    the model doesn't use the reasoning_effort API field at all.
    Providers declare this via MODEL_EFFORT = {model_id: "max", ...}.
    Models absent from MODEL_EFFORT get None (field not sent).
    """
    p = _provider_for_model(model_id)
    if p is None:
        return None
    return getattr(p, "MODEL_EFFORT", {}).get(model_id)

def needs_reasoning_passback(model_id: str) -> bool:
    """
    True if the model requires reasoning_content to be echoed back in every
    assistant message during multi-turn conversations (e.g. DeepSeek V4).
    Providers declare this via NEEDS_REASONING_PASSBACK = {"model-id", ...}.
    """
    p = _provider_for_model(model_id)
    if p is None:
        return False
    return model_id in getattr(p, "NEEDS_REASONING_PASSBACK", set())
