# This file is part of vibe-cading.
#
# vibe-cading is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# vibe-cading is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""3D-printable 45°-flank screw threads (``print45``) and 60° ISO/Unified threads.

Adapted from CNC Kitchen's ScanRuler thread engine
(``plugins/reverse/core/thread.ts`` and ``plugins/reverse/core/kernelThread.ts``,
SPDX-License-Identifier: AGPL-3.0-only).

Thread forms
------------
* ``"print45"`` — 3D-printing thread form with 90° included angle (each flank
  at 45° to the radius), flat crest ``0.2 * pitch``, flat root ``0.2 * pitch``,
  and profile depth ``0.3 * pitch``.  Printed with its axis vertical, every
  downward-facing flank leans at 45° so it prints cleanly without supports.
  Sized on ISO metric diameters from M3 to M64 at ``print_pitch(coarse)`` —
  the pitch that cuts as deep as the ISO coarse thread so the minor diameter
  and core strength match ISO metric coarse — plus the ISO coarse pitch itself.
* ``"metric"`` / ``"unc"`` / ``"unf"`` — Standard 60° vee thread form
  (ISO 68-1 / ASME B1.1) with flanks at 30° to the radius, crest flat
  ``pitch / 8``, root flat ``pitch / 4``, and depth ``(5/8) * (sqrt(3)/2) * pitch``.

Coordinate system
-----------------
``Z = 0`` is the start (bottom) face of the threaded section; the thread runs
along ``+Z`` to ``Z = length``, centered on the Z axis ``(X = 0, Y = 0)``.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

import cadquery as cq
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
from OCP.BRepGProp import BRepGProp
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.GProp import GProp_GProps
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Dir

from vibe_cading.print_settings import ToleranceProfile


ThreadStandard = Literal["metric", "unc", "unf", "print45"]

THREAD_STANDARDS: list[dict[str, str]] = [
    {"id": "metric", "label": "ISO metric"},
    {"id": "unc", "label": "Unified coarse (UNC)"},
    {"id": "unf", "label": "Unified fine (UNF)"},
    {"id": "print45", "label": "3D printing — 45° flanks"},
]

# Maximum helical turns allowed in a single BRep thread sweep.
MAX_THREAD_TURNS: int = 400

_INCH_MM: float = 25.4
_PRINT_FROM_MM: float = 3.0
_SAME_MM: float = 1e-4
_DRAWN_AT_SIZE_MM: float = 0.01
_POINT_FLAT: float = 1e-4
_CREST_ON_FACE: float = 0.01
_CHAMFER_PAST_ROOT: float = 0.1


@dataclass(frozen=True)
class _ThreadForm:
    """Normalized thread profile shares per unit pitch.

    Satisfies ``crest + root + 2 * depth * tan == 1``.
    """

    tan: float
    sec: float
    depth: float
    crest: float
    root: float


# 60° form of ISO 68-1 and ASME B1.1: flanks at 30° to the radius.
V60_FORM = _ThreadForm(
    tan=1.0 / math.sqrt(3.0),
    sec=2.0 / math.sqrt(3.0),
    depth=(5.0 / 8.0) * (math.sqrt(3.0) / 2.0),
    crest=1.0 / 8.0,
    root=1.0 / 4.0,
)

# 45° form for 3D printing: flanks at 45° to the radius, crest and root
# 1/5 pitch wide each, depth 0.3 pitch.
PRINT45_FORM = _ThreadForm(
    tan=1.0,
    sec=math.sqrt(2.0),
    depth=0.3,
    crest=1.0 / 5.0,
    root=1.0 / 5.0,
)

THREAD_FORMS: dict[str, _ThreadForm] = {
    "metric": V60_FORM,
    "unc": V60_FORM,
    "unf": V60_FORM,
    "print45": PRINT45_FORM,
}


def _get_form(standard: str) -> _ThreadForm:
    key = standard.strip().lower()
    if key not in THREAD_FORMS:
        raise ValueError(
            f"Unknown thread standard '{standard}'. "
            f"Available: {list(THREAD_FORMS.keys())}"
        )
    return THREAD_FORMS[key]


# ISO 261 first- and second-choice metric sizes up to M64: (diameter_mm, [coarse, *fine]).
ISO_METRIC_TABLE: tuple[tuple[float, list[float]], ...] = (
    (1.0, [0.25, 0.2]),
    (1.2, [0.25, 0.2]),
    (1.4, [0.3, 0.2]),
    (1.6, [0.35, 0.2]),
    (2.0, [0.4, 0.25]),
    (2.5, [0.45, 0.35]),
    (3.0, [0.5, 0.35]),
    (3.5, [0.6, 0.35]),
    (4.0, [0.7, 0.5]),
    (5.0, [0.8, 0.5]),
    (6.0, [1.0, 0.75]),
    (7.0, [1.0, 0.75]),
    (8.0, [1.25, 1.0, 0.75]),
    (10.0, [1.5, 1.25, 1.0, 0.75]),
    (12.0, [1.75, 1.5, 1.25, 1.0]),
    (14.0, [2.0, 1.5, 1.25, 1.0]),
    (16.0, [2.0, 1.5, 1.0]),
    (18.0, [2.5, 2.0, 1.5, 1.0]),
    (20.0, [2.5, 2.0, 1.5, 1.0]),
    (22.0, [2.5, 2.0, 1.5, 1.0]),
    (24.0, [3.0, 2.0, 1.5, 1.0]),
    (27.0, [3.0, 2.0, 1.5, 1.0]),
    (30.0, [3.5, 3.0, 2.0, 1.5, 1.0]),
    (33.0, [3.5, 3.0, 2.0, 1.5]),
    (36.0, [4.0, 3.0, 2.0, 1.5]),
    (39.0, [4.0, 3.0, 2.0, 1.5]),
    (42.0, [4.5, 4.0, 3.0, 2.0, 1.5]),
    (45.0, [4.5, 4.0, 3.0, 2.0, 1.5]),
    (48.0, [5.0, 4.0, 3.0, 2.0, 1.5]),
    (52.0, [5.0, 4.0, 3.0, 2.0, 1.5]),
    (56.0, [5.5, 4.0, 3.0, 2.0, 1.5]),
    (60.0, [5.5, 4.0, 3.0, 2.0, 1.5]),
    (64.0, [6.0, 4.0, 3.0, 2.0, 1.5]),
)

