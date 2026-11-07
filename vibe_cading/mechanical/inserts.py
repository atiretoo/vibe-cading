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

"""
Standard heat-set threaded inserts for 3D printed parts.
Generates straight or tapered voids for installing brass inserts with a soldering iron.

Supports generic dimensions or manufacturer-specific profiles (CNC Kitchen, Ruthex, Voron).
CNC Kitchen insert specifications and wall/relief rules adapted from ScanRuler
(``plugins/reverse/core/inserts.ts``, SPDX-License-Identifier: AGPL-3.0-only).
"""

from __future__ import annotations

from typing import Literal

import cadquery as cq

from vibe_cading.print_settings import ToleranceProfile


# Extra pocket depth beyond insert length for displaced plastic (mm),
# per CNC Kitchen / ScanRuler specification.
CNC_KITCHEN_EXTRA_DEPTH_MM: float = 1.0


def _normalize_insert_key(label: str) -> str:
    """Normalize an insert size label for case-, space-, and quote-insensitive lookup."""
    cleaned = (
        label.strip()
        .lower()
        .replace('"', "")
        .replace("'", "")
        .replace("_", " ")
    )
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.replace(" - ", "-").replace(" -", "-").replace("- ", "-")
    return cleaned


class _InsertSpecTable(dict):
    """Dictionary of CNC Kitchen insert specs supporting flexible label lookup."""

    def __init__(self, entries: list[tuple[str, dict[str, float | str]]]) -> None:
        super().__init__(entries)
        self._lookup: dict[str, str] = {}
        for key, spec in entries:
            self._lookup[_normalize_insert_key(key)] = key
            self._lookup[_normalize_insert_key(str(spec["label"]))] = key

    def _resolve(self, key: object) -> str | None:
        if not isinstance(key, str):
            return None
        if super().__contains__(key):
            return key
        return self._lookup.get(_normalize_insert_key(key))

    def __contains__(self, key: object) -> bool:
        return self._resolve(key) is not None

    def __getitem__(self, key: str) -> dict[str, float | str]:
        resolved = self._resolve(key)
        if resolved is None:
            raise KeyError(key)
        return super().__getitem__(resolved)

    def get(self, key: str, default: object = None) -> object:
        resolved = self._resolve(key)
        if resolved is None:
            return default
        return super().__getitem__(resolved)


_RAW_CNC_KITCHEN_SPECS: tuple[tuple[str, str, float, float, float], ...] = (
    # (key, canonical ScanRuler label, length_mm, hole_mm, min_wall_mm)
    ("M2", "M2", 3.0, 3.2, 1.3),
    ("M2.5", "M2.5", 4.0, 4.0, 1.6),
    ("M3", "M3", 5.7, 4.0, 1.6),
    ("M3_short", "M3 short", 3.0, 4.0, 1.6),
    ("M3_Voron", "M3 Voron", 4.0, 4.4, 1.3),
    ("M4", "M4", 8.1, 5.6, 2.1),
    ("M4_short", "M4 short", 4.0, 5.6, 2.1),
    ("M5", "M5", 9.5, 6.4, 2.6),
    ("M5_short", "M5 short", 5.8, 6.4, 2.6),
    ("M6", "M6", 12.7, 8.0, 3.3),
    ("M8", "M8", 12.7, 9.7, 3.3),
    ("1/4-20", '1/4"-20', 12.7, 8.0, 3.3),
)

CNC_KITCHEN_INSERTS: dict[str, dict[str, float | str]] = _InsertSpecTable([
    (
        key,
        {
            "key": key,
            "label": label,
            "length": length,
            "insert_length": length,
            "hole": hole,
            "hole_diameter": hole,
            "depth": round(length + CNC_KITCHEN_EXTRA_DEPTH_MM, 2),
            "wall": wall,
            "min_wall": wall,
            "min_boss_diameter": round(hole + 2.0 * wall, 4),
        },
    )
    for key, label, length, hole, wall in _RAW_CNC_KITCHEN_SPECS
])


def insert_size(label: str) -> dict[str, float | str] | None:
    """Look up a CNC Kitchen insert specification by label, ignoring case and spacing."""
    spec = CNC_KITCHEN_INSERTS.get(label)
    return dict(spec) if isinstance(spec, dict) else None


def insert_hole(size: str | dict[str, float | str]) -> dict[str, float]:
    """Return the hole ``radius`` and ``depth`` (mm) for a CNC Kitchen insert."""
    if isinstance(size, str):
        spec = insert_size(size)
        if spec is None:
            raise ValueError(
                f"Unknown CNC Kitchen insert size '{size}'. "
                f"Available: {list(CNC_KITCHEN_INSERTS.keys())}"
            )
    else:
        spec = size
    hole = float(spec["hole"])
    length = float(spec["length"])
    return {
        "radius": hole / 2.0,
        "depth": round((length + CNC_KITCHEN_EXTRA_DEPTH_MM) * 100.0) / 100.0,
    }


