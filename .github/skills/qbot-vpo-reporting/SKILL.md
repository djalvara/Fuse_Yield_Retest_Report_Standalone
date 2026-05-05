---
name: qbot-vpo-reporting
description: "Use when working on the VPO reporting workflow, including vpo_bin_attributes_pull.py, Yield_Retest_Report_Create.py, setup, execution, troubleshooting, and report interpretation."
---

# QBOT VPO Reporting Skill

## Purpose
This skill documents the end-to-end workflow for:
- `TOOLS/vpo_bin_attributes_pull.py` (data pull and curation)
- `TOOLS/Yield_Retest_Report_Create.py` (interactive HTML report generation)

It is designed to be shared with teammates so they can run, troubleshoot, and interpret outputs consistently.

### Tester Identity Key (Quick Reference)
- Tester identity = `FACILITY` (lab geo location) + `Testing Entity` (tool name) + `UNIT_TESTER_SITE_ID` (cell).
- In this workflow, "where unit testing / VPO runs happened" is represented by that 3-field tester identity.

## Starting Point for New Teammates and Agents

Use this section first before running either script.

### 1) Minimum Requirements
- Windows PowerShell terminal.
- Python 3.10+ available.
- Intel certificate file at `IntelChain.pem` (workspace root).
- Access to Intel PyPI for dependency installation:
  - `https://intelpypi.intel.com/pythonsv/production`

### 2) Recommended Python Environment
For end-to-end pull + report generation, use the DDA tool environment because it already contains the required data and MIDAS packages.

Recommended interpreter:
- `applications.analytics.dda-tool/.venv/Scripts/python.exe`

### 3) One-Time Setup (if environment is not ready)
```powershell
cd applications.analytics.dda-tool
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install "keyring<23.7.0" -i https://intelpypi.intel.com/pythonsv/production
python -m pip install -r requirements.txt -i https://intelpypi.intel.com/pythonsv/production
```

### 4) Session Setup (every new terminal)
```powershell
$env:MIDAS_CERTIFICATE = ".\\IntelChain.pem"
```

Optional import path setup if you run qbot_flow modules from `applications.analytics.dda-tool`:
```powershell
$env:PYTHONPATH = "applications.analytics.dda-tool/src;applications.ai.qbot-agents/src"
```

### 5) Smoke Test (first validation)
Run a short pull and generate a report from that CSV:

```powershell
& "applications.analytics.dda-tool/.venv/Scripts/python.exe" TOOLS\vpo_bin_attributes_pull.py --product GNR --days-ago 2
& "applications.analytics.dda-tool/.venv/Scripts/python.exe" TOOLS\Yield_Retest_Report_Create.py --csv TOOLS\output_dir\<new_curated_csv>.csv
```

Expected outcome:
- A curated CSV in `TOOLS/output_dir`.
- A matching interactive HTML report in `TOOLS/output_dir`.
- No lock-file conflict (`.vpo_bin_attributes_pull.lock`) while only one pull is running.

## What This Workflow Produces
1. A curated CSV with stable schema and derived fields.
2. An interactive HTML report with:
- KPI cards
- Yield drilldown chart (`INTERFACE_BIN -> FUNCTIONAL_BIN -> DATA_BIN`)
- Recovery chart that mirrors drill level in retest mode
- Retest-linked dynamic table (top tester cells by retest run count)
- Linked or unlinked chart-to-dataset filtering
- Column-level filtering with dropdown suggestions
- CSV export for the filtered dataset

## Script 1: Data Pull and Curation
File: `TOOLS/vpo_bin_attributes_pull.py`

### Core Behavior
- Submits exactly one MIDAS query per script execution.
- Uses product-specific operation mapping:
  - `GNR -> 6197, 6262`
  - `CWF -> 6197`
  - `DMR -> 6197`
  - fallback default: `6197`
- Test program release path convention (relative to HDMXPROGS root):
  - `hdmxprogs/<PRODUCT>`
  - Examples:
    - `hdmxprogs/GNR`
    - `hdmxprogs/CWF`
    - `hdmxprogs/DMR`