# ASME B1.1 Unified coarse (UNC): (label, major_diameter_in, tpi)
UNC_TABLE: tuple[tuple[str, float, float], ...] = (
    ("#1", 0.073, 64.0),
    ("#2", 0.086, 56.0),
    ("#3", 0.099, 48.0),
    ("#4", 0.112, 40.0),
    ("#5", 0.125, 40.0),
    ("#6", 0.138, 32.0),
    ("#8", 0.164, 32.0),
    ("#10", 0.19, 24.0),
    ("#12", 0.216, 24.0),
    ("1/4", 0.25, 20.0),
    ("5/16", 0.3125, 18.0),
    ("3/8", 0.375, 16.0),
    ("7/16", 0.4375, 14.0),
    ("1/2", 0.5, 13.0),
    ("9/16", 0.5625, 12.0),
    ("5/8", 0.625, 11.0),
    ("3/4", 0.75, 10.0),
    ("7/8", 0.875, 9.0),
    ("1", 1.0, 8.0),
    ("1-1/8", 1.125, 7.0),
    ("1-1/4", 1.25, 7.0),
    ("1-3/8", 1.375, 6.0),
    ("1-1/2", 1.5, 6.0),
    ("1-3/4", 1.75, 5.0),
    ("2", 2.0, 4.5),
)

# ASME B1.1 Unified fine (UNF): (label, major_diameter_in, tpi)
UNF_TABLE: tuple[tuple[str, float, float], ...] = (
    ("#0", 0.06, 80.0),
    ("#1", 0.073, 72.0),
    ("#2", 0.086, 64.0),
    ("#3", 0.099, 56.0),
    ("#4", 0.112, 48.0),
    ("#5", 0.125, 44.0),
    ("#6", 0.138, 40.0),
    ("#8", 0.164, 36.0),
    ("#10", 0.19, 32.0),
    ("#12", 0.216, 28.0),
    ("1/4", 0.25, 28.0),
    ("5/16", 0.3125, 24.0),
    ("3/8", 0.375, 24.0),
    ("7/16", 0.4375, 20.0),
    ("1/2", 0.5, 20.0),
    ("9/16", 0.5625, 18.0),
    ("5/8", 0.625, 18.0),
    ("3/4", 0.75, 16.0),
    ("7/8", 0.875, 14.0),
    ("1", 1.0, 12.0),
    ("1-1/8", 1.125, 12.0),
    ("1-1/4", 1.25, 12.0),
    ("1-3/8", 1.375, 12.0),
    ("1-1/2", 1.5, 12.0),
)


def _fmt_num(val: float) -> str:
    """Format a diameter or pitch without trailing '.0'."""
    rounded = round(val, 4)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:g}"


def print_pitch(coarse: float) -> float:
    """Compute the ``print45`` thread pitch matching an ISO coarse pitch's thread depth.

    Because the 60° ISO form cuts ``~0.541266 * pitch`` deep while the 45°
    printing form cuts ``0.3 * pitch`` deep, the depth-matched printing pitch
    is ``(coarse * V60.depth) / 0.3`` (~1.804x coarse), rounded to 0.25 mm
    steps under 3 mm and 0.5 mm steps from 3 mm up (using half-up rounding to
    match TypeScript ``Math.round``).
    """
    p = (float(coarse) * V60_FORM.depth) / PRINT45_FORM.depth
    step = 0.25 if p < 3.0 else 0.5
    return math.floor(p / step + 0.5) * step


class _Print45SizeTable(dict):
    """Dictionary of ``print45`` standard sizes supporting ``M<d>``, ``Ø<d>``, or numeric lookup."""

    def __init__(self, entries: list[tuple[str, dict[str, object]]]) -> None:
        super().__init__(entries)
        self._lookup: dict[str, str] = {}
        for key, spec in entries:
            d_str = _fmt_num(float(spec["diameter"]))
            self._lookup[key.lower()] = key
            self._lookup[f"ø{d_str}".lower()] = key
            self._lookup[d_str] = key

    def _resolve(self, key: object) -> str | None:
        if isinstance(key, (int, float)):
            return self._lookup.get(_fmt_num(float(key)))
        if isinstance(key, str):
            if super().__contains__(key):
                return key
            cleaned = key.strip().lower()
            return self._lookup.get(cleaned)
        return None

    def __contains__(self, key: object) -> bool:
        return self._resolve(key) is not None

    def __getitem__(self, key: object) -> dict[str, object]:
        resolved = self._resolve(key)
        if resolved is None:
            raise KeyError(key)
        return super().__getitem__(resolved)

    def get(self, key: object, default: object = None) -> object:
        resolved = self._resolve(key)
        if resolved is None:
            return default
        return super().__getitem__(resolved)


