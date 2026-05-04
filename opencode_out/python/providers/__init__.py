"""
Provider system for OpenCode.

Auto-scans the providers/ folder and exposes:
  - get_provider(model_id) -> provider module
  - all_models() -> {provider_name: [models]}
"""

import os
import importlib
import importlib.util
import sys

_PROVIDERS_DIR = os.path.dirname(os.path.abspath(__file__))
_model_to_provider = {}
_all_models = {}


def _scan_providers():
    global _model_to_provider, _all_models
    _model_to_provider = {}
    _all_models = {}

    for fname in os.listdir(_PROVIDERS_DIR):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        if fname == "__init__.py":
            continue

        mod_name = fname[:-3]
        fpath = os.path.join(_PROVIDERS_DIR, fname)

        spec = importlib.util.spec_from_file_location(mod_name, fpath)
        if spec is None or spec.loader is None:
            continue

        try:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        except Exception:
            continue

        if not hasattr(mod, "MODELS"):
            continue

        provider_name = getattr(mod, "PROVIDER_NAME", mod_name)
        _all_models[provider_name] = mod.MODELS

        for m in mod.MODELS:
            _model_to_provider[m["id"]] = mod


_scan_providers()


def get_provider(model_id: str):
    """Return the provider module for a given model ID, or None."""
    return _model_to_provider.get(model_id)


def all_models():
    """Return {provider_name: [models]} grouped by provider."""
    return dict(_all_models)


def reload():
    """Re-scan providers (call after adding new provider files)."""
    _scan_providers()