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

"""Unit and kinematic tests for CNC Kitchen heat-set inserts and 45°-flank printable threads."""

from __future__ import annotations

import math

import pytest

from vibe_cading.mechanical import (
    CNC_KITCHEN_EXTRA_DEPTH_MM,
    CNC_KITCHEN_INSERTS,
    CNC_KITCHEN_PRINT45_SIZES,
    HeatSetInsert,
    PRINT45_FORM,
    PrintableThread,
    ThreadProfile,
    V60_FORM,
    chamfer_radius,
    fill_radius,
    insert_hole,
    insert_size,
    max_thread_offset,
    minor_diameter,
    pitch_diameter,
    print_pitch,
    size_for_face,
    size_of,
    thread_depth,
    thread_designation,
    thread_name,
    thread_problem,
    thread_sizes,
    threads_per_inch,
    tooth_outline,
)
from vibe_cading.mechanical.protocols import CutterProtocol


# ===========================================================================
# 1. CNC Kitchen Heat-Set Inserts
# ===========================================================================

_EXPECTED_CNC_KITCHEN_TABLE = [
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
]


@pytest.mark.parametrize(
    "key,label,length,hole,wall",
    _EXPECTED_CNC_KITCHEN_TABLE,
    ids=[row[0] for row in _EXPECTED_CNC_KITCHEN_TABLE],
)
def test_cnckitchen_insert_table_values(
    key: str, label: str, length: float, hole: float, wall: float
) -> None:
    """Every CNC Kitchen insert size matches ScanRuler's table dimensions."""
    assert len(CNC_KITCHEN_INSERTS) == 12
    assert key in CNC_KITCHEN_INSERTS
    assert label in CNC_KITCHEN_INSERTS

    spec = CNC_KITCHEN_INSERTS[key]
    assert spec["label"] == label
    assert spec["length"] == pytest.approx(length)
    assert spec["insert_length"] == pytest.approx(length)
    assert spec["hole"] == pytest.approx(hole)
    assert spec["hole_diameter"] == pytest.approx(hole)
    assert spec["depth"] == pytest.approx(length + CNC_KITCHEN_EXTRA_DEPTH_MM)
    assert spec["wall"] == pytest.approx(wall)
    assert spec["min_wall"] == pytest.approx(wall)
    assert spec["min_boss_diameter"] == pytest.approx(hole + 2.0 * wall)

    # Alias lookup via ScanRuler space/quote label
    by_label = insert_size(label)
    assert by_label is not None
    assert by_label["hole"] == pytest.approx(hole)

    hole_info = insert_hole(label)
    assert hole_info["radius"] == pytest.approx(hole / 2.0)
    assert hole_info["depth"] == pytest.approx(length + 1.0)


@pytest.mark.parametrize(
    "alias,expected_key",
    [
        ("M3 short", "M3_short"),
        ("m3_short", "M3_short"),
        ("M3 Voron", "M3_Voron"),
        ("m3 voron", "M3_Voron"),
        ("M4 short", "M4_short"),
        ("M5 short", "M5_short"),
        ('1/4"-20', "1/4-20"),
        ("1/4-20", "1/4-20"),
    ],
)
def test_cnckitchen_insert_aliases(alias: str, expected_key: str) -> None:
    """Space, underscore, quote, and case variants resolve to the canonical spec."""
    ins = HeatSetInsert.cnckitchen(alias)
    expected = CNC_KITCHEN_INSERTS[expected_key]
    assert ins.hole_diameter == pytest.approx(float(expected["hole"]))
    assert ins.insert_length == pytest.approx(float(expected["length"]))
    assert ins.depth == pytest.approx(float(expected["depth"]))
    assert ins.min_wall == pytest.approx(float(expected["wall"]))
    assert ins.min_boss_diameter == pytest.approx(float(expected["min_boss_diameter"]))


def test_cnckitchen_unknown_size_raises() -> None:
    """Unknown insert size raises ValueError."""
    assert insert_size("M99") is None
    with pytest.raises(ValueError, match="Unknown CNC Kitchen"):
        HeatSetInsert.cnckitchen("M99")
    with pytest.raises(ValueError, match="Unknown CNC Kitchen"):
        insert_hole("M99")