CNC_KITCHEN_PRINT45_SIZES: dict[str, dict[str, object]] = _Print45SizeTable([
    (
        f"M{_fmt_num(d)}",
        {
            "key": f"M{_fmt_num(d)}",
            "label": f"Ø{_fmt_num(d)}",
            "diameter": float(d),
            "pitches": [print_pitch(pitches[0]), float(pitches[0])],
            "print_pitch": print_pitch(pitches[0]),
            "coarse_pitch": float(pitches[0]),
        },
    )
    for d, pitches in ISO_METRIC_TABLE
    if d >= _PRINT_FROM_MM
])

_SIZES_BY_STANDARD: dict[str, list[dict[str, object]]] = {
    "metric": [
        {"label": f"M{_fmt_num(d)}", "diameter": float(d), "pitches": list(pitches)}
        for d, pitches in ISO_METRIC_TABLE
    ],
    "unc": [
        {"label": label, "diameter": d * _INCH_MM, "pitches": [_INCH_MM / tpi]}
        for label, d, tpi in UNC_TABLE
    ],
    "unf": [
        {"label": label, "diameter": d * _INCH_MM, "pitches": [_INCH_MM / tpi]}
        for label, d, tpi in UNF_TABLE
    ],
    "print45": [
        {
            "label": f"Ø{_fmt_num(d)}",
            "diameter": float(d),
            "pitches": [print_pitch(pitches[0]), float(pitches[0])],
        }
        for d, pitches in ISO_METRIC_TABLE
        if d >= _PRINT_FROM_MM
    ],
}


def thread_sizes(standard: str = "print45") -> list[dict[str, object]]:
    """Return the standard thread size table for ``standard``, smallest first."""
    key = standard.strip().lower()
    if key not in _SIZES_BY_STANDARD:
        raise ValueError(
            f"Unknown thread standard '{standard}'. "
            f"Available: {list(_SIZES_BY_STANDARD.keys())}"
        )
    return [dict(s) for s in _SIZES_BY_STANDARD[key]]


def size_of(standard: str, diameter: float) -> dict[str, object] | None:
    """Return the size entry in ``standard`` matching ``diameter`` (mm), or ``None``."""
    for s in thread_sizes(standard):
        if abs(float(s["diameter"]) - float(diameter)) < _SAME_MM:
            return s
    return None


def threads_per_inch(pitch: float) -> float:
    """Convert a pitch in millimetres to threads per inch (TPI)."""
    return _INCH_MM / float(pitch)


def thread_designation(standard: str, diameter: float, pitch: float) -> str | None:
    """Return the standard designation (e.g. ``'M16×2'``, ``'Ø16×3.5 print'``) or ``None``."""
    std = standard.strip().lower()
    size = size_of(std, diameter)
    if size is None:
        return None
    pitches = [float(p) for p in size["pitches"]]  # type: ignore[index]
    if not any(abs(p - float(pitch)) < _SAME_MM for p in pitches):
        return None
    if std == "metric":
        return f"{size['label']}×{_fmt_num(pitch)}"
    if std == "print45":
        return f"{size['label']}×{_fmt_num(pitch)} print"
    tpi_str = _fmt_num(round(threads_per_inch(pitch) * 100.0) / 100.0)
    return f"{size['label']}-{tpi_str} {std.upper()}"


def thread_name(standard: str, diameter: float, pitch: float) -> str:
    """Return the human-readable thread name (designation or custom ``Ød × p``)."""
    std = standard.strip().lower()
    desig = thread_designation(std, diameter, pitch)
    if desig is not None:
        return desig
    d_str = _fmt_num(round(float(diameter) * 1000.0) / 1000.0)
    p_str = _fmt_num(round(float(pitch) * 1000.0) / 1000.0)
    suffix = " print" if std == "print45" else ""
    return f"Ø{d_str} × {p_str}{suffix}"


def thread_depth(pitch: float, standard: str = "print45") -> float:
    """Radial depth of the basic thread profile from major to minor radius (mm).

    Equals ``0.3 * pitch`` for ``"print45"`` and ``(5/8) * (sqrt(3)/2) * pitch``
    (~0.541266 * pitch) for ``"metric"`` / ``"unc"`` / ``"unf"``.
    """
    return _get_form(standard).depth * float(pitch)


def minor_diameter(diameter: float, pitch: float, standard: str = "print45") -> float:
    """Minor diameter of the basic profile (bolt root / nut crest), in mm."""
    return float(diameter) - 2.0 * thread_depth(pitch, standard)


def pitch_diameter(diameter: float, pitch: float, standard: str = "print45") -> float:
    """Pitch diameter where tooth thickness equals gap width (mm)."""
    form = _get_form(standard)
    p = float(pitch)
    return float(diameter) - (p / 2.0 - form.crest * p) / form.tan


