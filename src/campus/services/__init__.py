"""campus.services

Shared Campus services: self-contained capability packages that campus
services consume (email sending today; more may follow).

This package is owned entirely by the campus-core distribution — the
suite is its only consumer (#992).
"""

__all__ = ["email"]

from . import email