def test_cnckitchen_cutter_and_boss_geometry() -> None:
    """CNC Kitchen M3 insert cutter and boss have exact expected dimensions and volume."""
    ins = HeatSetInsert.cnckitchen("M3")
    assert isinstance(ins, CutterProtocol)
    assert ins.hole_diameter == pytest.approx(4.0)
    assert ins.insert_length == pytest.approx(5.7)
    assert ins.depth == pytest.approx(6.7)
    assert ins.min_wall == pytest.approx(1.6)
    assert ins.min_boss_diameter == pytest.approx(7.2)

    cutter = ins.to_cutter()
    assert len(cutter.solids().vals()) == 1
    bb = cutter.val().BoundingBox()
    assert bb.xmin == pytest.approx(-2.0, abs=1e-2)
    assert bb.xmax == pytest.approx(2.0, abs=1e-2)
    assert bb.zmin == pytest.approx(-6.7, abs=1e-2)
    assert bb.zmax == pytest.approx(0.0, abs=1e-2)
    expected_vol = math.pi * (2.0**2) * 6.7
    assert cutter.val().Volume() == pytest.approx(expected_vol, rel=1e-3)

    # Taper option widens top opening and increases volume
    tapered = HeatSetInsert.cnckitchen("M3", taper=0.4)
    assert tapered.top_diameter == pytest.approx(4.4)
    assert tapered.bot_diameter == pytest.approx(4.0)
    assert tapered.to_cutter().val().Volume() > expected_vol

    # Through-hole appends 100 mm shaft below -depth
    thru = HeatSetInsert.cnckitchen("M3", through_hole=True, clearance_diameter=3.2)
    bb_thru = thru.to_cutter().val().BoundingBox()
    assert bb_thru.zmin == pytest.approx(-(6.7 + 100.0), abs=1e-2)

    # Boss with blind floor (height > depth)
    boss = ins.to_boss(height=8.0)
    assert len(boss.solids().vals()) == 1
    bb_boss = boss.val().BoundingBox()
    assert bb_boss.xmax - bb_boss.xmin == pytest.approx(7.2, abs=1e-2)
    assert bb_boss.zmin == pytest.approx(-8.0, abs=1e-2)
    assert bb_boss.zmax == pytest.approx(0.0, abs=1e-2)
    expected_boss_vol = math.pi * (3.6**2) * 8.0 - expected_vol
    assert boss.val().Volume() == pytest.approx(expected_boss_vol, rel=1e-3)


def test_existing_voron_and_ruthex_presets_preserved() -> None:
    """Existing Voron and Ruthex presets remain 100% backward-compatible."""
    v_m3 = HeatSetInsert.voron("M3")
    assert v_m3.r_top == pytest.approx(2.2)
    assert v_m3.r_bot == pytest.approx(2.0)
    assert v_m3.depth == pytest.approx(5.5)

    r_m4 = HeatSetInsert.ruthex("M4")
    assert r_m4.r_top == pytest.approx(3.05)
    assert r_m4.r_bot == pytest.approx(2.8)
    assert r_m4.depth == pytest.approx(8.5)


# ===========================================================================
# 2. ScanRuler 45°-Flank Printable Threads & 60° ISO Metric Threads
# ===========================================================================


def test_thread_form_identities() -> None:
    """Both PRINT45 and V60 thread forms satisfy crest + root + 2 * depth * tan == 1."""
    for form in (PRINT45_FORM, V60_FORM):
        total = form.crest + form.root + 2.0 * form.depth * form.tan
        assert total == pytest.approx(1.0, abs=1e-12)
    assert PRINT45_FORM.tan == pytest.approx(1.0)
    assert PRINT45_FORM.depth == pytest.approx(0.3)
    assert PRINT45_FORM.crest == pytest.approx(0.2)
    assert PRINT45_FORM.root == pytest.approx(0.2)


