"""Read-only query API for the field-cartography warehouse.

    from field_cartography.api import Client
    c = Client()          # lazy; cheap to construct
    c.warm()              # optional: preload all indexes (~2-3 GB RSS)
    rec = c.metadata("doi:10.1002/ima.70306")

See README.md in this directory for the full contract and caveats.
"""
from __future__ import annotations

from .client import Client
from .models import ExternalIds, PaperRecord
from .reducer import REDUCER_SPEC

__all__ = ["Client", "PaperRecord", "ExternalIds", "REDUCER_SPEC", "__version__"]

# semver — v31 records this on every transcript. Bump major only on
# signature removal/rename; add methods freely at minor.
__version__ = "1.0.0"