- Writes a curated CSV for every successful run.
- Deletes raw MIDAS CSV by default after curated CSV creation.
- Preserves raw MIDAS CSV only when `--keep-raw` is explicitly used.
- Applies strict post-pull date filtering to enforce the requested time window.
- Supports exact calendar month pulls via `--month YYYY-MM`.
- Maps `DATA_BIN` to `Failing_Instance` from product `BinReport.xml` collateral.
- Supports TP release resolution from `PROGRAM_OR_BI_RECIPE_NAME` suffix (last 8 chars).
  - For each resolved TP release, the script uses this canonical source path (relative to HDMXPROGS root):
    `hdmxprogs/<PRODUCT>/<TP_RELEASE>/POR_TP/CLASS_TP/Reports/BinReport.xml`.
  - Local cache mirror path used by the script:
    `TOOLS/tools_collaterals/<PRODUCT>/<TP_RELEASE>/POR_TP/CLASS_TP/Reports/BinReport.xml`.
  - If TP release does not resolve or the TP file is missing, script falls back to product-level `BinReport.xml` under `TOOLS/tools_collaterals/<PRODUCT>` (for example `BinReport_Fallback/BinReport.xml` when present).
- Adds `OPERGROUP` to output as the first curated column.

### Single-Instance Safety (Important)
The script enforces a lock file:
- `TOOLS/output_dir/.vpo_bin_attributes_pull.lock`

If another instance is running, the script exits with guidance. This protects against accidental concurrent MIDAS submissions.

Stale lock handling is built in:
- If the lock file exists but its recorded PID is no longer alive, the script removes that stale lock and retries lock acquisition.
- If the PID is active, the script keeps blocking to prevent multiple MIDAS instances.

### CLI Examples
Run a pull:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/vpo_bin_attributes_pull.py --product GNR --days-ago 5
```

Run an exact month pull:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/vpo_bin_attributes_pull.py --product DMR --month 2026-03
```

Keep raw CSV explicitly:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/vpo_bin_attributes_pull.py --product DMR --month 2026-03 --keep-raw
```

Run latest-only mode:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/vpo_bin_attributes_pull.py --product GNR --days-ago 15 --latest-only
```

Run pull and auto-generate HTML report in one step:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/vpo_bin_attributes_pull.py --product GNR --days-ago 15 --create-report
```

Filter by lot and visual ID:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/vpo_bin_attributes_pull.py --product GNR --days-ago 18 --lot J615177CR --visual-id 440A226X00344
```

### Key Inputs
- `--product` required unless `--describe`
- exactly one of `--days-ago` or `--month` required unless `--describe`
- `--month` format must be `YYYY-MM` and cannot be in the future
- `--output-dir` optional (default `TOOLS/output_dir`)
- `--keep-raw` optional (default is delete raw after curated write)
- `--lot` optional
- `--visual-id` optional
- `--latest-only` optional (default behavior includes non-latest)
- `--create-report` optional flag: after curated CSV is written, automatically invokes `Yield_Retest_Report_Create.py` and writes the HTML report beside the CSV in the same output directory

### Curated Output Schema (Ordered)
1. `OPERGROUP`
2. `LOT`
3. `VISUAL_ID`
4. `FACILITY`
5. `PROGRAM_OR_BI_RECIPE_NAME`
6. `Testing Entity`
7. `UNIT_TESTER_SITE_ID`
8. `Within_LOTS_Latest_Flag`
9. `Within_LOTS_Seq_Num`
10. `S_SPEC`
11. `LOTS Start Date Time`
12. `LOTS End Date Time`
13. `DevRevStep`
14. `FUNCTIONAL_BIN`
15. `INTERFACE_BIN`
16. `DATA_BIN`
17. `Failing_Instance`
18. `FRV_SPEC`

### Typical Outputs
- Day-window mode:
  - Raw CSV: `vpo_bin_attrs_<PRODUCT>_<DAYS>d_raw_<TIMESTAMP>.csv`
  - Curated CSV: `vpo_bin_attrs_<PRODUCT>_<DAYS>d_<TIMESTAMP>.csv`