@pytest.mark.parametrize(
    "size_key,expected_d,expected_print_p,expected_coarse_p",
    [
        ("M3", 3.0, 1.0, 0.5),
        ("M4", 4.0, 1.25, 0.7),
        ("M5", 5.0, 1.5, 0.8),
        ("M6", 6.0, 1.75, 1.0),
        ("M8", 8.0, 2.25, 1.25),
        ("M10", 10.0, 2.75, 1.5),
        ("M12", 12.0, 3.0, 1.75),
        ("M16", 16.0, 3.5, 2.0),
        ("M20", 20.0, 4.5, 2.5),
        ("M24", 24.0, 5.5, 3.0),
        ("M64", 64.0, 11.0, 6.0),
    ],
)
def test_print45_sizes_and_pitches(
    size_key: str,
    expected_d: float,
    expected_print_p: float,
    expected_coarse_p: float,
) -> None:
    """CNC_KITCHEN_PRINT45_SIZES covers M3..M64 with depth-matched print pitches."""
    assert len(CNC_KITCHEN_PRINT45_SIZES) == 27
    spec = CNC_KITCHEN_PRINT45_SIZES[size_key]
    assert spec["diameter"] == pytest.approx(expected_d)
    assert spec["print_pitch"] == pytest.approx(expected_print_p)
    assert spec["coarse_pitch"] == pytest.approx(expected_coarse_p)
    assert spec["pitches"] == [expected_print_p, expected_coarse_p]
    assert print_pitch(expected_coarse_p) == pytest.approx(expected_print_p)

    # Also accessible by Ø label and numeric diameter
    assert CNC_KITCHEN_PRINT45_SIZES[f"Ø{int(expected_d)}"]["diameter"] == pytest.approx(expected_d)
    assert CNC_KITCHEN_PRINT45_SIZES[expected_d]["diameter"] == pytest.approx(expected_d)


def test_thread_dimensions_and_tooth_outline_math() -> None:
    """Verify thread_depth, minor_diameter, pitch_diameter, max_thread_offset, and tooth_outline."""
    # M8 print45 with pitch = 2.0 mm
    d = 8.0
    p = 2.0
    assert thread_depth(p, "print45") == pytest.approx(0.6)
    assert minor_diameter(d, p, "print45") == pytest.approx(6.8)
    # pitch_diameter = D - (P/2 - crest*P) / tan = 8.0 - (1.0 - 0.4) / 1.0 = 7.4
    assert pitch_diameter(d, p, "print45") == pytest.approx(7.4)
    assert max_thread_offset(p, "print45") == pytest.approx(p / (4.0 * math.sqrt(2.0)))

    # External tooth outline with 0 offset: check 45° flank slope (|dz / dr| == 1.0)
    ext = tooth_outline(d, p, standard="print45", offset=0.0, internal=False, reach=0.5)
    assert ext.crest == pytest.approx(4.0)
    assert ext.root == pytest.approx(3.4)
    assert ext.pointed is False
    pts = ext.points
    assert len(pts) == 6
    # Points 1 -> 2 is the lower 45° flank from bend to crest
    r1, z1 = pts[1]
    r2, z2 = pts[2]
    assert abs((z2 - z1) / (r2 - r1)) == pytest.approx(1.0, abs=1e-9)

    # Internal tooth outline with 0 offset: check 45° flank slope
    intr = tooth_outline(d, p, standard="print45", offset=0.0, internal=True, reach=0.5)
    assert intr.crest == pytest.approx(3.4)
    assert intr.root == pytest.approx(4.0)
    assert intr.pointed is False
    ipts = intr.points
    assert len(ipts) == 6
    ir0, iz0 = ipts[0]
    ir1, iz1 = ipts[1]
    assert abs((iz1 - iz0) / (ir1 - ir0)) == pytest.approx(1.0, abs=1e-9)

    # Designations and helper lookups
    assert thread_designation("print45", 16.0, 3.5) == "Ø16×3.5 print"
    assert thread_designation("metric", 16.0, 2.0) == "M16×2"
    assert thread_name("print45", 15.0, 2.0) == "Ø15 × 2 print"
    assert threads_per_inch(25.4 / 20.0) == pytest.approx(20.0)
    assert size_of("metric", 8.0)["label"] == "M8"  # type: ignore[index]
    assert size_for_face("print45", 8.0, internal=False) == {"diameter": 8.0, "pitch": 2.25}
    assert chamfer_radius(8.0, 2.0, "print45", offset=0.0, internal=False) == pytest.approx(3.2)
    assert chamfer_radius(8.0, 2.0, "print45", offset=0.0, internal=True) == pytest.approx(4.2)
    assert fill_radius(8.0, 2.0, "print45", offset=0.0, internal=False, face_radius=3.5) == pytest.approx(4.0)
    assert fill_radius(8.0, 2.0, "print45", offset=0.0, internal=False, face_radius=4.0) is None
    assert len(thread_sizes("unc")) == 25
    assert len(thread_sizes("unf")) == 24


