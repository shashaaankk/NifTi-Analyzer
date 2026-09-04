# NIfTI Analyzer

Standalone desktop app that takes a NIfTI CT volume and reports its geometry,
HU statistics, and contrast phase. No server, no ports: the window's page
talks to Python directly over pywebview's js_api bridge (WebKit2GTK).

Reported metrics: dimensions, coverage, pixel width, slice thickness,
orientation code and viewing plane, anisotropy, HU range, and the contrast
phase predicted by TotalSegmentator's `totalseg_get_phase` (phase,
probability, post-injection time, its range, and stddev).

## System requirements (Linux)

pywebview uses the system GTK stack, which pip cannot provide:
`python3-gi` and `gir1.2-webkit2-4.1` (Debian/Ubuntu names). The venv must
see them: set `include-system-site-packages = true` in `.venv/pyvenv.cfg`.

## Install

```bash
python -m venv .venv
sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' .venv/pyvenv.cfg
.venv/bin/pip install '.[phase]'
```

The `phase` extra pulls TotalSegmentator and torch (~2 GB) plus xgboost
(which upstream forgets to declare). Without it the app still reports
everything header-derived and marks the phase row unavailable.

## Run

```bash
.venv/bin/nifti-analyzer
```

(`nifti-analyzer-desktop` is the same entry point.) Pick a `.nii` / `.nii.gz`
file with Browse or drag-and-drop, press Run analysis. The header metrics
take under a second; the contrast phase runs TotalSegmentator (seconds on
GPU, minutes on CPU). Cancel kills the model's whole process tree. Save
result writes the report as CSV via a native save dialog.