- Month mode:
  - Raw CSV: `vpo_bin_attrs_<PRODUCT>_<YYYY-MM>_raw_<TIMESTAMP>.csv`
  - Curated CSV: `vpo_bin_attrs_<PRODUCT>_<YYYY-MM>_<TIMESTAMP>.csv`

## Script 2: Interactive Report Builder
File: `TOOLS/Yield_Retest_Report_Create.py`

### Core Behavior
- Reads curated CSV and builds a single self-contained HTML report.
- Auto-discovers latest curated CSV if `--csv` is omitted.
- Computes KPI metadata from CSV content:
  - Product inferred from `PROGRAM_OR_BI_RECIPE_NAME` prefixes.
  - Operations inferred dynamically from unique `OPERGROUP` values.
- Displays `OPERGROUP` as leftmost column in Complete Dataset; `LOTS Start Date Time` and `Workweek` appear as the two rightmost columns.

### Complete Dataset Column Order
1. `OPERGROUP`
2. `LOT`
3. `VISUAL_ID`
4. `FACILITY`
5. `PROGRAM_OR_BI_RECIPE_NAME`
6. `Testing Entity`
7. `UNIT_TESTER_SITE_ID`
8. `Within_LOTS_Latest_Flag`
9. `Within_LOTS_Seq_Num`
10. `S_SPEC`
11. `FUNCTIONAL_BIN`
12. `INTERFACE_BIN`
13. `DATA_BIN`
14. `Failing_Instance`
15. `FRV_SPEC`
16. `LOTS Start Date Time`
17. `Workweek`

Only columns present in the source CSV fieldnames are rendered.

### CLI Examples
Generate report from a specific CSV:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/Yield_Retest_Report_Create.py --csv TOOLS/output_dir/vpo_bin_attrs_GNR_5d_20260417_143951.csv
```

Generate report with custom output path:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/Yield_Retest_Report_Create.py --csv TOOLS/output_dir/vpo_bin_attrs_GNR_5d_20260417_143951.csv --output TOOLS/output_dir/custom_report.html
```

### Output Filename
When `--output` is not specified, the report is written to the current working directory as:
```
yield_and_retest_report_{csv_stem}.html
```
For example, CSV `vpo_bin_attrs_DMR_2026-03_20260505_140531.csv` produces:
```
yield_and_retest_report_vpo_bin_attrs_DMR_2026-03_20260505_140531.html
```
When invoked via `--create-report` from the pull script, the HTML is written beside the curated CSV in `TOOLS/output_dir`.

### Report Interaction Rules
- Drilldown chart levels:
  - Level 0: `INTERFACE_BIN`
  - Level 1: `FUNCTIONAL_BIN`
  - Level 2: `DATA_BIN`
- The upper chart and lower recovery chart share the same drill state.
- In both charts, level 2 bars expose `Failing_Instance` on hover.
- In both charts, level 0/1 bars are clickable to drill down.

### Top N by Retest Run Cells Table (Dynamic)
- Shows top 10 tester cells (Facility | Tester | Cell) ranked by total bin count matching current chart filters.
- This table is always visible and always in sync with the active chart filter state.
- **DATA_BIN injected filter**: clicking a `DATA_BIN`-level bar injects a DATA_BIN filter into this table only, shown as a teal chip below the table. This filter is independent from the column filter row in Complete Dataset (unless Apply graph filters is also enabled).
- **Injected filter clear rules** — the injected DATA_BIN chip and filter are automatically cleared whenever:
  - The **Show only Fuse bins** toggle is changed.
  - The **Exclude bin 1** toggle is changed.
  - The **Retest filter** dropdown is switched between options.
  - Chart navigation moves away from `DATA_BIN` level (drill up or to a different branch).
  - The X on the chip itself is clicked.
- Complete Dataset supports for every column (including `OPERGROUP`):
  - text filter
  - dropdown suggestions
  - pagination
  - export inclusion

### Linked vs Unlinked Filtering
Toggle: `Apply graph filters to Complete Dataset`
- Off:
  - chart interactions do not force dataset graph filters
  - deepest-level (`DATA_BIN`) bar click on either chart does not apply DATA_BIN filter to Complete Dataset
