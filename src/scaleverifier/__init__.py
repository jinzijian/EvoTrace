"""Backward-compatible namespace for the former ScaleVerifier package name."""

import importlib
import sys

from evotrace import EvoTraceError, __version__

_MODULES = (
    "analytics",
    "builder",
    "compiler",
    "curator_agent",
    "errors",
    "gitops",
    "history",
    "importers",
    "miner",
    "privacy",
    "recorder",
    "runner",
    "store",
    "util",
)

for _name in _MODULES:
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(f"evotrace.{_name}")

ScaleVerifierError = EvoTraceError

__all__ = ["EvoTraceError", "ScaleVerifierError", "__version__"]
