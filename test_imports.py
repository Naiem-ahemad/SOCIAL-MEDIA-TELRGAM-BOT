import os, importlib

def test_all_imports():
    base = os.path.dirname(__file__)
    for root, _, files in os.walk(base):
        for f in files:
            if f.endswith(".py") and not f.startswith("test_"):
                path = os.path.join(root, f)
                rel = os.path.relpath(path, base)
                mod = rel[:-3].replace(os.sep, ".")
                try:
                    importlib.import_module(mod)
                except Exception as e:
                    raise AssertionError(f"❌ Failed to import {mod}: {e}")