- On:
  - chart context syncs into dataset filters
  - deepest-level (`DATA_BIN`) bar click on either chart applies DATA_BIN filter in Complete Dataset

### Percentage Semantics in Chart
Bar labels show `true% / relative%` when values differ.
- `true%` baseline is fixed to:
  - retest filter = latest runs only (`Y`)
  - includes `INTERFACE_BIN = 1`
- `relative%` is computed from the currently displayed chart subset.

## Recommended End-to-End Runbook
1. Pull data:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/vpo_bin_attributes_pull.py --product GNR --days-ago 5
```
2. Generate report from the new curated CSV:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/Yield_Retest_Report_Create.py --csv TOOLS/output_dir/<new_curated_csv>.csv
```
3. Open generated HTML from `TOOLS/output_dir`.

## Troubleshooting

### 1) Lock Error: "Another vpo_bin_attributes_pull execution is already running"
- Wait for active run to finish.
- If previous run crashed, rerun once: stale lock cleanup is automatic when PID is inactive.
- Only manually remove lock if you have verified no active process and stale-lock cleanup cannot recover.

### 2) Operations Do Not Match Expectation
- Confirm product argument (for example `GNR` includes both `6197` and `6262`).
- In report, verify `Operations Included` KPI (derived from `OPERGROUP` values in source CSV).

### 3) Report Generated But Data Looks Old
- Ensure you passed the intended `--csv` path.
- If omitted, script uses latest curated CSV by file modification time.

### 4) DATA_BIN Click Not Filtering Dataset
- This is expected unless `Apply graph filters to Complete Dataset` is enabled.

## Terminology

### Data Fields

| Term | Definition |
|------|------------|
| `INTERFACE_BIN` | Top-level (least granular) bin classification. The bin hierarchy from least to most granular is: `INTERFACE_BIN → FUNCTIONAL_BIN → DATA_BIN`. A passing unit has `INTERFACE_BIN = 1`; `INTERFACE_BIN = 100` is also a known passing-class value. Used as the root level in drilldown charts. |
| `FUNCTIONAL_BIN` | Mid-level bin classification, one step below `INTERFACE_BIN`. More granular than `INTERFACE_BIN`, less granular than `DATA_BIN`. |
| `DATA_BIN` | Deepest (most granular) bin classification, one step below `FUNCTIONAL_BIN`. Represents the most specific failure code available in MIDAS output. `DATA_BIN = 10010000` is a known passing-class value. |
| `VISUAL_ID` | Unique identifier for a unit (device under test). When referring to "units" in any context, this maps to `VISUAL_ID`. |
| `LOT` | Lot identifier from MIDAS. `LOT` and `VPO` are interchangeable terms — both refer to the same lot entity. |
| `FACILITY` | MIDAS facility/site code for where the lot-unit record was processed. Known codes seen in current VPO pulls include: `CRVC = Costa Rica`, `A04 = Malaysia (Penang)`, `A15 = Malaysia (Kulim)`, `A90 = Israel`, `SVC = Silicon Valley, California`. |
| `Testing Entity` | Tester tool name used to run the unit test. Together with `FACILITY` and `UNIT_TESTER_SITE_ID`, this identifies where unit testing / VPO execution occurred. |
| `UNIT_TESTER_SITE_ID` | Tester cell identifier for the tester tool site. In report context, this is the "Cell" component of a tester identity. |
| `Failing_Instance` | Human-readable label mapped from `DATA_BIN` using product `BinReport.xml` collateral. Added during curation. |
| `Within_LOTS_Latest_Flag` | `Y` if this row is the most recent (latest) test run for a given LOT + VISUAL_ID pair; `N` for all earlier runs. The latest run reflects the current known state of the unit. |
| `Within_LOTS_Seq_Num` | Sequential run counter for a LOT-UNIT pair (1 = first run, 2 = second run, etc.). The maximum value for a pair equals the total number of runs that unit has had in the lot. |
| `Retest_Recovery` | Derived field indicating whether a unit that failed on a prior run subsequently passed on a later run. |
| `OPERGROUP` | Operation group identifier from MIDAS (e.g., `6197`, `6262`). Represents the test operation the data row belongs to. Used as leftmost column in Complete Dataset. |
| `PROGRAM_OR_BI_RECIPE_NAME` | Test program recipe name as stored in MIDAS. Used to infer product and TP release suffix. |
| `FRV_SPEC` | Functional revision spec — test program versioning field from MIDAS. |
| `S_SPEC` | Sort spec from MIDAS. |
| `DevRevStep` | Device/revision/step field from MIDAS. |

