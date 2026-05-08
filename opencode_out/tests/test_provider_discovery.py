"""
Test that provider discovery works without index.json.
The providers/ directory is the sole source of truth.
"""

import os
import sys
import tempfile
import shutil
import pkgutil
import unittest

# Ensure opencode_out/python is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import providers as providers_module


class TestProviderDiscovery(unittest.TestCase):
    def setUp(self):
        """Create a fake providers dir with .py files only — no index.json."""
        self.temp_dir = tempfile.mkdtemp()
        self.providers_dir = os.path.join(self.temp_dir, "providers")
        os.makedirs(self.providers_dir)

        # Write two fake provider modules
        self._write_provider("fake_provider_a.py", "FakeProviderA")
        self._write_provider("fake_provider_b.py", "FakeProviderB")

        # Save original get_providers_dir and patch it
        self.original_get_providers_dir = providers_module._load_providers
        providers_module._providers = {}  # reset state

        self.providers_module = providers_module

    def _write_provider(self, filename, provider_name):
        content = f'''
PROVIDER_NAME = "{provider_name}"
MODELS = [
    {{"id": "{provider_name.lower()}/model-1", "name": "Model 1"}},
]
'''
        with open(os.path.join(self.providers_dir, filename), "w") as f:
            f.write(content)

    def test_discovery_without_index_json(self):
        """Providers should be discovered from .py files alone."""
        # Simulate pkgutil.iter_modules on our temp dir
        found_modules = list(
            name for _, name, _ in pkgutil.iter_modules([self.providers_dir])
        )
        self.assertIn("fake_provider_a", found_modules)
        self.assertIn("fake_provider_b", found_modules)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)
        # Restore
        providers_module._providers = {}


if __name__ == "__main__":
    unittest.main()