def max_thread_offset(pitch: float, standard: str = "print45") -> float:
    """Maximum radial clearance offset before tooth thickness vanishes at the pitch diameter (mm)."""
    form = _get_form(standard)
    return float(pitch) / (4.0 * form.sec)


def size_for_face(
    standard: str,
    face_diameter: float,
    internal: bool = False,
) -> dict[str, float]:
    """Infer the closest standard ``{diameter, pitch}`` for a cylindrical boss or bore."""
    sizes = thread_sizes(standard)
    if internal:
        for s in sizes:
            if abs(float(s["diameter"]) - float(face_diameter)) < _DRAWN_AT_SIZE_MM:
                pitches = s["pitches"]  # type: ignore[assignment]
                return {"diameter": float(s["diameter"]), "pitch": float(pitches[0])}
    best = sizes[0]
    best_off = float("inf")
    for s in sizes:
        d = float(s["diameter"])
        p0 = float(s["pitches"][0])  # type: ignore[index]
        target = minor_diameter(d, p0, standard) if internal else d
        off = abs(target - float(face_diameter))
        if off < best_off:
            best = s
            best_off = off
    return {
        "diameter": float(best["diameter"]),
        "pitch": float(best["pitches"][0]),  # type: ignore[index]
    }


def thread_problem(
    diameter: float,
    pitch: float,
    standard: str = "print45",
    offset: float = 0.0,
) -> str | None:
    """Validate thread parameters and return an error description string, or ``None`` if valid."""
    if not (diameter > 0.0):
        return "The thread needs a diameter."
    if not (pitch > 0.0):
        return "The thread needs a pitch."
    if not (offset >= 0.0):
        return "The offset only takes material away — it has to be zero or more."
    max_off = max_thread_offset(pitch, standard)
    if offset >= max_off:
        return (
            f"An offset of {offset:.4g} mm leaves the tooth no thickness at the "
            f"pitch diameter — under {max_off:.4g} mm for a pitch of {pitch:.4g} mm."
        )
    if diameter / 2.0 - thread_depth(pitch, standard) - offset < pitch / 4.0:
        return (
            f"A pitch of {pitch:.4g} mm is too coarse for Ø{diameter:.4g} mm — "
            "the thread would reach the axis."
        )
    return None


class _ToothOutlineResult(dict):
    """Dict-and-attribute result returned by :func:`tooth_outline`."""

    def __init__(
        self,
        *,
        points: list[tuple[float, float]],
        crest: float,
        root: float,
        pointed: bool,
    ) -> None:
        super().__init__(points=points, crest=crest, root=root, pointed=pointed)
        self.points = points
        self.crest = crest
        self.root = root
        self.pointed = pointed


def tooth_outline(
    diameter: float | object,
    pitch: float | None = None,
    standard: str = "print45",
    offset: float = 0.0,
    internal: bool = False,
    reach: float = 0.0,
    face: float | None = None,
) -> _ToothOutlineResult:
    """Compute the 2D ``(r, z)`` outline of one thread tooth in an axial half-plane.

    Accepts either explicit ``(diameter, pitch, standard, offset, ...)``
    arguments or a spec object/dict as the first argument.

    Returns a :class:`_ToothOutlineResult` mapping (also supporting attribute
    access) with keys:
    * ``"points"``: ``list[tuple[float, float]]`` of ``(r, z)`` coordinates
      wound counter-clockwise.
    * ``"crest"``: radius of the tooth crest (mm).
    * ``"root"``: radius of the tooth root (mm).
    * ``"pointed"``: ``True`` if ``offset`` has narrowed the crest flat to a point.
    """
    if not isinstance(diameter, (int, float)):
        spec = diameter
        if isinstance(spec, dict):
            d_val = float(spec["diameter"])
            p_val = float(spec["pitch"])
            std_val = str(spec.get("standard", standard))
            off_val = float(spec.get("offset", offset))
        else:
            d_val = float(getattr(spec, "diameter"))
            p_val = float(getattr(spec, "pitch"))
            std_val = str(getattr(spec, "standard", standard))
            off_val = float(getattr(spec, "offset", offset))
    else:
        if pitch is None:
            raise ValueError("pitch is required when diameter is passed as a number.")
        d_val = float(diameter)
        p_val = float(pitch)
        std_val = standard
        off_val = float(offset)

    P = p_val
    c = off_val
    R = d_val / 2.0
    form = _get_form(std_val)
    tan, sec, depth, crest_flat, root_flat = (
        form.tan,
        form.sec,
        form.depth,
        form.crest,
        form.root,
    )
    h = depth * P
    shrink = c * tan - c * sec
    past = min(0.05 * P, float(reach) / 2.0)

    if not internal:
        root = R - h - c
        root_half = ((1.0 - root_flat) * P) / 2.0 + shrink
        crest_half = (crest_flat * P) / 2.0 + shrink
        pointed = crest_half < _POINT_FLAT * P
        apex = R - c + crest_half / tan
        crest = apex if pointed else R - c
        to_apex = pointed or (face is not None and crest >= face - _CREST_ON_FACE * P)
        top: list[tuple[float, float]] = (
            [(apex, 0.0)] if to_apex else [(crest, -crest_half), (crest, crest_half)]
        )
        bend = root - past
        bend_half = root_half + past * tan
        base = root - float(reach)
        points: list[tuple[float, float]] = [
            (base, -bend_half),
            (bend, -bend_half),
            *top,
            (bend, bend_half),
            (base, bend_half),
        ]
        return _ToothOutlineResult(
            points=points, crest=crest, root=root, pointed=pointed
        )

    minor = R - h
    root = R + c
    root_half = ((1.0 - crest_flat) * P) / 2.0 + shrink
    crest_half = (root_flat * P) / 2.0 + shrink
    pointed = crest_half < _POINT_FLAT * P
    apex = minor + c - crest_half / tan
    crest = apex if pointed else minor + c
    to_apex = pointed or (face is not None and crest <= face + _CREST_ON_FACE * P)
    below: list[tuple[float, float]] = (
        [(apex, 0.0)] if to_apex else [(crest, -crest_half)]
    )
    above: list[tuple[float, float]] = [] if to_apex else [(crest, crest_half)]
    bend = root + past
    bend_half = root_half + past * tan
    outer = root + float(reach)
    points = [
        *below,
        (bend, -bend_half),
        (outer, -bend_half),
        (outer, bend_half),
        (bend, bend_half),
        *above,
    ]
    return _ToothOutlineResult(
        points=points, crest=crest, root=root, pointed=pointed
    )