### Concepts and Metrics

| Term | Definition |
|------|------------|
| **Pass / Passing unit** | A unit (identified by `VISUAL_ID`) whose latest run is in pass-class bins. Primary KPI pass condition is `INTERFACE_BIN = 1`. Known tied pass-class representations across bin levels include `INTERFACE_BIN = 1`, `INTERFACE_BIN = 100`, and `DATA_BIN = 10010000`. |
| **Yield** | Pass rate computed as the percentage of latest-run units (`Within_LOTS_Latest_Flag = Y`) where `INTERFACE_BIN = 1`. Shown as a KPI card in the report. Nuance: this KPI uses the top-level interface-bin pass condition even though pass-class bins are tied representations across hierarchy levels. |
| **Unit** | A device under test, uniquely identified by its `VISUAL_ID`. The terms "unit" and "VISUAL_ID" are interchangeable. |
| **LOT / VPO** | Lot identifier. `LOT` and `VPO` are interchangeable terms referring to the same entity in MIDAS. |
| **Tester** | The place/tool context where unit testing (VPO execution) occurs, identified by the tuple: `FACILITY` (lab geo location), `Testing Entity` (tool name), and `UNIT_TESTER_SITE_ID` (cell). |
| **Tester cell** | A single tester location slot represented by `UNIT_TESTER_SITE_ID`; unique in practice when paired with `FACILITY` and `Testing Entity`. |
| **LOT-UNIT pair** | A unique combination of `LOT` + `VISUAL_ID`. The atomic unit of analysis for retest and recovery tracking. |
| **Latest run** | The most recent test record for a given LOT-UNIT pair; identified by `Within_LOTS_Latest_Flag = Y`. Represents the current known pass/fail state of the unit. |
| **Retest run** | Any run for a LOT-UNIT pair that is *not* the latest run — i.e., rows where `Within_LOTS_Latest_Flag = N`. These are all prior runs before the most recent result. A unit has been retested if `Within_LOTS_Seq_Num > 1`. |
| **Recovery rate** | For a given bin, the fraction of lot-unit pairs that previously failed at that bin and subsequently passed. Shown in the lower (recovery) chart. |
| **N+Y unique-pair denominator** | The count of distinct lot-unit pairs that had at least one non-latest (`N`) run at a given bin level. Used as the denominator for recovery rate percentages in the lower chart. |
| **True% / Relative%** | Two percentage semantics used in upper chart bar labels when they differ. `True%` is fixed to the full latest-run baseline (including INTERFACE_BIN=1). `Relative%` is computed from the currently displayed (filtered) chart subset. |
| **Drill level** | Current depth of the drilldown chart. Level 0 = `INTERFACE_BIN`, Level 1 = `FUNCTIONAL_BIN`, Level 2 = `DATA_BIN`. |
| **TP release** | Test program release version, resolved from the last 8 characters of `PROGRAM_OR_BI_RECIPE_NAME`. Used to locate product-specific `BinReport.xml` collateral under `TOOLS/tools_collaterals/<PRODUCT>/<TP_RELEASE>/POR_TP/CLASS_TP/Reports/`. |
| **BinReport.xml collateral** | XML collateral file used to map numeric `DATA_BIN` values to descriptive `Failing_Instance` labels (from `<Element name="...">` and child `<Testname name="...">` entries). |
| **MIDAS** | Intel manufacturing data system queried by `vpo_bin_attributes_pull.py` to retrieve per-unit test results. |

### Report UI Components

