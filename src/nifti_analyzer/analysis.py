"""Volume analysis: header geometry, HU statistics, contrast phase.

Everything except the contrast phase is header arithmetic plus a slab-wise
pass over the voxel array. The contrast phase shells out to TotalSegmentator's
totalseg_get_phase (segments ~20 structures, then an XGBoost classifier over
their median HU), which takes minutes and is run in its own process group so
a cancel can kill the whole tree, with stderr on a file so no wait can block
on pipes held by grandchildren.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import nibabel as nib
import numpy as np

# Dominant axis of the slice-normal column -> viewing plane. Codes are nibabel
# axis codes (direction of increasing index), so L/R both mean the x axis.
PLANE_BY_AXIS = {"L": "sagittal", "R": "sagittal",
                 "P": "coronal", "A": "coronal",
                 "I": "axial", "S": "axial"}


def row(label: str, value: str, unit: str = "", info: str = "") -> dict:
    return {"label": label, "value": value, "unit": unit, "info": info}


PHASE_CITATION = ("(TotalSegmentator totalseg_get_phase; Wasserthal et al., "
                  "Radiology: AI 2023, doi:10.1148/ryai.230024)")

_ABS_PATH = re.compile(r"(?:/[\w.\-+@]+){2,}")


def scrub_paths(text: str) -> str:
    """Reduce absolute filesystem paths to their basename so server-side
    locations never reach the browser."""
    return _ABS_PATH.sub(lambda m: m.group(0).rsplit("/", 1)[-1], text)


def kill_proc_tree(proc: subprocess.Popen) -> None:
    """Terminate the subprocess and every descendant (its process group)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def geometry_rows(img: nib.Nifti1Image) -> list[dict]:
    if len(img.shape) < 3:
        raise ValueError(f"expected a 3D volume, got a {len(img.shape)}D image")
    shape = img.shape[:3]
    zooms = [float(z) for z in img.header.get_zooms()[:3]]
    affine = img.affine

    axcodes = "".join(nib.aff2axcodes(affine))
    plane = PLANE_BY_AXIS[axcodes[2]]
    coverage = [n * z for n, z in zip(shape, zooms)]
    anisotropy = zooms[2] / min(zooms[:2])

    return [
        row("Dimensions", f"{shape[0]} × {shape[1]} × {shape[2]}", "vox"),
        row("Coverage", " × ".join(f"{c:.0f}" for c in coverage), "mm"),
        row("Pixel width", f"{zooms[0]:.3f} × {zooms[1]:.3f}", "mm"),
        row("Slice thickness", f"{zooms[2]:.3f}", "mm"),
        row("Orientation", f"{axcodes} → {plane}"),
        row("Anisotropy", f"{anisotropy:.2f}",
            info="The ratio of slice thickness to in-plane pixel width. It answers "
                 "whether voxels are cubes or elongated boxes. A value near 1 means "
                 "detail is equal in all directions."),
    ]


def hu_rows(img: nib.Nifti1Image) -> list[dict]:
    # Slab-wise min/max over the scaled data proxy: never materialises or
    # caches the full volume (a ~GB float32 copy for a routine hepatic CT).
    lo, hi = np.inf, -np.inf
    for k in range(0, img.shape[2], 32):
        slab = np.asanyarray(img.dataobj[:, :, k:k + 32], dtype=np.float32)
        lo = min(lo, float(slab.min()))
        hi = max(hi, float(slab.max()))
    return [
        row("HU range", f"{lo:.0f} – {hi:.0f}", "HU"),
    ]


def phase_available() -> bool:
    return importlib.util.find_spec("totalsegmentator") is not None


def start_phase(path: Path, out_json: Path, stderr_handle) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "totalsegmentator.bin.totalseg_get_phase",
           "-i", str(path), "-o", str(out_json)]
    # Own process group so kill_proc_tree reaches every descendant; stderr on
    # a file (not a pipe) so wait() cannot block on fds inherited by children.
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr_handle,
                            text=True, start_new_session=True)


def phase_rows(path: Path, proc_holder: dict, cancel_check=lambda: False) -> list[dict]:
    """Run the phase prediction. proc_holder["proc"] exposes the Popen so the
    caller can kill its process group; cancel_check is re-read around the
    spawn so a cancel can never be lost in the handoff window."""
    if not phase_available():
        return [row("Contrast phase", "unavailable (TotalSegmentator not installed)")]
    if cancel_check():
        return [row("Contrast phase", "cancelled")]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "phase.json"
        err_path = Path(tmp) / "stderr.log"
        with err_path.open("w") as err:
            proc = start_phase(path, out, err)
            proc_holder["proc"] = proc
            if cancel_check():
                kill_proc_tree(proc)
            proc.wait()
            proc_holder["proc"] = None
        stderr_txt = err_path.read_text(errors="replace")
        if proc.returncode != 0:
            if cancel_check():
                return [row("Contrast phase", "cancelled")]
            if proc.returncode < 0:
                return [row("Contrast phase",
                            f"failed: terminated by signal {-proc.returncode}")]
            return [row("Contrast phase",
                        "failed: " + scrub_paths(stderr_txt.strip()[-160:]))]
        data = json.loads(out.read_text())
        return [
            row("Contrast phase", str(data.get("phase", "?")),
                info="The scan's timing window relative to contrast injection, mapped "
                     "from the predicted post-injection time. " + PHASE_CITATION),
            row("Phase probability", str(data.get("probability", "?")),
                info="High when the predicted time sits centrally in its phase window, "
                     "lower near a boundary. A fixed lookup, not a model output. "
                     "(totalseg_get_phase)"),
            row("Post-injection time", str(data.get("pi_time", "?")), "s",
                info="Seconds since contrast injection, predicted from the median HU "
                     "of 20 segmented structures. Documented mean absolute error "
                     "5.55 s. (totalseg_get_phase)"),
            row("Post-injection time range",
                f"{data.get('pi_time_min', '?')} – {data.get('pi_time_max', '?')}", "s",
                info="Lowest and highest prediction of the 5-model ensemble. A narrow "
                     "range means the models agree. (totalseg_get_phase)"),
            row("Post-injection time stddev", str(data.get("stddev", "?")), "s",
                info="Spread of the 5 ensemble predictions. Low values mean higher "
                     "confidence. (totalseg_get_phase)"),
        ]
