"""3D Hub — cost & time estimator.

Two modes:
  - "sliced": post-slicing. The slicer already told us est print time and
    filament used; we just apply shop rates and material markup.
  - "mesh": pre-slicing. We have a mesh report (volume mm^3, bbox). We
    estimate print time from a heuristic and quote a wider range.

Pure functions — no LLM, no I/O beyond reading the input values.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field


# Material rates: $/gram
MATERIAL_RATE = {
    "pla": 0.025,
    "petg": 0.030,
    "abs": 0.030,
    "tpu": 0.045,
    "resin": 0.080,
}

# Machine+operator rate: $/minute
MACHINE_RATE = {
    "pla": 0.04,
    "petg": 0.05,
    "abs": 0.05,
    "tpu": 0.07,
    "resin": 0.08,
}

FAILURE_BUFFER = {
    "pla": 0.12,
    "petg": 0.12,
    "abs": 0.12,
    "tpu": 0.15,
    "resin": 0.06,
}

# Heuristic volumetric flow rate in mm^3/s for a 0.4mm nozzle.
HEURISTIC_FLOW_MM3_PER_S = {
    "pla": 8.0,
    "petg": 7.0,
    "abs": 7.0,
    "tpu": 4.0,
    "resin": 0.0,
}

RESIN_SECONDS_PER_CM3 = 35.0


@dataclass
class Quote:
    material: str
    layer_height_mm: float
    est_print_time_s: float
    est_filament_g: float
    machine_cost: float
    material_cost: float
    subtotal: float
    failure_buffer: float
    post_processing: float
    rush_multiplier: float
    total: float
    range_low: float
    range_high: float
    currency: str = "USD"
    notes: list[str] = field(default_factory=list)
    source: str = ""  # "sliced" | "mesh"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _round2(x: float) -> float:
    return round(x, 2)


def quote_from_slicer(
    material: str,
    filament_g: float,
    print_time_s: float,
    layer_height_mm: float = 0.2,
    post_processing: bool = False,
    rush: bool = False,
) -> Quote:
    material = material.lower()
    if material not in MACHINE_RATE:
        raise ValueError(f"unknown material: {material}")

    minutes = print_time_s / 60.0
    machine = minutes * MACHINE_RATE[material]
    mat = filament_g * MATERIAL_RATE[material]
    sub = machine + mat
    buf = sub * FAILURE_BUFFER[material]
    pp = 15.0 if post_processing else 0.0
    rush_mul = 1.5 if rush else 1.0
    total = (sub + buf + pp) * rush_mul

    q = Quote(
        material=material,
        layer_height_mm=layer_height_mm,
        est_print_time_s=print_time_s,
        est_filament_g=filament_g,
        machine_cost=_round2(machine),
        material_cost=_round2(mat),
        subtotal=_round2(sub),
        failure_buffer=_round2(buf),
        post_processing=_round2(pp),
        rush_multiplier=rush_mul,
        total=_round2(total),
        range_low=_round2(total * 0.85),
        range_high=_round2(total * 1.20),
        notes=[],
        source="sliced",
    )
    return _apply_floor(q)


def quote_from_mesh(
    material: str,
    volume_mm3: float,
    bbox_size_mm: list[float],
    layer_height_mm: float = 0.2,
    post_processing: bool = False,
    rush: bool = False,
) -> Quote:
    """Pre-slicing heuristic. Volume in mm^3, bbox in mm. Wider range."""
    material = material.lower()
    if material not in MACHINE_RATE:
        raise ValueError(f"unknown material: {material}")

    infill = 0.20
    density = 1.10 if material == "resin" else 1.24
    volume_cm3 = volume_mm3 / 1000.0
    filament_g = volume_cm3 * density * infill

    if material == "resin":
        est_time_s = volume_cm3 * RESIN_SECONDS_PER_CM3
    else:
        flow = HEURISTIC_FLOW_MM3_PER_S[material]
        est_time_s = (volume_mm3 * infill) / flow if flow else 0.0

    q = quote_from_slicer(
        material,
        filament_g,
        est_time_s,
        layer_height_mm=layer_height_mm,
        post_processing=post_processing,
        rush=rush,
    )
    q.source = "mesh"
    q.range_low = _round2(q.total * 0.65)
    q.range_high = _round2(q.total * 1.45)
    q.notes = [
        f"Estimate assumes {int(infill * 100)}% infill and {density} g/cm^3 "
        "material density. Real value computed after slicing.",
        f"Bounding box {bbox_size_mm} mm — print orientation may shift quote.",
    ]
    return _apply_floor(q)


def _apply_floor(q: Quote, floor_usd: float = 5.0) -> Quote:
    """Enforce a minimum order floor. No print under $5 (machine time + handling)."""
    if q.total < floor_usd or q.range_low < floor_usd:
        q.total = max(q.total, floor_usd)
        q.range_low = max(q.range_low, floor_usd)
        q.range_high = max(q.range_high, floor_usd * 1.2)
        if "Floor applied" not in " ".join(q.notes):
            q.notes.append(f"Floor applied: minimum ${floor_usd:.2f}")
    return q


def main() -> int:
    ap = argparse.ArgumentParser(description="Quote a 3D print job.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_sliced = sub.add_parser("sliced", help="quote from slicer output")
    p_sliced.add_argument("--material", required=True)
    p_sliced.add_argument("--filament-g", type=float, required=True)
    p_sliced.add_argument("--print-time-s", type=float, required=True)
    p_sliced.add_argument("--layer-height", type=float, default=0.2)
    p_sliced.add_argument("--post-processing", action="store_true")
    p_sliced.add_argument("--rush", action="store_true")

    p_mesh = sub.add_parser("mesh", help="quote from mesh report (no slicer)")
    p_mesh.add_argument("--material", required=True)
    p_mesh.add_argument("--volume-mm3", type=float, required=True)
    p_mesh.add_argument("--bbox", required=True, help="dx,dy,dz in mm")
    p_mesh.add_argument("--layer-height", type=float, default=0.2)
    p_mesh.add_argument("--post-processing", action="store_true")
    p_mesh.add_argument("--rush", action="store_true")

    args = ap.parse_args()
    try:
        if args.cmd == "sliced":
            q = quote_from_slicer(
                args.material,
                args.filament_g,
                args.print_time_s,
                layer_height_mm=args.layer_height,
                post_processing=args.post_processing,
                rush=args.rush,
            )
        else:
            bbox = [float(x) for x in args.bbox.split(",")]
            q = quote_from_mesh(
                args.material,
                args.volume_mm3,
                bbox,
                layer_height_mm=args.layer_height,
                post_processing=args.post_processing,
                rush=args.rush,
            )
        q = _apply_floor(q)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    print(q.to_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