def test_thread_validation_rejects_invalid_params() -> None:
    """thread_problem and PrintableThread reject invalid diameter, pitch, and offset."""
    max_off = max_thread_offset(2.25, "print45")
    assert thread_problem(0.0, 2.25, "print45", 0.0) is not None
    assert thread_problem(8.0, 0.0, "print45", 0.0) is not None
    assert thread_problem(8.0, 2.25, "print45", -0.05) is not None
    assert thread_problem(8.0, 2.25, "print45", max_off) is not None
    assert thread_problem(3.0, 4.0, "print45", 0.0) is not None

    with pytest.raises(ValueError, match="no thickness at the pitch diameter"):
        PrintableThread(diameter=8.0, pitch=2.25, length=6.0, standard="print45", offset=max_off)

    with pytest.raises(ValueError, match="zero or more"):
        PrintableThread(diameter=8.0, pitch=2.25, length=6.0, standard="print45", offset=-0.1)


def test_printable_thread_3d_geometry_and_kinematic_engagement() -> None:
    """Verify 3D BRep generation, zero seated collision with offset, and positive pull-out engagement."""
    # M8 print45 with 0.1 mm clearance offset per flank/crest/root
    bolt = PrintableThread.from_size(
        "M8", length=6.0, standard="print45", internal=False, offset=0.1
    )
    nut_thread = ThreadProfile.from_size(
        "M8", length=6.0, standard="print45", internal=True, offset=0.1
    )
    assert isinstance(bolt, CutterProtocol)
    assert isinstance(nut_thread, CutterProtocol)
    assert bolt.designation == "Ø8×2.25 print"

    bolt_solid = bolt.solid
    nut_solid = nut_thread.to_nut(outer_diameter=12.0)

    assert len(bolt_solid.solids().vals()) == 1
    assert len(nut_solid.solids().vals()) == 1

    # Bolt volume must lie strictly between minor-core cylinder and major cylinder
    r_minor = (bolt.minor_diameter / 2.0) - bolt.offset
    r_major = (bolt.diameter / 2.0) - bolt.offset
    v_min = math.pi * (r_minor**2) * bolt.length
    v_max = math.pi * (r_major**2) * bolt.length
    assert v_min < bolt_solid.val().Volume() < v_max

    # 1. Seated at rest (dz = 0): clearance offset separates all thread faces -> zero collision
    seated_interference = bolt_solid.intersect(nut_solid)
    seated_vol = (
        seated_interference.val().Volume()
        if seated_interference.solids().vals()
        else 0.0
    )
    assert seated_vol == pytest.approx(0.0, abs=1e-3)

    # 2. Displaced axially by pitch / 2 along the pull-out axis: teeth engage -> positive interference
    pulled_bolt = bolt_solid.translate((0.0, 0.0, bolt.pitch / 2.0))
    engaged_interference = pulled_bolt.intersect(nut_solid)
    assert len(engaged_interference.solids().vals()) >= 1
    assert engaged_interference.val().Volume() > 5.0


def test_printable_thread_chamfer_metric_and_lefthanded() -> None:
    """Verify 45° lead-in chamfer, 60° ISO metric standard, and left-handed threads."""
    plain = PrintableThread(diameter=8.0, pitch=2.25, length=6.0, standard="print45", chamfer=False)
    chamfered = PrintableThread(diameter=8.0, pitch=2.25, length=6.0, standard="print45", chamfer=True)
    assert len(chamfered.solid.solids().vals()) == 1
    # Chamfer removes material from the bolt ends
    assert chamfered.solid.val().Volume() < plain.solid.val().Volume()

    # Left-handed thread has 1 solid and identical volume to right-handed
    lh = PrintableThread(diameter=8.0, pitch=2.25, length=6.0, standard="print45", left_handed=True)
    assert len(lh.solid.solids().vals()) == 1
    assert lh.solid.val().Volume() == pytest.approx(plain.solid.val().Volume(), rel=1e-3)

    # ISO metric 60° thread
    m8_metric = PrintableThread.from_size("M8", length=5.0, standard="metric", offset=0.05)
    assert m8_metric.pitch == pytest.approx(1.25)
    assert m8_metric.designation == "M8×1.25"
    assert len(m8_metric.solid.solids().vals()) == 1
