"""Provider base + shared HTTP helpers for the prospecting engine.

Providers FETCH and PARSE only — they yield ProspectRecord objects.
All DB writes go through engine.upsert_prospect (single dedupe path).
Every provider must be safe to run with --dry-run (no writes anywhere).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests

UA = {'User-Agent': 'Mozilla/5.0 (compatible; MontanaBlotterProspecting/1.0; '
                    'admin contact: montanablotter.com)'}
TIMEOUT = 30


class ProviderUnavailable(RuntimeError):
    """Raised when a provider's prereqs are missing (no API key, WAF wall,
    endpoint changed). CLI reports it and moves on — never crashes the run."""


@dataclass
class Vertical:
    key: str            # matches advertising_prospects.category values
    label: str
    google_queries: tuple
    sos_keywords: tuple


VERTICALS = {
    'bail': Vertical('bail', 'Bail Bondsmen',
                     ('bail bonds {county} Montana', 'bail bond agent Montana'),
                     ('bail bond',)),
    'legal': Vertical('legal', 'Defense Attorneys',
                      ('criminal defense attorney {county} Montana',),
                      ('law office', 'attorney')),
    'pi': Vertical('pi', 'Private Investigators',
                   ('private investigator {county} Montana',),
                   ('investigation', 'investigator')),
    'process_server': Vertical('process_server', 'Process Servers',
                               ('process server {county} Montana',),
                               ('process server',)),
}


def env(name: str, default: str = '') -> str:
    """Read .env-style setting without importing the app's config loader
    (engine runs standalone via python -m)."""
    val = os.environ.get(name) or default
    if not val:
        try:
            root = os.path.join(os.path.dirname(__file__), *['..'] * 5)
            with open(os.path.join(root, '.env')) as fh:
                for line in fh:
                    k, _, v = line.partition('=')
                    if k.strip() == name:
                        return v.strip().strip('"\'')
        except OSError:
            pass
    return val


def get(url: str, **kw) -> requests.Response:
    kw.setdefault('timeout', TIMEOUT)
    kw.setdefault('headers', UA)
    return requests.get(url, **kw)
