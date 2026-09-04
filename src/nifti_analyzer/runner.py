"""Job runner and JS bridge for the standalone pywebview app.

No HTTP anywhere: the window's JavaScript calls these methods directly over
pywebview's js_api bridge. The analysis runs on the user's file in place (no
upload copy), in a worker thread, with the phase subprocess in its own
process group so cancel kills the whole tree.
"""

from __future__ import annotations

import csv
import io
import re
import threading
import time
import uuid
from pathlib import Path

import nibabel as nib
import webview

from . import analysis

STEPS = ["Reading header", "Loading voxel data", "Computing statistics", "Writing report"]


class Job:
    def __init__(self, path: Path):
        self.id = uuid.uuid4().hex[:12]
        self.path = path
        self.name = path.name
        self.size_mb = round(path.stat().st_size / 1e6, 1)
        self.state = "running"  # running | done | cancelled | error
        self.step = 0
        self.started = time.time()
        self.elapsed = 0.0
        self.error = ""
        self.rows: list[dict] = []
        self.proc_holder: dict = {"proc": None}
        self.cancel_requested = False


class Api:
    """Exposed to the page as window.pywebview.api."""

    def __init__(self):
        self.window: webview.Window | None = None
        self.job: Job | None = None
        self._lock = threading.Lock()

    # -- job lifecycle -----------------------------------------------------

    def _run(self, j: Job) -> None:
        try:
            j.step = 0
            img = nib.load(j.path)
            rows = analysis.geometry_rows(img)
            if j.cancel_requested:
                return
            j.step = 1
            rows += analysis.hu_rows(img)
            if j.cancel_requested:
                return
            j.step = 2
            rows += analysis.phase_rows(j.path, j.proc_holder,
                                        cancel_check=lambda: j.cancel_requested)
            if j.cancel_requested:
                return
            j.step = 3
            j.rows = rows
            j.elapsed = time.time() - j.started
            j.state = "done"
        except Exception as exc:
            j.state = "error"
            j.error = analysis.scrub_paths(str(exc))
        finally:
            if j.state == "running":
                j.state = "cancelled"
            # The file is the user's own; nothing to delete.

    def analyze(self, path_str: str) -> dict:
        p = Path(path_str)
        if not p.name.endswith((".nii", ".nii.gz")):
            return {"ok": False, "error": "expected a .nii or .nii.gz file"}
        if not p.is_file():
            return {"ok": False, "error": f"file not found: {p.name}"}
        with self._lock:
            if self.job is not None and self.job.state == "running":
                return {"ok": False, "error": "a job is already running"}
            self.job = Job(p)
            threading.Thread(target=self._run, args=(self.job,), daemon=True).start()
            return {"ok": True, "job_id": self.job.id, "name": self.job.name,
                    "size_mb": self.job.size_mb}

    def status(self) -> dict:
        j = self.job
        if j is None:
            return {"state": "idle"}
        if j.state == "done":
            pct = 100
        elif j.step < 2:
            pct = (5, 20)[j.step]
        elif j.step == 2:
            pct = min(90, 30 + int((time.time() - j.started) / 4))
        else:
            pct = 95
        return {"state": j.state, "step": j.step, "step_label": STEPS[j.step],
                "pct": pct, "name": j.name, "size_mb": j.size_mb,
                "elapsed": round(time.time() - j.started, 1), "error": j.error,
                "job_id": j.id}

    def result(self) -> dict:
        j = self.job
        if j is None or j.state != "done":
            return {"ok": False, "error": "no finished result"}
        return {"ok": True, "name": j.name, "size_mb": j.size_mb,
                "elapsed": round(j.elapsed, 1), "rows": j.rows}

    def cancel(self, job_id: str | None = None) -> dict:
        j = self.job
        if j is None or j.state != "running":
            return {"ok": True, "cancelled": False}
        if job_id is not None and job_id != j.id:
            return {"ok": False, "error": "job id does not match the running job"}
        j.cancel_requested = True
        proc = j.proc_holder.get("proc")
        if proc is not None:
            analysis.kill_proc_tree(proc)
        return {"ok": True, "cancelled": True}

    def cleanup(self) -> None:
        """Kill any running job's process group. Called when the window closes."""
        j = self.job
        if j is not None and j.state == "running":
            j.cancel_requested = True
            proc = j.proc_holder.get("proc")
            if proc is not None:
                analysis.kill_proc_tree(proc)

    # -- file dialogs and export -------------------------------------------

    def pick_file(self) -> dict | None:
        res = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("NIfTI (*.nii;*.nii.gz;*.gz)", "All files (*.*)"))
        if not res:
            return None
        p = Path(res[0] if isinstance(res, (list, tuple)) else res)
        return {"path": str(p), "name": p.name,
                "size_mb": round(p.stat().st_size / 1e6, 1)}

    def csv_text(self) -> str:
        j = self.job
        if j is None or j.state != "done":
            return ""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["metric", "value", "unit"])
        for r in j.rows:
            writer.writerow([r["label"], r["value"], r["unit"]])
        return buf.getvalue()

    def suggested_filename(self) -> str:
        j = self.job
        stem = (j.name if j else "volume").removesuffix(".gz").removesuffix(".nii")
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem) or "volume"
        return f"{stem}_report.csv"

    def save_csv(self) -> dict:
        text = self.csv_text()
        if not text:
            return {"ok": False, "error": "no finished result"}
        res = self.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=self.suggested_filename())
        if not res:
            return {"ok": False, "cancelled": True}
        target = Path(res if isinstance(res, str) else res[0])
        try:
            target.write_text(text)
        except OSError as exc:
            return {"ok": False, "error": analysis.scrub_paths(str(exc))}
        return {"ok": True, "path": str(target)}
