# Yield Retest Report Standalone

Standalone utilities for pulling VPO bin attributes from MIDAS and generating an interactive yield/retest HTML report.

## Files
- `vpo_bin_attributes_pull.py`: pulls raw MIDAS data, curates it, and writes timestamped CSV output.
- `Yield_Retest_Report_Create.py`: builds a self-contained HTML report from a curated CSV.
- `.github/skills/qbot-vpo-reporting/SKILL.md`: workspace skill describing setup, workflow, and troubleshooting.

## Requirements
- Windows PowerShell.
- Python 3.10 or newer.
- Access to the required Intel Python package index.
- `IntelChain.pem` present locally at the repository root.

## Install
Create and activate a virtual environment, then install the Python dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt -i https://intelpypi.intel.com/pythonsv/production
```

If your environment needs the older keyring package used in related Intel tooling, install it before the requirements file:

```powershell
python -m pip install "keyring<23.7.0" -i https://intelpypi.intel.com/pythonsv/production
```

## Setup
Set the certificate path in each new terminal session:

```powershell
$env:MIDAS_CERTIFICATE = ".\\IntelChain.pem"
```

If you use a virtual environment, activate it before running either script.

## Typical Workflow
Pull a curated CSV for a recent day window:

```powershell
python .\vpo_bin_attributes_pull.py --product GNR --days-ago 5
```

Generate an HTML report from a specific curated CSV:

```powershell
python .\Yield_Retest_Report_Create.py --csv .\output_dir\vpo_bin_attrs_GNR_5d_<timestamp>.csv
```

Generate the report automatically as part of the pull:

```powershell
python .\vpo_bin_attributes_pull.py --product GNR --days-ago 5 --create-report
```

## Output
By default, both scripts use `output_dir` under the repository root.

Common generated artifacts:
- `vpo_bin_attrs_<PRODUCT>_<PERIOD>_<TIMESTAMP>.csv`: curated CSV output.
- `vpo_bin_attrs_<PRODUCT>_<PERIOD>_raw_<TIMESTAMP>.csv`: raw MIDAS CSV when `--keep-raw` is used.
- `vpo_bin_attrs_interactive_report_<csv_stem>.html`: generated HTML report.
- `.vpo_bin_attributes_pull.lock`: lock file used to prevent concurrent pulls.

`output_dir/` is gitignored so generated artifacts stay local by default.

## Notes
- Do not run multiple instances of `vpo_bin_attributes_pull.py` at the same time.
- `GNR` uses operations `6197` and `6262`; `CWF` and `DMR` use `6197`.
- If `--csv` is omitted for report generation, the latest non-raw CSV in `output_dir` is used.

## More Detail
For workflow details, troubleshooting, and report interpretation, see `.github/skills/qbot-vpo-reporting/SKILL.md`.