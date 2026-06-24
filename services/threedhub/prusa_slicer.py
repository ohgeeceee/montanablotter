"""3D Hub — slicer wrapper (PrusaSlicer CLI).

Slices an STL into gcode using a printer profile .ini. Returns a JSON
report with the gcode path, file size, slicer version, and estimated stats
grepped from the gcode header.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


# Default profile lives under the MontanaBlotter configs tree. The dispatch
# step passes a different .ini per printer/material combo; this is only the
# fallback when no specific profile is requested.
DEFAULT_PROFILE = (
    Path(__file__).resolve().parents[2] / "configs" / "threedhub" / "default.ini"
)

SLICER_TIMEOUT_S = 600


@dataclass
class SliceReport:
    input_stl: str
    input_sha256: str
    output_gcode: str
    profile: str
    slicer: str = "prusa-slicer"
    slicer_version: str = ""
    wall_seconds: float = 0.0
    output_bytes: int = 0
    filament_used_mm: float = 0.0
    filament_used_g: float = 0.0
    est_print_time_s: float = 0.0
    layer_height_mm: float = 0.0
    warnings: list[str] = field(default_factory=list)
    ok: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _version() -> str:
    """Best-effort: PrusaSlicer's banner line is on stderr."""
    try:
        out = subprocess.run(
            ["prusa-slicer", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        m = re.search(r"(PrusaSlicer-[\d.]+\S*)", out.stdout + out.stderr)
        return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"


def _parse_hms(s: str) -> float:
    """Parse '2h 13m 45s' / '13m 45s' / '45s' into seconds."""
    total = 0.0
    m = re.search(r"(\d+)\s*d", s)
    if m:
        total += int(m.group(1)) * 86400
    m = re.search(r"(\d+)\s*h", s)
    if m:
        total += int(m.group(1)) * 3600
    m = re.search(r"(\d+)\s*m", s)
    if m:
        total += int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*s", s)
    if m:
        total += int(m.group(1))
    return total


def _grep_gcode_stats(gcode: Path) -> dict:
    """Parse PrusaSlicer footer stats for filament used and print time."""
    stats = {
        "filament_used_mm": 0.0,
        "filament_used_g": 0.0,
        "est_print_time_s": 0.0,
    }
    if not gcode.exists():
        return stats

    txt = gcode.read_text(errors="replace").splitlines()
    mm_pat = re.compile(r"filament used\s*\[mm\]\s*=\s*([\d.]+)", re.I)
    g_pat = re.compile(r"(?:total )?filament used\s*\[g\]\s*=\s*([\d.]+)", re.I)
    time_pat = re.compile(r"estimated printing time.*?=\s*(.+?)\s*$", re.I)

    found = 0
    for line in txt:
        if not line.startswith(";"):
            continue
        if found >= 3:
            break
        if (m := mm_pat.search(line)):
            stats["filament_used_mm"] = float(m.group(1))
            found += 1
        elif (m := g_pat.search(line)):
            v = float(m.group(1))
            if v > 0:
                stats["filament_used_g"] = v
            found += 1
        elif (m := time_pat.search(line)):
            stats["est_print_time_s"] = _parse_hms(m.group(1).strip())
            found += 1

    # Fallback: compute weight from length if PrusaSlicer reports 0 g.
    if stats["filament_used_g"] == 0.0 and stats["filament_used_mm"] > 0.0:
        radius_cm = 0.175 / 2
        length_cm = stats["filament_used_mm"] / 10.0
        volume_cm3 = 3.14159 * radius_cm ** 2 * length_cm
        stats["filament_used_g"] = volume_cm3 * 1.24

    return stats


def slice_mesh(
    stl: str | Path,
    out_gcode: str | Path,
    profile: str | Path | None = None,
    timeout: int = SLICER_TIMEOUT_S,
) -> SliceReport:
    stl_p = Path(stl).expanduser().resolve()
    out_p = Path(out_gcode).expanduser().resolve()
    profile_p = Path(profile).expanduser().resolve() if profile else DEFAULT_PROFILE
    if not profile_p.exists():
        raise FileNotFoundError(f"profile not found: {profile_p}")
    out_p.parent.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    if shutil.which("prusa-slicer") is None:
        raise RuntimeError("prusa-slicer not installed (apt: prusa-slicer)")

    t0 = time.monotonic()
    cmd = [
        "prusa-slicer",
        "--export-gcode",
        "--load", str(profile_p),
        "--output", str(out_p),
        str(stl_p),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    wall = time.monotonic() - t0

    if proc.returncode != 0:
        raise RuntimeError(
            f"prusa-slicer failed (rc={proc.returncode}): "
            f"{proc.stderr.strip()[:500]}"
        )
    if not out_p.exists() or out_p.stat().st_size == 0:
        raise RuntimeError("prusa-slicer exited 0 but produced no gcode")

    stats = _grep_gcode_stats(out_p)
    return SliceReport(
        input_stl=str(stl_p),
        input_sha256=_sha256(stl_p),
        output_gcode=str(out_p),
        profile=str(profile_p),
        slicer_version=_version(),
        wall_seconds=round(wall, 2),
        output_bytes=out_p.stat().st_size,
        filament_used_mm=stats["filament_used_mm"],
        filament_used_g=stats["filament_used_g"],
        est_print_time_s=stats["est_print_time_s"],
        warnings=warnings,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Slice an STL with PrusaSlicer.")
    ap.add_argument("stl", help="input STL/OBJ path")
    ap.add_argument("gcode", help="output gcode path")
    ap.add_argument("--profile", help=f"printer profile .ini (default: {DEFAULT_PROFILE})")
    ap.add_argument("--timeout", type=int, default=SLICER_TIMEOUT_S)
    args = ap.parse_args()
    try:
        rep = slice_mesh(args.stl, args.gcode, args.profile, args.timeout)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    print(rep.to_json())
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