| Term | Definition |
|------|------------|
| **KPI cards** | Summary metric boxes displayed at the top of the report. Includes: Number of lots, Number of units, Yield, Operations included, Date range. |
| **Facility KPI / legend** | KPI card showing the number of distinct `FACILITY` codes in the loaded CSV plus a dynamic legend that expands only the facilities present in that report. |
| **Upper chart** | The primary drilldown bar chart. In default mode shows yield breakdown ("Yield Analysis"). In dual/retest mode shows retest run counts ("Unit retest runs by bin"). |
| **Lower chart / Recovery chart** | The secondary bar chart, visible only when retest filter is set to retest-fail mode (`Within_LOTS_Latest_Flag = N`). It is titled "Unit recovery rate by bin". Bar labels show `count/denominator (pct%)` format. |
| **Dual mode** | Report state where both the upper and lower charts are displayed (stacked vertically). Activated when retest filter is set to retest-fail mode. |
| **Default mode** | Report state showing only the upper chart ("Yield Analysis"). Active when no retest filter is applied. |
| **Drill level indicator** | UI element in the chart header showing the current drilldown depth (e.g., `INTERFACE_BIN > FUNCTIONAL_BIN`). |
| **Retest Linked Context table** | Dynamic table shown in the KPI grid near Retest. Title updates as `Top N tester cells by retest run count (Dynamic table)`, where `N` is the currently displayed row count (capped to top 3). Table content syncs with the selected chart filter mode and current chart context. Columns: `Facility`, `Testing Entity`, `Unit Tester Site ID`, `Count`. |
| **"Mirrors current top graph drill level"** | Lower chart legend text shown at drill levels 0 and 1, indicating lower chart depth mirrors upper chart depth. At drill level 2, legend changes to "Hover on bar for failing instance details". |
| **Complete Dataset** | The full data table at the bottom of the report, showing all CSV rows with column filters, dropdown suggestions, pagination, and CSV export. |
| **Linked vs. Unlinked filtering** | Toggle (`Apply graph filters to Complete Dataset`). When linked (on), chart bar clicks push filter context into the Complete Dataset. When unlinked (off), the dataset is not affected by chart navigation. |

### File Naming Conventions

| Pattern | Description |
|---------|-------------|
| `vpo_bin_attrs_<PRODUCT>_<DAYS>d_<TIMESTAMP>.csv` | Curated output CSV from day-window mode. |
| `vpo_bin_attrs_<PRODUCT>_<DAYS>d_raw_<TIMESTAMP>.csv` | Raw day-window CSV (kept only with `--keep-raw`). |
| `vpo_bin_attrs_<PRODUCT>_<YYYY-MM>_<TIMESTAMP>.csv` | Curated output CSV from month mode. |
| `vpo_bin_attrs_<PRODUCT>_<YYYY-MM>_raw_<TIMESTAMP>.csv` | Raw month CSV (kept only with `--keep-raw`). |
| `vpo_bin_attrs_interactive_report_<source_csv_name>.html` | Self-contained HTML report generated from a curated CSV. |
| `.vpo_bin_attributes_pull.lock` | Lock file in `output_dir/` preventing concurrent pull executions. |

### Facility Code Legend

| Code | Full Name / Location |
|------|----------------------|
| `CRVC` | Costa Rica |
| `A04` | Malaysia (Penang) |
| `A15` | Malaysia (Kulim) |
| `A90` | Israel |
| `SVC` | Silicon Valley, California |

---

## Team Conventions and Guardrails
- Do not run multiple pull instances concurrently.
- Keep curated CSV artifacts; keep raw CSV only when required by using `--keep-raw`.
- Prefer explicit `--csv` during report generation when validating specific runs.
- Use `OPERGROUP` as the operation truth source in downstream report interpretation.

## Quick Commands Reference
Pull 5-day GNR:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/vpo_bin_attributes_pull.py --product GNR --days-ago 5
```

Build report from that pull:
```powershell
applications.analytics.dda-tool/.venv/Scripts/python.exe TOOLS/Yield_Retest_Report_Create.py --csv TOOLS/output_dir/vpo_bin_attrs_GNR_5d_<timestamp>.csv
```