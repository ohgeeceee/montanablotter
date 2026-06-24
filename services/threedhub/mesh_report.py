"""3D Hub — mesh inspection tool.

Given a path to an STL/OBJ/3MF/PLY/STEP file (or anything trimesh can read),
returns a JSON-serialisable report with format, bounds, printability flags,
and a list of issues the intake step should surface.

This is the deterministic, low-latency first pass; any LLM/agent layer
consumes this JSON rather than calling trimesh directly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import trimesh


# A "printable" mesh in our shop is watertight, has positive volume, at least
# one face, and is not absurdly large (>1 m per axis usually means a unit
# mismatch because STL is unitless).
PRINTABLE_MM_LIMIT = 1000.0


@dataclass
class MeshReport:
    path: str
    format: str
    file_bytes: int
    file_sha256: str
    n_vertices: int
    n_faces: int
    n_edges: int
    is_watertight: bool
    is_winding_consistent: bool
    volume_mm3: float
    surface_area_mm2: float
    bbox_mm: list[list[float]]  # [[xmin,ymin,zmin],[xmax,ymax,zmax]]
    bbox_size_mm: list[float]   # [dx,dy,dz]
    centroid_mm: list[float]
    issues: list[str] = field(default_factory=list)
    printable: bool = True
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=_jsonable)


def _jsonable(o: Any) -> Any:
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not jsonable: {type(o)}")


def inspect_mesh(path: str | Path) -> MeshReport:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(p)

    raw = p.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    fmt = p.suffix.lower().lstrip(".") or "unknown"

    if fmt in ("step", "stp"):
        return MeshReport(
            path=str(p),
            format=fmt,
            file_bytes=len(raw),
            file_sha256=sha,
            n_vertices=0,
            n_faces=0,
            n_edges=0,
            is_watertight=False,
            is_winding_consistent=False,
            volume_mm3=0.0,
            surface_area_mm2=0.0,
            bbox_mm=[[0, 0, 0], [0, 0, 0]],
            bbox_size_mm=[0, 0, 0],
            centroid_mm=[0, 0, 0],
            printable=False,
            issues=[
                "STEP files require a CAD kernel (cadquery/cascadio) which is "
                "not installed in this toolchain. Convert to STL or 3MF "
                "before re-uploading."
            ],
        )

    mesh = trimesh.load(p, force="mesh", process=True)

    issues: list[str] = []
    notes: list[str] = []

    if len(mesh.faces) == 0:
        issues.append("Mesh is empty (no faces).")
    if not mesh.is_watertight:
        issues.append(
            "Mesh is not watertight — has boundary edges. Slicer will "
            "try to cap them but the result may be wrong."
        )
    if mesh.volume < 0:
        issues.append(
            "Mesh volume is negative — winding is inverted. "
            "trimesh can fix this with `mesh.invert()` on retry."
        )
    if len(mesh.faces) > 0 and not mesh.is_winding_consistent:
        notes.append(
            "Winding is not fully consistent. Print may show artifacts "
            "where normals flip."
        )

    if len(mesh.faces):
        bounds_arr = mesh.bounds
        bbox = [[float(bounds_arr[0, i]) for i in range(3)],
                [float(bounds_arr[1, i]) for i in range(3)]]
        size = [bbox[1][i] - bbox[0][i] for i in range(3)]
    else:
        bbox = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
        size = [0.0, 0.0, 0.0]

    if any(s > PRINTABLE_MM_LIMIT for s in size):
        issues.append(
            f"Bounding box is {size} mm — one axis exceeds 1 m. Common cause: "
            "the model was exported in cm or inches instead of mm. Re-export "
            "in mm and re-upload."
        )

    return MeshReport(
        path=str(p),
        format=fmt,
        file_bytes=len(raw),
        file_sha256=sha,
        n_vertices=int(len(mesh.vertices)),
        n_faces=int(len(mesh.faces)),
        n_edges=int(len(mesh.edges_unique)),
        is_watertight=bool(mesh.is_watertight),
        is_winding_consistent=bool(mesh.is_winding_consistent),
        volume_mm3=float(mesh.volume) if mesh.is_watertight else 0.0,
        surface_area_mm2=float(mesh.area),
        bbox_mm=bbox,
        bbox_size_mm=size,
        centroid_mm=mesh.centroid.tolist() if len(mesh.faces) else [0, 0, 0],
        issues=issues,
        notes=notes,
        printable=(
            len(mesh.faces) > 0
            and mesh.is_watertight
            and mesh.volume > 0
            and not issues
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect a 3D mesh for printability.")
    ap.add_argument("path", help="STL/OBJ/3MF/PLY file to inspect")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args()
    try:
        report = inspect_mesh(args.path)
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e), "path": args.path}))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(report.to_json())
    else:
        print(report.to_json())
    return 0 if report.printable else 1


if __name__ == "__main__":
    sys.exit(main())