class HeatSetInsert:
    """Generic or standard heat-set threaded insert.

    Generates the cutter void required to melt the insert cleanly into
    plastic.  Supports both tapered pockets (preventing plastic from
    extruding over the top face during insertion) and straight cylindrical
    pockets with 1 mm relief depth as specified by CNC Kitchen.

    Through-vs-blind: the insert pocket is fundamentally **blind**
    (defined floor at ``-depth``).  When ``through_hole=True`` an
    arbitrarily deep clearance shaft (100 mm) is appended below the
    pocket so a screw can pass completely through.
    """

    # Class-level overcut for the optional through-hole clearance shaft —
    # 100 mm guarantees clearance through any reasonable host body.
    _THROUGH_SHAFT_LENGTH: float = 100.0

    def __init__(
        self,
        top_diameter: float,
        bot_diameter: float,
        depth: float,
        through_hole: bool = False,
        clearance_diameter: float = 3.2,
    ):
        """Create a custom insert profile based on raw geometries.

        :param top_diameter: Diameter of the void at the surface (mm).
        :param bot_diameter: Diameter of the void at the bottom (mm) — usually
            equal to ``top_diameter`` (straight pocket) or slightly smaller
            for taper.
        :param depth: Total depth of the insert void (mm).
        :param through_hole: When True, ``to_cutter`` appends a deep
            clearance shaft beneath the insert pocket so a screw can
            pass through the host body.  Migrated from a ``to_cutter``
            kwarg to a constructor argument per Phase 4 so the
            ``CutterProtocol`` call-time signature stays uniform.
        :param clearance_diameter: Diameter of the optional through-hole shaft
            (mm) — e.g. 3.2 for an M3 clearance hole.  Ignored when
            ``through_hole`` is False.
        """
        self.top_diameter = float(top_diameter)
        self.bot_diameter = float(bot_diameter)
        self.hole_diameter = float(bot_diameter)
        self.r_top = top_diameter / 2.0
        self.r_bot = bot_diameter / 2.0
        self.depth = depth
        self.insert_length = float(depth)
        self.min_wall = 1.6
        self.min_boss_diameter = round(self.hole_diameter + 2.0 * self.min_wall, 4)
        self.through_hole = through_hole
        self.clearance_diameter = clearance_diameter
        self.label: str | None = None

    def to_cutter(self, profile: ToleranceProfile | None = None) -> cq.Workplane:
        """Generate a boolean cutter for the insert pocket.

        :param profile: Currently unused — insert dimensions are
            constructor-driven (top_diameter / bot_diameter / depth).  The
            argument is accepted to satisfy ``CutterProtocol``.
        """
        # Tapered (or straight when r_top == r_bot) loft for the insert
        pocket = (
            cq.Workplane("XY")
            .circle(self.r_top)
            .workplane(offset=-self.depth)
            .circle(self.r_bot)
            .loft()
        )

        if self.through_hole:
            # Add an arbitrarily deep cylinder below it for the screw shaft clearance
            shaft = (
                cq.Workplane("XY")
                .transformed(offset=cq.Vector(0, 0, -self.depth))
                .circle(self.clearance_diameter / 2.0)
                .extrude(-self._THROUGH_SHAFT_LENGTH)
            )
            pocket = pocket.union(shaft)

        return pocket

    def to_boss(self, height: float | None = None) -> cq.Workplane:
        """Generate a minimum-wall cylindrical mounting boss with the insert pocket cut out.

        The boss outer diameter equals ``self.min_boss_diameter``
        (``hole_diameter + 2 * min_wall``), with its top opening at ``Z = 0``
        extending in ``-Z`` by ``height`` (defaults to ``self.depth``).
        """
        boss_height = float(self.depth if height is None else height)
        if boss_height <= 0.0:
            raise ValueError(f"Boss height must be positive; got {boss_height}")
        boss = (
            cq.Workplane("XY")
            .circle(self.min_boss_diameter / 2.0)
            .extrude(-boss_height)
        )
        return boss.cut(self.to_cutter())

    # --- Standard Presets ---

    @classmethod
    def cnckitchen(
        cls,
        size: str = "M3",
        through_hole: bool = False,
        clearance_diameter: float = 3.2,
        taper: float = 0.0,
    ) -> HeatSetInsert:
        """Official CNC Kitchen heat-set insert specification (from ScanRuler).

        Uses a straight cylindrical hole of diameter ``D3`` (or optional lead-in
        ``taper`` added to ``top_diameter``), pocket depth ``length + 1.0`` mm
        to accommodate plastic pushed ahead of the insert, and records the
        minimum wall thickness ``min_wall`` and ``min_boss_diameter``.

        Supported sizes: ``"M2"``, ``"M2.5"``, ``"M3"``, ``"M3_short"``
        (``"M3 short"``), ``"M3_Voron"`` (``"M3 Voron"``), ``"M4"``,
        ``"M4_short"`` (``"M4 short"``), ``"M5"``, ``"M5_short"``
        (``"M5 short"``), ``"M6"``, ``"M8"``, ``"1/4-20"`` (``'1/4"-20'``).
        """
        spec = insert_size(size)
        if spec is None:
            raise ValueError(
                f"Unknown CNC Kitchen size '{size}'. "
                f"Available: {list(CNC_KITCHEN_INSERTS.keys())}"
            )
        hole = float(spec["hole"])
        length = float(spec["length"])
        depth = float(spec["depth"])
        wall = float(spec["wall"])
        inst = cls(
            top_diameter=hole + float(taper),
            bot_diameter=hole,
            depth=depth,
            through_hole=through_hole,
            clearance_diameter=clearance_diameter,
        )
        inst.hole_diameter = hole
        inst.insert_length = length
        inst.min_wall = wall
        inst.min_boss_diameter = round(hole + 2.0 * wall, 4)
        inst.label = str(spec["label"])
        return inst

    @classmethod
    def voron(cls, size: Literal["M3", "M4"] = "M3", through_hole: bool = False, clearance_diameter: float = 3.2) -> HeatSetInsert:
        """Standard insert void sizes specified by the Voron Design manual.

        Typical Voron insert for M3 is M3x5x4.
        """
        profiles = {
            "M3": {"top_diameter": 4.4, "bot_diameter": 4.0, "depth": 5.5},
            "M4": {"top_diameter": 6.0, "bot_diameter": 5.5, "depth": 8.1},
        }
        if size not in profiles:
            raise ValueError(f"Unknown Voron size '{size}'. Available: {list(profiles.keys())}")
        return cls(through_hole=through_hole, clearance_diameter=clearance_diameter, **profiles[size])

    @classmethod
    def ruthex(cls, size: Literal["M2", "M2.5", "M3", "M3_short", "M4", "M5"] = "M3", through_hole: bool = False, clearance_diameter: float = 3.2) -> HeatSetInsert:
        """Ruthex / CNC Kitchen tapered geometry standard.

        Slightly wider / longer than Voron specs.
        """
        profiles = {
            "M2": {"top_diameter": 3.6, "bot_diameter": 3.2, "depth": 4.0},
            "M2.5": {"top_diameter": 4.1, "bot_diameter": 3.6, "depth": 4.5},
            "M3": {"top_diameter": 4.4, "bot_diameter": 4.0, "depth": 6.0},       # Standards are 5.7 mm long, 6.0 void depth
            "M3_short": {"top_diameter": 4.4, "bot_diameter": 4.0, "depth": 3.3}, # 3.0 mm long short variant
            "M4": {"top_diameter": 6.1, "bot_diameter": 5.6, "depth": 8.5},
            "M5": {"top_diameter": 7.1, "bot_diameter": 6.5, "depth": 10.0},
        }
        if size not in profiles:
            raise ValueError(f"Unknown Ruthex size '{size}'. Available: {list(profiles.keys())}")
        return cls(through_hole=through_hole, clearance_diameter=clearance_diameter, **profiles[size])

    @classmethod
    def demo(cls, **kwargs) -> list[tuple[cq.Workplane, str, str]]:
        """Show a generic insert next to Voron M3 and Ruthex M4 cutters."""
        # 1. Custom generic insert (blind)
        custom = cls(top_diameter=4.5, bot_diameter=4.0, depth=4.0)

        # 2. Manufacturer-specific presets — Voron M3 with through-hole
        voron_m3 = cls.voron("M3", through_hole=True, clearance_diameter=3.2)
        ruthex_m4 = cls.ruthex("M4")

        return [
            (custom.to_cutter().translate((-10, 0, 0)),
             "Custom 4.5x4 Insert", "royalblue"),
            (voron_m3.to_cutter().translate((0, 0, 0)),
             "Voron M3 (Through-hole)", "gold"),
            (ruthex_m4.to_cutter().translate((15, 0, 0)),
             "Ruthex M4", "tomato"),
        ]