def fill_radius(
    diameter: float,
    pitch: float,
    standard: str = "print45",
    offset: float = 0.0,
    internal: bool = False,
    face_radius: float = 0.0,
) -> float | None:
    """Return the target crest radius when a cylinder face needs filling/building up, or ``None``."""
    outline = tooth_outline(
        diameter=diameter,
        pitch=pitch,
        standard=standard,
        offset=offset,
        internal=internal,
        reach=0.0,
    )
    crest = outline.crest
    pointed = outline.pointed
    near = _CREST_ON_FACE * float(pitch)
    if internal:
        if face_radius <= crest + near:
            return None
    else:
        if face_radius >= crest - near:
            return None
    if not pointed:
        return crest
    return (
        max(crest - float(pitch) / 4.0, crest / 2.0)
        if internal
        else crest + float(pitch) / 4.0
    )


def chamfer_radius(
    diameter: float,
    pitch: float,
    standard: str = "print45",
    offset: float = 0.0,
    internal: bool = False,
) -> float:
    """Radius where a 45° lead-in chamfer meets the end face of the thread (mm)."""
    root = tooth_outline(
        diameter=diameter,
        pitch=pitch,
        standard=standard,
        offset=offset,
        internal=internal,
        reach=0.0,
    ).root
    return (
        root + _CHAMFER_PAST_ROOT * float(pitch)
        if internal
        else root - _CHAMFER_PAST_ROOT * float(pitch)
    )


def _make_helix_by_turns(
    pitch: float,
    turns: int,
    radius: float,
    z_start: float,
    left_handed: bool = False,
) -> cq.Wire:
    """Build a multi-turn helix wire with one edge per turn for robust OCCT booleans."""
    builder = BRepBuilderAPI_MakeWire()
    for i in range(turns):
        turn_wire = cq.Wire.makeHelix(
            pitch=pitch,
            height=pitch,
            radius=radius,
            center=cq.Vector(0.0, 0.0, z_start + i * pitch),
            dir=cq.Vector(0.0, 0.0, 1.0),
            lefthand=left_handed,
        )
        builder.Add(turn_wire.wrapped)
    return cq.Wire(builder.Wire())


def _sweep_helical_tooth(
    points: list[tuple[float, float]],
    pitch: float,
    z_from: float,
    z_to: float,
    left_handed: bool = False,
    phase_offset: float = 0.0,
) -> cq.Workplane:
    """Sweep a 2D ``(r, z)`` tooth polygon helically from ``z_from - pitch`` across ``turns``."""
    turns = math.ceil((z_to - z_from) / pitch - 1e-9) + 3
    if turns > MAX_THREAD_TURNS:
        raise ValueError(
            f"The thread would take {turns} turns — at most {MAX_THREAD_TURNS}; "
            "give it a coarser pitch or a shorter length."
        )
    z_start = z_from - 1.5 * pitch + phase_offset
    r_mid = sum(r for r, _ in points) / len(points)
    z_mid = sum(z for _, z in points) / len(points)
    helix = _make_helix_by_turns(
        pitch=pitch,
        turns=turns,
        radius=r_mid,
        z_start=z_start + z_mid,
        left_handed=left_handed,
    )

    pts_3d = [cq.Vector(r, 0.0, z_start + z) for r, z in points]
    if pts_3d[0] != pts_3d[-1]:
        pts_3d.append(pts_3d[0])
    profile_wire = cq.Wire.makePolygon(pts_3d)

    pipe = BRepOffsetAPI_MakePipeShell(helix.wrapped)
    pipe.SetMode(gp_Dir(0.0, 0.0, 1.0))
    pipe.Add(profile_wire.wrapped, False, False)
    pipe.Build()
    pipe.MakeSolid()

    raw_solid = TopoDS.Solid_s(pipe.Shape())
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(raw_solid, props)
    if props.Mass() < 0.0:
        raw_solid.Reverse()

    return cq.Workplane(obj=cq.Shape(raw_solid))


