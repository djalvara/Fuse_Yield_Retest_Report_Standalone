# Fuse Yield Retest Report Standalone

Standalone utilities for pulling VPO bin attributes from MIDAS and generating an interactive yield/retest HTML report.

## Files
- `vpo_bin_attributes_pull.py`: pulls raw MIDAS data, curates it, and writes timestamped CSV output.
- `Yield_Retest_Report_Create.py`: builds a self-contained HTML report from a curated CSV.
- `.github/skills/fuse-yield-and-retest-reporting/SKILL.md`: workspace skill describing setup, workflow, and troubleshooting.

## Requirements
- Windows PowerShell.
- Python 3.10 or newer.
- Access to the required Intel Python package index.
- `IntelChain.pem` present locally at the repository root.

## Installation

### Step 1: Prepare Environment

Check Python installation:

```powershell
python --version
# Expected output: Python 3.10.9 or higher
```

Configure network only if you are behind a corporate proxy and it is not already auto-configured:

```powershell
$env:HTTP_PROXY = "http://proxy-jf.intel.com:912"
$env:HTTPS_PROXY = "http://proxy-jf.intel.com:912"
$env:NO_PROXY = "localhost,127.0.0.1,.intel.com"
```

### Step 2: Create Virtual Environment

Purpose: isolate project dependencies and avoid conflicts with system packages.

```powershell
python -m venv .venv
```

Verification:

```powershell
.\.venv\Scripts\python.exe --version
```

### Step 3: Activate Virtual Environment

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Windows Command Prompt:

```bat
.\.venv\Scripts\activate.bat
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Visual indicator: your prompt is prefixed with `(.venv)` when activation succeeds.

### Step 4: Install Keyring (Recommended)

Install this before full requirements when using Intel PyPI authentication:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install "keyring<23.7.0" -i https://intelpypi.intel.com/pythonsv/production
```

Why this version: newer keyring versions may have compatibility issues with the Intel PyPI mirror; `<23.7.0` is typically stable in this workflow.

### Step 5: Install Project Dependencies

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt -i https://intelpypi.intel.com/pythonsv/production
```

Intel PyPI authentication notes:
- First run can prompt for Intel SSO credentials.
- Keyring caches credentials for later installs.

### Step 6: Setup MIDAS Database Access

Prerequisites:
1. If `IntelChain.pem` is not present in the repository root, download it from [Intel SharePoint](https://intel.sharepoint.com/sites/Midasshare/MIDAS%20Library/).
2. Place it in the workspace root as `IntelChain.pem`.

Set MIDAS certificate for the current session:

```powershell
$env:MIDAS_CERTIFICATE = ".\\IntelChain.pem"
```

Verification:

```powershell
echo $env:MIDAS_CERTIFICATE
# Expected output ends with \IntelChain.pem
```

Optional cleanup:

```powershell
Remove-Item Env:MIDAS_CERTIFICATE
echo $env:MIDAS_CERTIFICATE
# Should be empty
```

## Typical Workflow

Activate the virtual environment and set the certificate before running any script:

```powershell
.\.venv\Scripts\Activate.ps1
$env:MIDAS_CERTIFICATE = ".\IntelChain.pem"
```

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

## Product Examples
Use these as copy-paste starting points for the supported products.

GNR recent pull and report generation:

```powershell
python .\vpo_bin_attributes_pull.py --product GNR --days-ago 5
python .\Yield_Retest_Report_Create.py --csv .\output_dir\vpo_bin_attrs_GNR_5d_<timestamp>.csv
```

GNR pull with report generation in one step:

```powershell
python .\vpo_bin_attributes_pull.py --product GNR --days-ago 5 --create-report
```

DMR exact month pull and keep raw CSV:

```powershell
python .\vpo_bin_attributes_pull.py --product DMR --month 2026-03 --keep-raw
python .\Yield_Retest_Report_Create.py --csv .\output_dir\vpo_bin_attrs_DMR_2026-03_<timestamp>.csv
```

CWF recent pull for latest runs only:

```powershell
python .\vpo_bin_attributes_pull.py --product CWF --days-ago 7 --latest-only
python .\Yield_Retest_Report_Create.py --csv .\output_dir\vpo_bin_attrs_CWF_7d_<timestamp>.csv
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
For workflow details, troubleshooting, and report interpretation, see `.github/skills/fuse-yield-and-retest-reporting/SKILL.md`.