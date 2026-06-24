"""3D Hub service layer for MontanaBlotter.

Mesh inspection, quoting, and slicer wrapping used by the
`blueprints.threedhub` admin blueprint. Kept free of web/Flask
dependencies so it can be called from scripts or agents.
"""

from services.threedhub.mesh_report import inspect_mesh, MeshReport
from services.threedhub.estimate import quote_from_mesh, quote_from_slicer, Quote

__all__ = [
    "inspect_mesh",
    "MeshReport",
    "quote_from_mesh",
    "quote_from_slicer",
    "Quote",
]