class PrintableThread:
    """Parametric 3D-printable 45°-flank (``print45``) or 60° ISO/Unified screw thread.

    Origin lies at ``(0, 0, 0)`` on the bottom face of the thread; the thread
    axis runs along ``+Z`` to ``Z = length``.

    * When ``internal=False`` (default):
      - ``.solid`` returns an external threaded rod / bolt shaft of major
        diameter ``diameter`` and height ``length``.
      - ``.to_cutter()`` returns the annular thread-cutting tool that cuts the
        external thread into a cylindrical boss of ``diameter``.
    * When ``internal=True``:
      - ``.to_cutter()`` (and ``.solid``) returns a positive tapped-bore
        cutter (minor bore + helical internal thread grooves + optional 45°
        countersink chamfers) ready to subtract from a solid block or nut body
        via ``body.cut(thread.to_cutter())``.
      - ``.to_nut()`` returns a complete internally threaded collar or hex nut.
    """

    def __init__(
        self,
        diameter: float = 8.0,
        pitch: float | None = None,
        length: float = 10.0,
        standard: str = "print45",
        internal: bool = False,
        offset: float = 0.0,
        chamfer: bool = False,
        left_handed: bool = False,
    ) -> None:
        self.standard = standard.strip().lower()
        _get_form(self.standard)  # validate standard
        self.diameter = float(diameter)

        if pitch is None:
            matched = size_of(self.standard, self.diameter)
            if matched is not None:
                self.pitch = float(matched["pitches"][0])  # type: ignore[index]
            elif self.standard == "print45":
                nearest = size_for_face("metric", self.diameter, internal=False)
                self.pitch = print_pitch(nearest["pitch"])
            else:
                nearest = size_for_face(self.standard, self.diameter, internal=False)
                self.pitch = float(nearest["pitch"])
        else:
            self.pitch = float(pitch)

        self.length = float(length)
        if not (self.length > 0.0):
            raise ValueError(f"Thread length must be positive; got {self.length}")

        self.internal = bool(internal)
        self.offset = float(offset)
        self.chamfer = bool(chamfer)
        self.left_handed = bool(left_handed)

        problem = thread_problem(
            diameter=self.diameter,
            pitch=self.pitch,
            standard=self.standard,
            offset=self.offset,
        )
        if problem is not None:
            raise ValueError(problem)

        turns = math.ceil((self.length + self.pitch) / self.pitch - 1e-9) + 2
        if turns > MAX_THREAD_TURNS:
            raise ValueError(
                f"The thread would take {turns} turns — at most {MAX_THREAD_TURNS}; "
                "give it a coarser pitch or a shorter length."
            )

        if self.chamfer:
            rc = self.chamfer_radius
            outline = self.tooth_outline()
            face_r = outline.crest if self.internal else self.diameter / 2.0
            reach = (
                rc - max(face_r, outline.crest)
                if self.internal
                else min(face_r, outline.crest) - rc
            )
            if 2.0 * reach >= self.length:
                raise ValueError(
                    f"The thread length ({self.length:.3g} mm) is too short for "
                    f"a 45° chamfer at each end (each {reach:.3g} mm deep)."
                )

    @classmethod
    def from_size(
        cls,
        size: str = "M8",
        length: float = 10.0,
        standard: str = "print45",
        internal: bool = False,
        offset: float = 0.0,
        chamfer: bool = False,
        left_handed: bool = False,
        use_coarse_pitch: bool = False,
    ) -> PrintableThread:
        """Create a :class:`PrintableThread` from a standard size label (e.g. ``'M8'``, ``'Ø8'``)."""
        std = standard.strip().lower()
        _get_form(std)
        cleaned = size.strip()

        if std == "print45" and cleaned in CNC_KITCHEN_PRINT45_SIZES:
            spec = CNC_KITCHEN_PRINT45_SIZES[cleaned]
            pitches = spec["pitches"]  # type: ignore[index]
            pitch_val = float(pitches[1] if use_coarse_pitch and len(pitches) > 1 else pitches[0])  # type: ignore[arg-type]
            return cls(
                diameter=float(spec["diameter"]),  # type: ignore[arg-type]
                pitch=pitch_val,
                length=length,
                standard=std,
                internal=internal,
                offset=offset,
                chamfer=chamfer,
                left_handed=left_handed,
            )

        norm = cleaned.lower()
        if norm.startswith("m") or norm.startswith("ø"):
            try:
                d_val = float(norm[1:])
            except ValueError:
                d_val = -1.0
            matched = size_of(std, d_val) if d_val > 0 else None
            if matched is not None:
                pitches = matched["pitches"]  # type: ignore[assignment]
                pitch_val = float(
                    pitches[1] if use_coarse_pitch and len(pitches) > 1 else pitches[0]  # type: ignore[index]
                )
                return cls(
                    diameter=float(matched["diameter"]),
                    pitch=pitch_val,
                    length=length,
                    standard=std,
                    internal=internal,
                    offset=offset,
                    chamfer=chamfer,
                    left_handed=left_handed,
                )

        for entry in thread_sizes(std):
            if str(entry["label"]).lower() == norm:
                pitches = entry["pitches"]  # type: ignore[assignment]
                pitch_val = float(
                    pitches[1] if use_coarse_pitch and len(pitches) > 1 else pitches[0]  # type: ignore[index]
                )
                return cls(
                    diameter=float(entry["diameter"]),
                    pitch=pitch_val,
                    length=length,
                    standard=std,
                    internal=internal,
                    offset=offset,
                    chamfer=chamfer,
                    left_handed=left_handed,
                )

        raise ValueError(
            f"Unknown size '{size}' for standard '{standard}'. "
            f"Available: {[s['label'] for s in thread_sizes(std)]}"
        )

    @property
    def thread_depth(self) -> float:
        """Radial thread depth ``h`` from major to minor radius (mm)."""
        return thread_depth(self.pitch, self.standard)

    @property
    def minor_diameter(self) -> float:
        """Basic minor diameter ``D - 2 * h`` (mm)."""
        return minor_diameter(self.diameter, self.pitch, self.standard)

    @property
    def pitch_diameter(self) -> float:
        """Basic pitch diameter where tooth width equals gap width (mm)."""
        return pitch_diameter(self.diameter, self.pitch, self.standard)

    @property
    def max_offset(self) -> float:
        """Maximum radial clearance offset allowed for this pitch and standard (mm)."""
        return max_thread_offset(self.pitch, self.standard)

    @property
    def chamfer_radius(self) -> float:
        """Radius at which a 45° lead-in chamfer meets the end face (mm)."""
        return chamfer_radius(
            self.diameter,
            self.pitch,
            self.standard,
            self.offset,
            self.internal,
        )

    @property
    def designation(self) -> str:
        """Human-readable thread designation (e.g. ``'Ø8×2.25 print'``, ``'M8×1.25'``)."""
        return thread_name(self.standard, self.diameter, self.pitch)

    def tooth_outline(
        self,
        reach: float | None = None,
        face: float | None = None,
    ) -> _ToothOutlineResult:
        """Return the ``(r, z)`` tooth outline for this thread."""
        root_r = tooth_outline(
            diameter=self.diameter,
            pitch=self.pitch,
            standard=self.standard,
            offset=self.offset,
            internal=self.internal,
            reach=0.0,
        ).root
        default_reach = (
            self.pitch / 2.0
            if self.internal
            else min(self.pitch / 2.0, root_r / 2.0)
        )
        return tooth_outline(
            diameter=self.diameter,
            pitch=self.pitch,
            standard=self.standard,
            offset=self.offset,
            internal=self.internal,
            reach=default_reach if reach is None else float(reach),
            face=face,
        )

    def _build_chamfer_ring(self, at_z: float, out_dir: int) -> cq.Workplane:
        """Build a 360° revolved 45° chamfer cutter ring at ``at_z`` (`out_dir` in {-1, +1})."""
        P = self.pitch
        edge = P / 8.0
        rc = self.chamfer_radius
        tooth = self.tooth_outline()
        face_r = tooth.crest if self.internal else self.diameter / 2.0
        bore = min(face_r, tooth.crest)
        c_far = (
            max(bore - edge, bore / 2.0)
            if self.internal
            else max(face_r, self.diameter / 2.0) + edge
        )
        c_near = rc + edge if self.internal else rc - edge

        if self.internal:
            tri = [
                (c_near, -edge),
                (c_far, rc - c_far),
                (c_far, -edge),
            ]
        else:
            tri = [
                (c_near, -edge),
                (c_far, c_far - rc),
                (c_far, -edge),
            ]

        pts_xz = [(r, at_z - out_dir * s) for r, s in tri]
        return (
            cq.Workplane("XZ")
            .polyline(pts_xz)
            .close()
            .revolve(360.0, (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        )

    def _build_external_cutter(self) -> cq.Workplane:
        """Build the annular boolean cutter for an external thread (`internal=False`)."""
        P = self.pitch
        face_r = self.diameter / 2.0
        tooth = self.tooth_outline(face=face_r)
        margin = P / 2.0
        inner = tooth.root
        outer = max(face_r, self.diameter / 2.0) + margin
        run = P / 2.0
        z_from = -run
        z_to = self.length + run

        screwed = _sweep_helical_tooth(
            points=tooth.points,
            pitch=P,
            z_from=z_from,
            z_to=z_to,
            left_handed=self.left_handed,
            phase_offset=0.0,
        )
        band = (
            cq.Workplane("XY", origin=(0.0, 0.0, z_from))
            .circle(outer)
            .circle(inner)
            .extrude(z_to - z_from)
        )
        cutter = band.cut(screwed)

        if self.chamfer:
            bot_ring = self._build_chamfer_ring(0.0, -1)
            top_ring = self._build_chamfer_ring(self.length, 1)
            cutter = cutter.union(bot_ring).union(top_ring)

        return cutter

    def _build_internal_bore_cutter(self) -> cq.Workplane:
        """Build the positive bore cutter for an internal thread (`internal=True`).

        Subtracting this cutter from a solid block or pre-bored nut cuts both
        the minor-diameter core bore and the helical internal thread grooves
        (plus 45° mouth countersinks when ``chamfer=True``).
        """
        P = self.pitch
        tooth = self.tooth_outline(face=None)
        outer = tooth.root
        run = P / 2.0
        z_from = -run
        z_to = self.length + run

        # Shift internal nut tooth by pitch / 2 so its helical groove aligns in
        # phase with an external bolt tooth placed at the same (0, 0, 0) origin.
        screwed = _sweep_helical_tooth(
            points=tooth.points,
            pitch=P,
            z_from=z_from,
            z_to=z_to,
            left_handed=self.left_handed,
            phase_offset=P / 2.0,
        )
        # Solid cylinder of radius `tooth.root` minus the nut's helical tooth
        # leaves the exact inner bore (r < crest) + helical thread grooves
        # (crest <= r <= root) with zero helix-on-cylinder coplanar faces.
        band = (
            cq.Workplane("XY", origin=(0.0, 0.0, z_from))
            .circle(outer)
            .extrude(z_to - z_from)
        )
        bore_cutter = band.cut(screwed)

        if self.chamfer:
            bot_ring = self._build_chamfer_ring(0.0, -1)
            top_ring = self._build_chamfer_ring(self.length, 1)
            bore_cutter = bore_cutter.union(bot_ring).union(top_ring)

        return bore_cutter

    def to_cutter(self, profile: ToleranceProfile | str | None = None) -> cq.Workplane:
        """Return a boolean-subtraction cutter for this thread.

        Satisfies :class:`~vibe_cading.mechanical.protocols.CutterProtocol`.
        When ``internal=True``, returns the positive tapped-bore cutter to
        subtract from a host block or nut blank.  When ``internal=False``,
        returns the annular external-thread cutter to subtract from a
        cylindrical shaft of ``self.diameter``.
        """
        if self.internal:
            return self._build_internal_bore_cutter()
        return self._build_external_cutter()

    @property
    def solid(self) -> cq.Workplane:
        """Return the 3D thread solid bounded between ``Z = 0`` and ``Z = length``.

        For ``internal=False``, returns the external threaded bolt/rod shaft.
        For ``internal=True``, returns the positive internal-thread bore
        volume (clipped to ``Z = 0..length``).
        """
        if not self.internal:
            shaft = (
                cq.Workplane("XY")
                .circle(self.diameter / 2.0)
                .extrude(self.length)
            )
            return shaft.cut(self._build_external_cutter())

        clip_r = self.diameter / 2.0 + self.pitch + abs(self.offset) + 1.0
        clip_cyl = cq.Workplane("XY").circle(clip_r).extrude(self.length)
        return clip_cyl.intersect(self._build_internal_bore_cutter())

    def to_nut(
        self,
        outer_diameter: float | None = None,
        across_flats: float | None = None,
    ) -> cq.Workplane:
        """Generate a cylindrical collar or hex nut with this internal thread cut through it."""
        internal_thread = (
            self
            if self.internal
            else PrintableThread(
                diameter=self.diameter,
                pitch=self.pitch,
                length=self.length,
                standard=self.standard,
                internal=True,
                offset=self.offset,
                chamfer=self.chamfer,
                left_handed=self.left_handed,
            )
        )
        min_od = self.diameter + 2.0 * self.offset + 2.0 * max(self.pitch, 1.5)
        if across_flats is not None:
            af = float(across_flats)
            if af <= self.diameter + 2.0 * self.offset:
                raise ValueError(
                    f"across_flats ({af}) must exceed major thread diameter "
                    f"({self.diameter + 2.0 * self.offset})."
                )
            circum_d = af / math.cos(math.radians(30.0))
            blank = cq.Workplane("XY").polygon(6, circum_d).extrude(self.length)
        else:
            od = float(min_od if outer_diameter is None else outer_diameter)
            if od <= self.diameter + 2.0 * self.offset:
                raise ValueError(
                    f"outer_diameter ({od}) must exceed major thread diameter "
                    f"({self.diameter + 2.0 * self.offset})."
                )
            blank = cq.Workplane("XY").circle(od / 2.0).extrude(self.length)

        return blank.cut(internal_thread.to_cutter())

    @classmethod
    def demo(cls, **kwargs) -> list[tuple[cq.Workplane, str, str]]:
        """Show a print45 M8 external bolt shaft next to a matching print45 M8 hex nut."""
        bolt = cls.from_size("M8", length=10.0, standard="print45", offset=0.1, chamfer=True)
        nut = cls.from_size(
            "M8", length=8.0, standard="print45", internal=True, offset=0.1, chamfer=True
        )
        return [
            (bolt.solid.translate((-8.0, 0.0, 0.0)), "M8 print45 Bolt", "royalblue"),
            (nut.to_nut(across_flats=13.0).translate((8.0, 0.0, 0.0)), "M8 print45 Nut", "gold"),
        ]


# Alias for callers importing ThreadProfile
ThreadProfile = PrintableThread

__all__ = [
    "CNC_KITCHEN_PRINT45_SIZES",
    "ISO_METRIC_TABLE",
    "MAX_THREAD_TURNS",
    "PRINT45_FORM",
    "PrintableThread",
    "THREAD_FORMS",
    "THREAD_STANDARDS",
    "ThreadProfile",
    "ThreadStandard",
    "UNC_TABLE",
    "UNF_TABLE",
    "V60_FORM",
    "chamfer_radius",
    "fill_radius",
    "max_thread_offset",
    "minor_diameter",
    "pitch_diameter",
    "print_pitch",
    "size_for_face",
    "size_of",
    "thread_depth",
    "thread_designation",
    "thread_name",
    "thread_problem",
    "thread_sizes",
    "threads_per_inch",
    "tooth_outline",
]
