# NIfTI Analyzer

Standalone desktop app that takes a NIfTI CT volume and reports its geometry,
HU statistics, and contrast phase. No server, no ports: the window's page
talks to Python directly over pywebview's js_api bridge (WebKit2GTK).

Reported metrics: dimensions, coverage, pixel width, slice thickness,
orientation code and viewing plane, anisotropy, HU range, and the contrast
phase predicted by TotalSegmentator's `totalseg_get_phase` (phase,
probability, post-injection time, its range, and stddev).

## Install

One installer for Linux, Windows, and macOS. It needs Python 3.10+ on the
system, checks everything else itself, and installs the full application
including the contrast-phase model stack (torch, ~2 GB; CPU-only torch is
chosen automatically when no NVIDIA GPU is present).

```bash
python3 install.py
```

On Windows: `py install.py`. Re-run after `git pull` to upgrade;
`python3 install.py --uninstall` reverts everything.

Platform notes. On Linux the window uses the system WebKit2GTK stack
(`python3-gi`, `gir1.2-webkit2-4.1`); the installer detects what is missing
and offers the exact install command. On Windows the window uses the
preinstalled Edge WebView2 runtime, nothing extra needed.

## Run

`nifti-analyzer` from the terminal, or the NIfTI Analyzer entry in the
application menu (Linux) / Start Menu (Windows). Pick a `.nii` / `.nii.gz`
file with Browse or drag-and-drop, press Run analysis. The header metrics
take under a second; the contrast phase runs TotalSegmentator (seconds on
GPU, minutes on CPU). Cancel kills the model's whole process tree. Save
result writes the report as CSV via a native save dialog.
