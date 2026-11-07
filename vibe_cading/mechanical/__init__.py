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

"""vibe_cading.mechanical — mid-level package.

Contributors may import symbols directly from the leaf modules / subpackages
that own them, e.g.::

    from vibe_cading.mechanical.holes import ClearanceHole
    from vibe_cading.mechanical.inserts import CNC_KITCHEN_INSERTS, HeatSetInsert
    from vibe_cading.mechanical.screws import MetricMachineScrew
    from vibe_cading.mechanical.threads import PrintableThread, ThreadProfile

For convenience, the CNC Kitchen insert and 3D-printable thread primitives
are also re-exported here.
"""

from vibe_cading.mechanical.inserts import (
    CNC_KITCHEN_EXTRA_DEPTH_MM,
    CNC_KITCHEN_INSERTS,
    HeatSetInsert,
    insert_hole,
    insert_size,
)
from vibe_cading.mechanical.threads import (
    CNC_KITCHEN_PRINT45_SIZES,
    PRINT45_FORM,
    PrintableThread,
    THREAD_FORMS,
    THREAD_STANDARDS,
    ThreadProfile,
    ThreadStandard,
    V60_FORM,
    chamfer_radius,
    fill_radius,
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

__all__ = [
    "CNC_KITCHEN_EXTRA_DEPTH_MM",
    "CNC_KITCHEN_INSERTS",
    "CNC_KITCHEN_PRINT45_SIZES",
    "HeatSetInsert",
    "PRINT45_FORM",
    "PrintableThread",
    "THREAD_FORMS",
    "THREAD_STANDARDS",
    "ThreadProfile",
    "ThreadStandard",
    "V60_FORM",
    "chamfer_radius",
    "fill_radius",
    "insert_hole",
    "insert_size",
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
