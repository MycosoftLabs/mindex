"""Public API contracts for MINDEX.

This package is the *only* stable import surface for external consumers.
Internal DB models, query shapes, and service plumbing should never leak past
these DTOs.
"""

from . import v1

__all__ = ["v1"]

