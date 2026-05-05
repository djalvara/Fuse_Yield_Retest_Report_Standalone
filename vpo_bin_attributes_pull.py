"""
vpo_bin_attributes_pull.py
--------------------------
Pull bin and session-attribute data for a given product at product-specific operations.

Output columns (fixed):
    OPERGROUP | LOT | VISUAL_ID | FACILITY | PROGRAM_OR_BI_RECIPE_NAME | Testing Entity | UNIT_TESTER_SITE_ID |
    Within_LOTS_Latest_Flag | Within_LOTS_Seq_Num | S_SPEC | LOTS Start Date Time |
    Workweek | LOTS End Date Time | DevRevStep |
    FUNCTIONAL_BIN | INTERFACE_BIN | DATA_BIN | Failing_Instance | FRV_SPEC

`FRV_SPEC` is a session attribute name stored in
`mds_lots_attribute_value.lots_attribute_name`; its
`lots_attribute_string_value` is returned via the MAPI
`session_attributes` mechanism.

CRITICAL: Only ONE MIDAS query is executed per run
============================================================
This script creates a SINGLE MIDAS query with internally-composed INSERT
statements. The lock mechanism prevents concurrent instances. Do NOT:
  - Run multiple script instances simultaneously
  - Submit multiple commands in quick succession
  - Manually create/submit additional MIDAS queries while this runs

AUTOMATION DIRECTIVE: One running query only
============================================================
If a lock file exists, treat the active query as authoritative and wait.
Do NOT implement auto-restart routines that:
    - Poll and then re-launch the same command repeatedly
    - Kill running python processes to "unstick" execution
    - Remove lock files while a live process still exists

Only restart manually after confirming the previous process has exited.

If you see "Another vpo_bin_attributes_pull execution is already running",
wait for the current execution to complete or check/remove the lock file at:
  TOOLS/output_dir/.vpo_bin_attributes_pull.lock

Usage
-----
    python vpo_bin_attributes_pull.py --product GNR --days-ago 13
    python vpo_bin_attributes_pull.py --product DMR --month 2026-04
    python vpo_bin_attributes_pull.py --product DMR --days-ago 30 --output-dir C:\\Temp\\out
    python vpo_bin_attributes_pull.py --product DMR --month 2026-04 --keep-raw
    python vpo_bin_attributes_pull.py --product GNR --days-ago 15 --create-report
    python vpo_bin_attributes_pull.py --product GNR --days-ago 15 --latest-only
    python vpo_bin_attributes_pull.py --product GNR --days-ago 18 --lot J615177CR --visual-id 440A226X00344

Arguments
---------
    --product     Program/recipe name prefix (e.g. GNR, DMR).  A trailing '%'
                  wildcard is added automatically.
    --days-ago    Pull records whose LOTS_START_DATE_TIME >= NOW - N days.
    --month       Pull records for a specific calendar month (format: YYYY-MM).
    --output-dir  Directory to write output files (default: TOOLS/output_dir).
    --keep-raw    Preserve raw MIDAS CSV after curated CSV is created.
    --create-report
                  Generate interactive HTML report from curated CSV.
    --lot         Optional exact lot filter (e.g. J615177CR).
    --visual-id   Optional exact visual_id filter (e.g. 440A226X00344).
    --include-non-latest
                  Include both latest and non-latest rows (default behavior).
    --latest-only Pull latest rows only.

Notes
-----
- Operations are selected by product (default map in script).
- GNR defaults to operations 6197 and 6262.
- Substructure is limited to U1.
- By default, both latest and non-latest rows are pulled.
- Use --latest-only to pull latest rows only.
- A post-pull strict date filter is applied after download to guard against
  historical records pulled in through downstream joins.
- Failing instance names are mapped from product BinReport collateral
        (TOOLS/tools_collaterals/<PRODUCT>/**/BinReport.xml).
- The raw MAPI CSV is preserved alongside the final output.
"""

from __future__ import annotations

import atexit
import argparse
import logging
import multiprocessing as mp
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    import pandas as pd

# ---------------------------------------------------------------------------
# Path bootstrap – make both src trees importable without installing packages
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DDA_SRC = _REPO_ROOT / "applications.analytics.dda-tool" / "src"
if str(_DDA_SRC) not in sys.path:
    sys.path.insert(0, str(_DDA_SRC))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_OPERATIONS = ["6197"]
PRODUCT_OPERATIONS = {
    "CWF": ["6197"],
    "DMR": ["6197"],
    "GNR": ["6197", "6262"],
}
MIDAS_URL = "https://chm1px.intel.com/MidasHbaseWebApi"
MIDAS_ENDPOINT = "/api/v1/reports/commonreports/genericreport_async"
CERT_PATH = _REPO_ROOT / "IntelChain.pem"

SESSION_ATTR_FRV_SPEC = "FRV_SPEC"

FINAL_COLUMNS = [
    "OPERGROUP",
    "LOT",
    "VISUAL_ID",
    "FACILITY",
    "PROGRAM_OR_BI_RECIPE_NAME",
    "Testing Entity",
    "UNIT_TESTER_SITE_ID",
    "Within_LOTS_Latest_Flag",
    "Retest_Recovery",
    "Within_LOTS_Seq_Num",
    "S_SPEC",
    "LOTS Start Date Time",
    "Workweek",
    "LOTS End Date Time",
    "DevRevStep",
    "FUNCTIONAL_BIN",
    "INTERFACE_BIN",
    "DATA_BIN",
    "Failing_Instance",
    "FRV_SPEC",
]

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output_dir"
VENV_PYTHON = _REPO_ROOT / "applications.analytics.dda-tool" / ".venv" / "Scripts" / "python.exe"
TOOLS_COLLATERALS_DIR = Path(__file__).resolve().parent / "tools_collaterals"
BIN_REPORT_FILENAME = "BinReport.xml"
DEFAULT_RELEASE_ROOT = r"\\amr\ec\proj\mdl\cr\intel\hdmxprogs"

DESCRIBE_TEXT = """
What this script does
---------------------
- Pulls MIDAS unit-test/bin data for product-specific operation lists.
- Filters by product prefix (e.g., GNR%) and rolling window (days ago).
- Pulls both latest and non-latest rows by default.
- Optional switch pulls latest rows only.
- Pivots FRV_SPEC from session attributes and merges native tester/program/spec/date fields.
- Maps DATA_BIN values to failing-instance names from product BinReport.xml collateral.
- Writes two files: raw MIDAS CSV and final curated CSV.

Required inputs
---------------
- --product    Product/program prefix. Example: GNR
- --days-ago   Rolling day window. Example: 14
    or
- --month      Specific calendar month. Example: 2026-04

Optional inputs
---------------
- --output-dir Output folder for raw/final files.
- --keep-raw Keep the intermediate raw MIDAS CSV (default is to delete it after curated CSV write).
- --lot Optional exact lot filter.
- --visual-id Optional exact visual_id filter.
- --release-root Optional TP release network root.
- --include-non-latest Include both latest and non-latest rows (default).
- --latest-only Pull latest rows only.

Example
-------
python vpo_bin_attributes_pull.py --product GNR --days-ago 14
python vpo_bin_attributes_pull.py --product DMR --month 2026-04
python vpo_bin_attributes_pull.py --product DMR --month 2026-04 --keep-raw
""".strip()


def _log_step(message: str) -> None:
    """Print a timestamped progress message for long-running script stages."""
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


def _get_operations_for_product(product_prefix: str) -> list[str]:
    """Return the default operation list for a product prefix."""
    return PRODUCT_OPERATIONS.get(product_prefix.upper(), DEFAULT_OPERATIONS)


def _acquire_single_instance_lock(lock_dir: Path) -> Path:
    """Prevent concurrent runs by creating an exclusive lock file."""
    lock_file = lock_dir / ".vpo_bin_attributes_pull.lock"

    def _parse_lock_pid(lock_text: str) -> int | None:
        match = re.search(r"\bpid=(\d+)\b", lock_text or "")
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _pid_is_running(pid: int | None) -> bool:
        if pid is None:
            return False
        if pid == os.getpid():
            return True
        try:
            # Cross-platform process existence check.
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Exists but inaccessible.
            return True
        except OSError:
            return False
        return True

    # One retry path is allowed: if lock exists but its PID is no longer alive,
    # clean stale lock and attempt lock acquisition again.
    for _ in range(2):
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError as exc:
            existing = ""
            try:
                existing = lock_file.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                pass

            existing_pid = _parse_lock_pid(existing)
            if existing and not _pid_is_running(existing_pid):
                print(
                    f"[warn] Removing stale lock file with inactive pid={existing_pid}: {lock_file}"
                )
                try:
                    lock_file.unlink(missing_ok=True)
                except OSError:
                    pass
                continue

            message = (
                "\n"
                "ERROR: Another vpo_bin_attributes_pull execution is already running.\n"
                "This script enforces single-instance execution to prevent multiple MIDAS queries.\n"
                "\n"
                "WAIT: Allow the current run to complete before starting a new one.\n"
                "Do NOT auto-restart, kill active python processes, or clear lock files while the process is alive.\n"
                "\n"
                f"Lock file: {lock_file}\n"
            )
            if existing:
                message = f"{message}Lock details: {existing}"
            raise SystemExit(message) from exc
    else:
        raise SystemExit(f"Unable to acquire lock after stale-lock cleanup attempt: {lock_file}")

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(
            f"pid={os.getpid()} started_utc={datetime.now(timezone.utc).isoformat()} "
            f"argv={' '.join(sys.argv)}\n"
        )

    def _release_lock() -> None:
        try:
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass

    atexit.register(_release_lock)
    return lock_file


def _find_product_bin_report_for_release(product_prefix: str, tp_release: str) -> Path | None:
    """Return a cached BinReport.xml for a product TP release, if available."""
    product_dir = TOOLS_COLLATERALS_DIR / product_prefix.upper()
    if not product_dir.exists():
        print(f"[warn] Product collateral directory not found: {product_dir}")
        return None

    release_dir = product_dir / tp_release
    if not release_dir.exists():
        print(f"  [warn] TP release folder not found in local collateral cache: {release_dir}")
        return None

    canonical_report = release_dir / "POR_TP" / "CLASS_TP" / "Reports" / BIN_REPORT_FILENAME
    if canonical_report.is_file():
        print(f"  Using TP release BinReport collateral: {tp_release}")
        return canonical_report

    candidates = sorted(p for p in release_dir.rglob(BIN_REPORT_FILENAME) if p.is_file())
    if not candidates:
        print(f"  [warn] TP release folder found but no {BIN_REPORT_FILENAME} under: {release_dir}")
        return None

    print(f"  [warn] Using non-canonical BinReport path for {tp_release}: {candidates[0]}")
    return candidates[0]


def _find_product_fallback_bin_report(product_prefix: str) -> Path | None:
    """Find fallback BinReport.xml under the product collateral tree."""
    product_dir = TOOLS_COLLATERALS_DIR / product_prefix.upper()
    if not product_dir.exists():
        print(f"[warn] Product collateral directory not found: {product_dir}")
        return None

    preferred_paths = [
        product_dir / BIN_REPORT_FILENAME,
        product_dir / "BinReport_Fallback" / BIN_REPORT_FILENAME,
        product_dir / "fallback_bdefs_file" / BIN_REPORT_FILENAME,
        product_dir / "fallback_sbdefs_bdefs_file" / BIN_REPORT_FILENAME,
    ]
    for candidate in preferred_paths:
        if candidate.is_file():
            return candidate

    candidates = sorted(p for p in product_dir.rglob(BIN_REPORT_FILENAME) if p.is_file())
    if not candidates:
        return None

    fallback_candidates = [p for p in candidates if "FALLBACK" in str(p).upper()]
    return fallback_candidates[0] if fallback_candidates else candidates[0]


def _extract_program_suffix(program_name: str) -> str:
    """Return last 8 characters of program name as TP release key token."""
    text = str(program_name).strip()
    if len(text) < 8:
        return ""
    return text[-8:].upper()


def _resolve_release_for_program(program_name: str, release_dirs: list[Path]) -> Path | None:
    """Resolve TP release dir by matching last-6 token of PROGRAM to release folder name."""
    token = _extract_program_suffix(program_name)
    if not token:
        return None

    return _resolve_release_for_token(token, release_dirs)


def _resolve_release_for_token(token: str, release_dirs: list[Path]) -> Path | None:
    """Resolve TP release dir by matching a token in release folder names."""
    token = str(token).strip().upper()
    if not token:
        return None

    matches = [d for d in release_dirs if token in d.name.upper()]
    if not matches:
        return None

    if len(matches) > 1:
        print(
            f"  [warn] Multiple TP release matches for token '{token}'; "
            f"using {sorted(matches, key=lambda p: p.name)[-1].name}"
        )
    return sorted(matches, key=lambda p: p.name)[-1]


def _sync_program_release_bin_reports(
    product_prefix: str,
    program_names: list[str],
    release_root: Path,
) -> tuple[dict[str, Path], dict[str, str], dict[str, str]]:
    """Sync BinReport.xml per TP release and return per-program report paths and source labels."""
    local_product_dir = TOOLS_COLLATERALS_DIR / product_prefix.upper()
    local_product_dir.mkdir(parents=True, exist_ok=True)
    local_release_dirs = sorted([p for p in local_product_dir.iterdir() if p.is_dir()])

    product_release_root = release_root / product_prefix.upper()
    remote_release_dirs: list[Path] = []
    if product_release_root.exists():
        remote_release_dirs = sorted([p for p in product_release_root.iterdir() if p.is_dir()])
    else:
        print(f"[warn] TP release root not found: {product_release_root} (local cache-only mode)")

    release_file_cache: dict[str, Path] = {}
    release_source_cache: dict[str, str] = {}
    program_to_report: dict[str, Path] = {}
    program_to_release: dict[str, str] = {}
    program_to_source: dict[str, str] = {}

    fallback_report = _find_product_fallback_bin_report(product_prefix)

    print(f"  Local collateral cache root : {local_product_dir}")
    print(f"  Remote TP release root      : {product_release_root}")
    if fallback_report is not None:
        print(f"  Fallback BinReport file     : {fallback_report}")

    for program_name in program_names:
        token = _extract_program_suffix(program_name)
        if not token:
            print(f"  [warn] Cannot extract TP release token from program '{program_name}'")
            continue

        # Cache-first: if local TP release folder exists with BinReport, use it and skip copying.
        local_release_dir = _resolve_release_for_token(token, local_release_dirs)
        if local_release_dir is not None:
            local_report = _find_product_bin_report_for_release(product_prefix, local_release_dir.name)
            if local_report is not None:
                release_name = local_release_dir.name
                program_to_release[program_name] = release_name
                release_file_cache[release_name] = local_report
                release_source_cache[release_name] = "cached-tp"
                program_to_report[program_name] = local_report
                program_to_source[program_name] = "cached-tp"
                print(f"  Using cached TP release BinReport: {release_name}")
                print(f"    cache file: {local_report}")
                continue

        release_dir = _resolve_release_for_token(token, remote_release_dirs)
        if release_dir is None:
            if fallback_report is not None:
                print(
                    f"  [warn] No TP release match for program '{program_name}'"
                    + (f" (token '{token}')" if token else "")
                    + "; using fallback BinReport.xml"
                )
                print(f"    fallback file: {fallback_report}")
                program_to_report[program_name] = fallback_report
                program_to_source[program_name] = "fallback"
            else:
                print(
                    f"  [warn] No TP release match for program '{program_name}'"
                    + (f" (token '{token}')" if token else "")
                )
            continue

        release_name = release_dir.name
        program_to_release[program_name] = release_name

        if release_name in release_file_cache:
            program_to_report[program_name] = release_file_cache[release_name]
            program_to_source[program_name] = release_source_cache.get(release_name, "cached-tp")
            continue

        cached_report = _find_product_bin_report_for_release(product_prefix, release_name)
        if cached_report is not None:
            release_file_cache[release_name] = cached_report
            release_source_cache[release_name] = "cached-tp"
            program_to_report[program_name] = cached_report
            program_to_source[program_name] = "cached-tp"
            print(f"  Using cached TP release BinReport: {release_name}")
            print(f"    cache file: {cached_report}")
            continue

        release_target = local_product_dir / release_name
        report_relative = Path("POR_TP") / "CLASS_TP" / "Reports" / BIN_REPORT_FILENAME
        remote_report = release_dir / report_relative
        if not remote_report.is_file():
            print(f"  [warn] TP release BinReport not found: {remote_report}")
            if fallback_report is not None:
                print(f"  [warn] Using fallback BinReport for program '{program_name}': {fallback_report}")
                program_to_report[program_name] = fallback_report
                program_to_source[program_name] = "fallback"
            continue

        target_report = release_target / report_relative
        target_report.parent.mkdir(parents=True, exist_ok=True)
        print(f"  Syncing TP release BinReport: {release_name}")
        print(f"    source release dir: {release_dir}")
        print(f"    source report file: {remote_report}")
        print(f"    cache target file : {target_report}")
        shutil.copy2(remote_report, target_report)

        release_file_cache[release_name] = target_report
        release_source_cache[release_name] = "synced-tp"
        program_to_report[program_name] = target_report
        program_to_source[program_name] = "synced-tp"
        print(f"  Synced TP release {release_name}: 1 file")

    return program_to_report, program_to_release, program_to_source


def _load_failing_instance_map_from_bin_report(bin_report_file: Path) -> dict[str, str]:
    """Parse BinReport.xml and map DATA_BIN (Element@name) -> failing instance (Testname@name)."""
    bin_map: dict[str, str] = {}
    try:
        root = ET.parse(bin_report_file).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"  [warn] Failed to parse BinReport file {bin_report_file}: {exc}")
        return bin_map

    for element in root.findall(".//Element"):
        data_bin = str(element.attrib.get("name", "")).strip()
        if not data_bin or not data_bin.isdigit():
            continue

        test_nodes = element.findall("./Testname")
        if not test_nodes:
            continue

        fail_nodes = [n for n in test_nodes if str(n.attrib.get("type", "")).strip().lower() == "fail"]
        selected_node = fail_nodes[0] if fail_nodes else test_nodes[0]
        instance_name = str(selected_node.attrib.get("name", "")).strip()
        if instance_name:
            bin_map[data_bin] = instance_name

    print(f"    {bin_report_file.name}: {len(bin_map):,} mappings")
    return bin_map


def _resolve_failing_instance(data_bin_value: str, failing_map: dict[str, str]) -> str:
    """Return mapped instance, empty for 10010000, else NOT_FOUND when missing."""
    data_bin = str(data_bin_value).strip()
    if not data_bin or data_bin.lower() == "nan":
        return ""
    if data_bin == "10010000":
        return ""
    return failing_map.get(data_bin, "NOT_FOUND")


def _resolve_failing_instance_for_program(
    data_bin_value: str,
    program_name: str,
    program_maps: dict[str, dict[str, str]],
) -> str:
    """Resolve failing instance from program-specific DATA_BIN mapping."""
    data_bin = str(data_bin_value).strip()
    if not data_bin or data_bin.lower() == "nan":
        return ""
    if data_bin == "10010000":
        return ""

    mapping = program_maps.get(str(program_name).strip(), {})
    return mapping.get(data_bin, "NOT_FOUND")


def _cleanup_runtime_resources() -> None:
    """Release background resources that can keep the process attached to the terminal."""
    # Flush/close logging handlers created by downstream libraries.
    logging.shutdown()

    # Some library paths may leave child processes alive after query completion.
    # Terminate any active children so Python can exit cleanly back to shell prompt.
    for child in mp.active_children():
        try:
            child.terminate()
            child.join(timeout=1)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Pull LOT / FACILITY / bin / session-attribute data at product-specific operations "
            "for a given product and rolling time window."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--describe",
        action="store_true",
        help="Print a short description of what the script does and its inputs, then exit.",
    )
    parser.add_argument(
        "--product",
        required=False,
        help="Program name prefix, e.g. GNR or DMR.  A trailing %% wildcard is added automatically.",
    )
    parser.add_argument(
        "--days-ago",
        dest="days_ago",
        type=int,
        required=False,
        help="Number of days back from today to include (e.g. 13 = April month-to-date).",
    )
    parser.add_argument(
        "--month",
        dest="month",
        type=str,
        required=False,
        help="Specific calendar month in YYYY-MM format (e.g. 2026-04).",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory for output files (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--keep-raw",
        dest="keep_raw",
        action="store_true",
        help="Preserve raw MIDAS CSV after final curated CSV is written.",
    )
    parser.add_argument(
        "--create-report",
        dest="create_report",
        action="store_true",
        help="Generate HTML interactive report from curated CSV after pull completes.",
    )
    parser.add_argument(
        "--lot",
        dest="lot",
        required=False,
        help="Optional exact lot filter, e.g. J615177CR.",
    )
    parser.add_argument(
        "--visual-id",
        dest="visual_id",
        required=False,
        help="Optional exact visual_id filter, e.g. 440A226X00344.",
    )
    parser.add_argument(
        "--release-root",
        dest="release_root",
        required=False,
        default=DEFAULT_RELEASE_ROOT,
        help=(
            "Optional TP release network root. "
            f"Default: {DEFAULT_RELEASE_ROOT}"
        ),
    )
    parser.add_argument(
        "--include-non-latest",
        action="store_true",
        default=True,
        help=(
            "Include both latest and non-latest rows. "
            "By default, this mode is enabled. Useful for restest analysis."
        ),
    )
    parser.add_argument(
        "--latest-only",
        dest="include_non_latest",
        action="store_false",
        help="Pull latest rows only.",
    )
    args = parser.parse_args()

    if args.describe:
        print(DESCRIBE_TEXT)
        raise SystemExit(0)

    # Keep explicit input validation here so --describe can run standalone.
    if not args.product:
        parser.error("--product is required unless --describe is used.")
    if args.days_ago is not None and args.month:
        parser.error("Use either --days-ago or --month, not both.")
    if args.days_ago is None and not args.month:
        parser.error("One of --days-ago or --month is required unless --describe is used.")

    if args.month:
        try:
            month_start = datetime.strptime(args.month, "%Y-%m").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            parser.error("--month must be in YYYY-MM format (example: 2026-04).")
            raise AssertionError("unreachable") from exc

        now_utc = datetime.now(tz=timezone.utc)
        if month_start > now_utc:
            parser.error("--month cannot be in the future.")

    return args


def _ensure_pandas_imported() -> None:
    """Import pandas only for execution paths that need data processing."""
    try:
        import pandas as _pd
    except ModuleNotFoundError as exc:
        # If called with a non-venv interpreter, relaunch once using the repo venv.
        if os.environ.get("QBOT_VENV_RELAUNCH") != "1" and VENV_PYTHON.exists() and Path(sys.executable) != VENV_PYTHON:
            os.environ["QBOT_VENV_RELAUNCH"] = "1"
            os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

        raise SystemExit(
            "Missing dependency: pandas and automatic venv relaunch was not possible. Install pandas or run with the project venv interpreter."
        ) from exc

    globals()["pd"] = _pd


# ---------------------------------------------------------------------------
# Post-pull helpers
# ---------------------------------------------------------------------------

def _pivot_session_attrs(
    df: pd.DataFrame,
    attr_names: list[str],
    preserve_row_multiplicity: bool = False,
) -> pd.DataFrame:
    """
    MAPI RAW output with session_attributes returns one row per
    (VISUAL_ID, attribute) combination, with bin data repeated on every row.
    ``PARAMETER_GROUP`` == 'Session Attribute' identifies attribute rows;
    ``TEST_NAME`` holds the attribute name; ``TEST_RESULT`` holds the value.

    This function pivots attribute rows into columns and joins them back to
    bin data rows. By default it de-duplicates by VISUAL_ID; when
    preserve_row_multiplicity=True it keeps row-level multiplicity from the
    source data (used for --include-non-latest mode). If a VISUAL_ID has no
    row for a particular attribute, that column is filled with an empty string.
    """
    # Separate attribute rows from plain unit rows (some formats mix them)
    if "PARAMETER_GROUP" in df.columns:
        attr_mask = df["PARAMETER_GROUP"].notna() & (df["PARAMETER_GROUP"].str.strip() == "Session Attribute")
    elif "TEST_NAME" in df.columns:
        attr_mask = df["TEST_NAME"].notna()
    else:
        attr_mask = pd.Series([False] * len(df), index=df.index)

    attr_df = df.loc[attr_mask].copy()
    unit_df = df.loc[~attr_mask].copy()

    # Bin data columns that are present in the attribute rows too
    bin_cols = [
        c
        for c in [
            "OPERATION",
            "VISUAL_ID",
            "LOT",
            "FACILITY",
            "DATA_BIN",
            "FUNCTIONAL_BIN",
            "INTERFACE_BIN",
            "WITHIN_LOTS_LATEST_FLAG",
            "WITHIN_LOTS_SEQ_NUM",
        ]
        if c in df.columns
    ]

    # If all rows are attribute rows (common for RAW+session_attributes only queries)
    # use attribute rows as bin context source, but do not duplicate rows per attribute.
    if len(unit_df) == 0:
        if preserve_row_multiplicity:
            unit_df = attr_df[bin_cols].drop_duplicates()
        else:
            unit_df = attr_df[bin_cols].drop_duplicates(subset=["VISUAL_ID"] if "VISUAL_ID" in bin_cols else None)

    # Build composite join keys so session attributes are associated with the
    # same lot/session context, not VISUAL_ID alone.
    key_candidates = [
        "VISUAL_ID",
        "LOT",
        "LATO_START_WW",
        "LOTS_SEQ_KEY",
        "WITHIN_LOTS_LATEST_FLAG",
        "WITHIN_LOTS_SEQ_NUM",
    ]
    join_keys = [k for k in key_candidates if k in unit_df.columns and k in attr_df.columns]
    if not join_keys and "VISUAL_ID" in unit_df.columns:
        join_keys = ["VISUAL_ID"]

    # FRV_SPEC should resolve to a single value per VISUAL_ID+LOT.
    frv_key_candidates = ["VISUAL_ID", "LOT"]
    frv_join_keys = [k for k in frv_key_candidates if k in unit_df.columns and k in attr_df.columns]
    if not frv_join_keys and "VISUAL_ID" in unit_df.columns and "VISUAL_ID" in attr_df.columns:
        frv_join_keys = ["VISUAL_ID"]

    # Base rows preserve bin context in non-latest mode, otherwise collapse by key.
    merged = unit_df[bin_cols].copy() if bin_cols else unit_df.copy()
    if join_keys and not preserve_row_multiplicity:
        merged = merged.drop_duplicates(subset=join_keys)

    def _frv_rank(value: str) -> tuple[int, int, int, str]:
        text = str(value).strip()
        match = re.search(r"_Y(\d{2})W(\d{2})", text)
        if not match:
            return (0, -1, -1, text)
        return (1, int(match.group(1)), int(match.group(2)), text)

    # Merge one resolved value per key for each requested attribute.
    for attr in attr_names:
        if "TEST_NAME" not in attr_df.columns or "TEST_RESULT" not in attr_df.columns:
            merged[attr] = ""
            continue

        rows_for_attr = attr_df.loc[attr_df["TEST_NAME"].astype(str).str.strip() == attr].copy()
        if rows_for_attr.empty:
            merged[attr] = ""
            continue

        attr_join_keys = frv_join_keys if attr == SESSION_ATTR_FRV_SPEC and frv_join_keys else join_keys

        if attr_join_keys:
            rows_for_attr = rows_for_attr[attr_join_keys + ["TEST_RESULT"]].copy()
            if attr == SESSION_ATTR_FRV_SPEC:
                rows_for_attr["_rank"] = rows_for_attr["TEST_RESULT"].map(_frv_rank)
                rows_for_attr = (
                    rows_for_attr.sort_values("_rank")
                    .drop_duplicates(subset=attr_join_keys, keep="last")
                    .drop(columns=["_rank"])
                )
            else:
                rows_for_attr = rows_for_attr.drop_duplicates(subset=attr_join_keys, keep="last")

            rows_for_attr = rows_for_attr.rename(columns={"TEST_RESULT": attr})
            merged = merged.merge(rows_for_attr, on=attr_join_keys, how="left")
        else:
            # Fallback when no reliable keys are available.
            merged[attr] = rows_for_attr["TEST_RESULT"].iloc[-1]

    # Fill missing attribute values with empty string
    for attr in attr_names:
        if attr in merged.columns:
            merged[attr] = merged[attr].fillna("")
        else:
            merged[attr] = ""

    return merged


def _apply_strict_date_filter(df: pd.DataFrame, date_col: str, days_ago: int) -> pd.DataFrame:
    """Remove any rows older than *days_ago* days (guards against join expansion)."""
    if date_col not in df.columns:
        print(f"[warn] Date column '{date_col}' not found – skipping strict date filter.")
        return df
    dates = pd.to_datetime(df[date_col], errors="coerce", utc=True)
    cutoff = datetime.now(tz=timezone.utc) - pd.Timedelta(days=days_ago)
    before = len(df)
    df = df.loc[dates >= cutoff].copy()
    removed = before - len(df)
    if removed:
        print(f"  Strict date filter removed {removed:,} rows older than {days_ago} days.")
    return df


def _apply_strict_month_filter(df: pd.DataFrame, date_col: str, month_yyyy_mm: str) -> pd.DataFrame:
    """Keep only rows whose timestamp in *date_col* falls within month_yyyy_mm (UTC)."""
    if date_col not in df.columns:
        print(f"[warn] Date column '{date_col}' not found - skipping strict month filter.")
        return df

    try:
        month_start = datetime.strptime(month_yyyy_mm, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"[warn] Invalid month '{month_yyyy_mm}' - skipping strict month filter.")
        return df

    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)

    dates = pd.to_datetime(df[date_col], errors="coerce", utc=True)
    before = len(df)
    df = df.loc[(dates >= month_start) & (dates < next_month)].copy()
    removed = before - len(df)
    if removed:
        print(f"  Strict month filter removed {removed:,} rows outside {month_yyyy_mm}.")
    return df


def _generate_html_report(csv_path: Path) -> None:
    """Invoke Yield_Retest_Report_Create.py to generate interactive HTML report."""
    
    script_path = Path(__file__).parent / "Yield_Retest_Report_Create.py"
    
    if not script_path.exists():
        print(f"  [warn] Report script not found: {script_path}")
        return
    
    # Build output path in same directory as CSV
    output_path = csv_path.parent / f"vpo_bin_attrs_interactive_report_{csv_path.stem}.html"
    
    cmd = [
        sys.executable,
        str(script_path),
        "--csv",
        str(csv_path),
        "--output",
        str(output_path),
    ]
    
    try:
        print(f"  Invoking report script: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False, text=True, check=True)
        print(f"  Report generation completed successfully.")
    except subprocess.CalledProcessError as exc:
        print(f"  [error] Report generation failed with exit code {exc.returncode}: {exc}")
    except Exception as exc:
        print(f"  [error] Failed to generate report: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()
    product_prefix = args.product.rstrip("%")

    _ensure_pandas_imported()
    from pysvtools.mapi.query import OutputFormat, generate_unique_ticket_name, query_midas  # noqa: E402
    from pysvtools.mapi.user import get_user  # noqa: E402

    program_filter = f"{product_prefix}%"
    operations = _get_operations_for_product(product_prefix)
    lot_filter = args.lot.strip() if args.lot else "%"
    visual_id_filter = args.visual_id.strip() if args.visual_id else ""
    query_days_ago: int
    period_label: str
    period_tag: str
    if args.month:
        month_start = datetime.strptime(args.month, "%Y-%m").replace(tzinfo=timezone.utc)
        now_utc = datetime.now(tz=timezone.utc)
        # Query a broad enough rolling window, then enforce exact month in post-filter.
        query_days_ago = max(1, (now_utc - month_start).days + 1)
        period_label = f"month {args.month}"
        period_tag = args.month
    else:
        query_days_ago = int(args.days_ago)
        period_label = f"last {query_days_ago} day(s)"
        period_tag = f"{query_days_ago}d"

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Lock ensures only ONE instance of this script runs at a time.
    # This prevents multiple MIDAS queries from being submitted concurrently.
    _acquire_single_instance_lock(output_dir)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_filename = f"vpo_bin_attrs_{product_prefix}_{period_tag}_raw_{timestamp}.csv"
    final_filename = f"vpo_bin_attrs_{product_prefix}_{period_tag}_{timestamp}.csv"
    raw_csv = output_dir / raw_filename
    final_csv = output_dir / final_filename

    ticket = generate_unique_ticket_name()
    user = get_user()

    print("[info] Execution started. This pull may take a while depending on MIDAS queue and data volume.")
    print(f"[vpo_bin_attributes_pull] Starting pull")
    print(f"  Product filter : {program_filter}")
    print(f"  Lot filter     : {lot_filter}")
    if visual_id_filter:
        print(f"  Visual filter  : {visual_id_filter}")
    print(f"  Operations     : {', '.join(operations)}")
    print(f"  Time window    : {period_label}")
    print(f"  Latest-only    : {not args.include_non_latest}")
    print(f"  Ticket         : {ticket}")
    print(f"  Output dir     : {output_dir}")
    _log_step("Stage 1/4 - Preparing MIDAS query payload")

    # Set MIDAS certificate if present
    import os
    if CERT_PATH.exists() and not os.environ.get("MIDAS_CERTIFICATE"):
        os.environ["MIDAS_CERTIFICATE"] = str(CERT_PATH)

    # ====================================================================
    # EXECUTION FLOW: ONE MIDAS Query + Post-processing
    # ====================================================================
    # Stage 1: Submit a SINGLE query to MIDAS with all filters, operations,
    #          and session attribute requests. The query internally uses
    #          multiple INSERT statements but is ONE atomic submission.
    #
    # Stage 2: Post-process the raw CSV from MIDAS locally (pandas):
    #          - Apply strict date filtering
    #          - Restrict to configured operations only
    #
    # Stage 3: Pivot session attributes and map/enrich columns
    #
    # Stage 4: Write final curated CSV
    # ====================================================================

    # 1) Pull raw MIDAS data with both native columns and session attributes.
    _log_step("Stage 1/4 - Submitting query to MIDAS and waiting for results")
    query_kwargs = dict(
        caller_app="qbot-flow",
        user_id=user,
        output_directory=str(output_dir),
        output_file=raw_filename,
        ticket=ticket,
        columns=[
            "visual_id",
            "lot",
            "facility",
            "program_or_bi_recipe_name",
            "operation",
            "lato_start_ww",
            "lots_seq_key",
            "lots_start_date_time",
            "lots_end_date_time",
            "substructure_id",
            "interface_bin",
            "functional_bin",
            "data_bin",
            "testing_entity",
            "unit_tester_id",
            "unit_tester_site_id",
            "devrevstep",
            "within_lots_latest_flag",
            "within_lots_seq_num",
            "s_spec",
        ],
        column_case="upper",
        output_format=OutputFormat.RAW.value,
        midas_url=MIDAS_URL,
        midas_report_endpoint=MIDAS_ENDPOINT,
        lato_valid_flag="Y",
        lot=[lot_filter],
        substructure_comp=["U1"],
        last_n_days=query_days_ago,
        program_or_bi_recipe_name=[program_filter],
        pre_assembly=False,
        testing_type=["ULT", "CLASS", "BURNIN", "PPV", "SORT", "SDS", "SDT"],
        sort_filters={"operation": operations},
        class_filters={"operation": operations},
        session_attributes=[
            {
                "operation": operations,
                "attribute": [SESSION_ATTR_FRV_SPEC],
            }
        ],
    )

    if visual_id_filter:
        query_kwargs["visual_ids"] = [visual_id_filter]

    if not args.include_non_latest:
        query_kwargs["coalesce_vf_operation_sfs_latest_flag_vf_operation_final_latest_flag"] = "Y"

    # ====================================================================
    # Execute ONE single MIDAS query. The pysvtools library constructs
    # the complete query payload with internal INSERT statements, but this
    # is a single atomic query submission to MIDAS. Do NOT call query_midas
    # multiple times or spawn multiple script instances.
    # ====================================================================
    with patch("pysvtools.mapi.query.get_user", return_value=user):
        query_midas(
            **query_kwargs,
        )

    _log_step("Stage 1/4 - MIDAS pull completed")
    print(f"  Raw CSV : {raw_csv}")

    # ------------------------------------------------------------------
    # 2) Post-process raw output.
    #    - Default mode: preserve row multiplicity.
    #    - --latest-only mode: collapse to one row per VISUAL_ID.
    # ------------------------------------------------------------------
    _log_step("Stage 2/4 - Loading raw CSV into pandas")
    df = pd.read_csv(raw_csv, dtype=str, low_memory=False)
    print(f"  Rows from MAPI : {len(df):,}")

    # Normalise column names to avoid whitespace / case surprises ONLY for
    # the lookup step; we'll rename back to user-facing labels.
    df.columns = [c.strip() for c in df.columns]

    _log_step("Stage 2/4 - Applying strict date and operation filters")
    # 1. Strict date filter – operation column check
    if args.month:
        df = _apply_strict_month_filter(df, "LOTS_START_DATE_TIME", args.month)
    else:
        df = _apply_strict_date_filter(df, "LOTS_START_DATE_TIME", query_days_ago)

    # Restrict to configured operations only (belt-and-suspenders)
    if "OPERATION" in df.columns:
        allowed_ops = {op.strip() for op in operations}
        before = len(df)
        df = df.loc[df["OPERATION"].astype(str).str.strip().isin(allowed_ops)].copy()
        removed = before - len(df)
        if removed:
            print(
                f"  Operation filter removed {removed:,} rows not in "
                f"{', '.join(sorted(allowed_ops))}."
            )

    print(f"  Rows after filters : {len(df):,}")

    _log_step("Stage 3/4 - Pivoting session attributes and mapping native columns")
    # 2. Pivot FRV_SPEC session attribute; tester/within-lots columns are native.
    print(f"  Pivoting session attributes: [{SESSION_ATTR_FRV_SPEC}]")
    out_df = _pivot_session_attrs(
        df,
        [SESSION_ATTR_FRV_SPEC],
        preserve_row_multiplicity=args.include_non_latest,
    )

    frv_has_data = SESSION_ATTR_FRV_SPEC in out_df.columns and out_df[SESSION_ATTR_FRV_SPEC].astype(str).str.strip().ne("").any()
    print(f"  FRV_SPEC populated : {frv_has_data}")

    # Testing Entity: use native TESTING_ENTITY when available; otherwise
    # fall back to UNIT_TESTER_ID to preserve prior behavior.
    if "TESTING_ENTITY" in df.columns:
        testing_entity_map = (
            df.dropna(subset=["TESTING_ENTITY"])[["VISUAL_ID", "TESTING_ENTITY"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["TESTING_ENTITY"]
        )
        out_df["Testing Entity"] = out_df["VISUAL_ID"].map(testing_entity_map).fillna("")
        populated = out_df["Testing Entity"].astype(str).str.strip().ne("").sum()
        print(f"  Testing Entity populated rows : {populated:,} / {len(out_df):,}")
    elif "UNIT_TESTER_ID" in df.columns:
        tester_map = (
            df.dropna(subset=["UNIT_TESTER_ID"])[["VISUAL_ID", "UNIT_TESTER_ID"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["UNIT_TESTER_ID"]
        )
        out_df["Testing Entity"] = out_df["VISUAL_ID"].map(tester_map).fillna("")
        populated = out_df["Testing Entity"].astype(str).str.strip().ne("").sum()
        print(f"  Testing Entity populated rows (from UNIT_TESTER_ID): {populated:,} / {len(out_df):,}")
    else:
        out_df["Testing Entity"] = ""
        print("  [warn] TESTING_ENTITY and UNIT_TESTER_ID columns not found in raw CSV.")

    # UNIT_TESTER_SITE_ID is also a native output column from mut.
    if "UNIT_TESTER_SITE_ID" in df.columns:
        tester_site_map = (
            df.dropna(subset=["UNIT_TESTER_SITE_ID"])[["VISUAL_ID", "UNIT_TESTER_SITE_ID"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["UNIT_TESTER_SITE_ID"]
        )
        out_df["UNIT_TESTER_SITE_ID"] = out_df["VISUAL_ID"].map(tester_site_map).fillna("")
        populated = out_df["UNIT_TESTER_SITE_ID"].astype(str).str.strip().ne("").sum()
        print(f"  UNIT_TESTER_SITE_ID populated rows : {populated:,} / {len(out_df):,}")
    else:
        out_df["UNIT_TESTER_SITE_ID"] = ""
        print("  [warn] UNIT_TESTER_SITE_ID column not found in raw CSV.")

    # Within_LOTS_Latest_Flag is a native output column.
    # In --include-non-latest mode, keep per-row values from out_df directly.
    if args.include_non_latest and "WITHIN_LOTS_LATEST_FLAG" in out_df.columns:
        out_df["Within_LOTS_Latest_Flag"] = out_df["WITHIN_LOTS_LATEST_FLAG"].fillna("")
        populated = out_df["Within_LOTS_Latest_Flag"].astype(str).str.strip().ne("").sum()
        print(f"  Within_LOTS_Latest_Flag populated rows : {populated:,} / {len(out_df):,}")
    elif "WITHIN_LOTS_LATEST_FLAG" in df.columns:
        within_lots_latest_flag_map = (
            df.dropna(subset=["WITHIN_LOTS_LATEST_FLAG"])[["VISUAL_ID", "WITHIN_LOTS_LATEST_FLAG"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["WITHIN_LOTS_LATEST_FLAG"]
        )
        out_df["Within_LOTS_Latest_Flag"] = out_df["VISUAL_ID"].map(within_lots_latest_flag_map).fillna("")
        populated = out_df["Within_LOTS_Latest_Flag"].astype(str).str.strip().ne("").sum()
        print(f"  Within_LOTS_Latest_Flag populated rows : {populated:,} / {len(out_df):,}")
    else:
        out_df["Within_LOTS_Latest_Flag"] = ""
        print("  [warn] WITHIN_LOTS_LATEST_FLAG column not found in raw CSV.")

    # Within_LOTS_Seq_Num is a native output column.
    # In --include-non-latest mode, keep per-row values from out_df directly.
    if args.include_non_latest and "WITHIN_LOTS_SEQ_NUM" in out_df.columns:
        out_df["Within_LOTS_Seq_Num"] = out_df["WITHIN_LOTS_SEQ_NUM"].fillna("")
        populated = out_df["Within_LOTS_Seq_Num"].astype(str).str.strip().ne("").sum()
        print(f"  Within_LOTS_Seq_Num populated rows : {populated:,} / {len(out_df):,}")
    elif "WITHIN_LOTS_SEQ_NUM" in df.columns:
        within_lots_seq_num_map = (
            df.dropna(subset=["WITHIN_LOTS_SEQ_NUM"])[["VISUAL_ID", "WITHIN_LOTS_SEQ_NUM"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["WITHIN_LOTS_SEQ_NUM"]
        )
        out_df["Within_LOTS_Seq_Num"] = out_df["VISUAL_ID"].map(within_lots_seq_num_map).fillna("")
        populated = out_df["Within_LOTS_Seq_Num"].astype(str).str.strip().ne("").sum()
        print(f"  Within_LOTS_Seq_Num populated rows : {populated:,} / {len(out_df):,}")
    else:
        out_df["Within_LOTS_Seq_Num"] = ""
        print("  [warn] WITHIN_LOTS_SEQ_NUM column not found in raw CSV.")

    # PROGRAM_OR_BI_RECIPE_NAME is a native output column.
    if "PROGRAM_OR_BI_RECIPE_NAME" in df.columns:
        program_map = (
            df.dropna(subset=["PROGRAM_OR_BI_RECIPE_NAME"])[["VISUAL_ID", "PROGRAM_OR_BI_RECIPE_NAME"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["PROGRAM_OR_BI_RECIPE_NAME"]
        )
        out_df["PROGRAM_OR_BI_RECIPE_NAME"] = out_df["VISUAL_ID"].map(program_map).fillna("")
        populated = out_df["PROGRAM_OR_BI_RECIPE_NAME"].astype(str).str.strip().ne("").sum()
        print(f"  PROGRAM_OR_BI_RECIPE_NAME populated rows : {populated:,} / {len(out_df):,}")
    else:
        out_df["PROGRAM_OR_BI_RECIPE_NAME"] = ""
        print("  [warn] PROGRAM_OR_BI_RECIPE_NAME column not found in raw CSV.")

    # S_SPEC is a native output column.
    if "S_SPEC" in df.columns:
        s_spec_map = (
            df.dropna(subset=["S_SPEC"])[["VISUAL_ID", "S_SPEC"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["S_SPEC"]
        )
        out_df["S_SPEC"] = out_df["VISUAL_ID"].map(s_spec_map).fillna("")
        populated = out_df["S_SPEC"].astype(str).str.strip().ne("").sum()
        print(f"  S_SPEC populated rows : {populated:,} / {len(out_df):,}")
    else:
        out_df["S_SPEC"] = ""
        print("  [warn] S_SPEC column not found in raw CSV.")

    # LOTS Start Date Time is a native output column.
    if "LOTS_START_DATE_TIME" in df.columns:
        lots_start_map = (
            df.dropna(subset=["LOTS_START_DATE_TIME"])[["VISUAL_ID", "LOTS_START_DATE_TIME"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["LOTS_START_DATE_TIME"]
        )
        out_df["LOTS Start Date Time"] = out_df["VISUAL_ID"].map(lots_start_map).fillna("")
        populated = out_df["LOTS Start Date Time"].astype(str).str.strip().ne("").sum()
        print(f"  LOTS Start Date Time populated rows : {populated:,} / {len(out_df):,}")
    else:
        out_df["LOTS Start Date Time"] = ""
        print("  [warn] LOTS_START_DATE_TIME column not found in raw CSV.")

    # Workweek is derived from LOTS Start Date Time using ISO year/week.
    out_df["Workweek"] = ""
    lot_start_ts = pd.to_datetime(out_df["LOTS Start Date Time"], errors="coerce")
    valid_lot_start = lot_start_ts.notna()
    if valid_lot_start.any():
        iso = lot_start_ts.dt.isocalendar()
        out_df.loc[valid_lot_start, "Workweek"] = (
            iso.loc[valid_lot_start, "year"].astype(int).astype(str)
            + "-WW"
            + iso.loc[valid_lot_start, "week"].astype(int).astype(str).str.zfill(2)
        )
    populated = out_df["Workweek"].astype(str).str.strip().ne("").sum()
    print(f"  Workweek populated rows : {populated:,} / {len(out_df):,}")

    # LOTS End Date Time is a native output column.
    if "LOTS_END_DATE_TIME" in df.columns:
        lots_end_map = (
            df.dropna(subset=["LOTS_END_DATE_TIME"])[["VISUAL_ID", "LOTS_END_DATE_TIME"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["LOTS_END_DATE_TIME"]
        )
        out_df["LOTS End Date Time"] = out_df["VISUAL_ID"].map(lots_end_map).fillna("")
        populated = out_df["LOTS End Date Time"].astype(str).str.strip().ne("").sum()
        print(f"  LOTS End Date Time populated rows : {populated:,} / {len(out_df):,}")
    else:
        out_df["LOTS End Date Time"] = ""
        print("  [warn] LOTS_END_DATE_TIME column not found in raw CSV.")

    # DevRevStep is a native output column.
    if "DEVREVSTEP" in df.columns:
        devrevstep_map = (
            df.dropna(subset=["DEVREVSTEP"])[["VISUAL_ID", "DEVREVSTEP"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["DEVREVSTEP"]
        )
        out_df["DevRevStep"] = out_df["VISUAL_ID"].map(devrevstep_map).fillna("")
        populated = out_df["DevRevStep"].astype(str).str.strip().ne("").sum()
        print(f"  DevRevStep populated rows : {populated:,} / {len(out_df):,}")
    else:
        out_df["DevRevStep"] = ""
        print("  [warn] DEVREVSTEP column not found in raw CSV.")

    # Failing_Instance is mapped from DATA_BIN using program-specific TP release BinReport.xml files.
    program_maps: dict[str, dict[str, str]] = {}
    if "PROGRAM_OR_BI_RECIPE_NAME" in out_df.columns:
        unique_programs = sorted(
            {
                p
                for p in out_df["PROGRAM_OR_BI_RECIPE_NAME"].fillna("").astype(str).str.strip()
                if p
            }
        )
        if unique_programs:
            _log_step("Stage 3/4 - Syncing release collateral for programs in current dataset")
            program_to_reports, _program_to_release, program_to_source = _sync_program_release_bin_reports(
                product_prefix,
                unique_programs,
                Path(args.release_root),
            )
            if program_to_source:
                print("  BinReport source by program:")
                for program_name in sorted(program_to_source):
                    source = program_to_source[program_name]
                    report_path = program_to_reports.get(program_name)
                    release_name = _program_to_release.get(program_name, "(no-release)")
                    print(
                        f"    {program_name} -> source={source}, release={release_name}, "
                        f"file={report_path if report_path else '(none)'}"
                    )
            for program_name, bin_report_file in program_to_reports.items():
                if not bin_report_file:
                    continue
                program_maps[program_name] = _load_failing_instance_map_from_bin_report(bin_report_file)
            print(
                f"  Program-specific BinReport maps loaded: {len(program_maps):,} / {len(unique_programs):,} program(s)"
            )

    if "DATA_BIN" in out_df.columns and "PROGRAM_OR_BI_RECIPE_NAME" in out_df.columns:
        out_df["Failing_Instance"] = out_df.apply(
            lambda r: _resolve_failing_instance_for_program(
                r.get("DATA_BIN", ""),
                r.get("PROGRAM_OR_BI_RECIPE_NAME", ""),
                program_maps,
            ),
            axis=1,
        )
        populated = out_df["Failing_Instance"].astype(str).str.strip().ne("").sum()
        not_found = (out_df["Failing_Instance"] == "NOT_FOUND").sum()
        print(f"  Failing_Instance populated rows : {populated:,} / {len(out_df):,}")
        print(f"  Failing_Instance NOT_FOUND rows : {not_found:,}")
    else:
        out_df["Failing_Instance"] = ""
        print("  [warn] DATA_BIN column not found; Failing_Instance left empty.")

    # OPERGROUP stores the per-row operation value.
    if "OPERATION" in out_df.columns:
        out_df["OPERGROUP"] = out_df["OPERATION"].fillna("")
    elif "OPERATION" in df.columns:
        operation_map = (
            df.dropna(subset=["OPERATION"])[["VISUAL_ID", "OPERATION"]]
            .drop_duplicates("VISUAL_ID")
            .set_index("VISUAL_ID")["OPERATION"]
        )
        out_df["OPERGROUP"] = out_df["VISUAL_ID"].map(operation_map).fillna("")
    else:
        out_df["OPERGROUP"] = ""
        print("  [warn] OPERATION column not found; OPERGROUP left empty.")

    # Retest_Recovery: 'LATEST' for the latest-run row of each unit-lot pair;
    # for non-latest rows, 'Y' if the LATEST run for that pair has INTERFACE_BIN == 1 (recovered), else 'N'.
    if "Within_LOTS_Latest_Flag" in out_df.columns and "INTERFACE_BIN" in out_df.columns:
        grp_cols = [c for c in ["VISUAL_ID", "LOT"] if c in out_df.columns]
        latest_mask = out_df["Within_LOTS_Latest_Flag"].astype(str).str.strip().str.upper() == "Y"

        out_df["_ibin_int"] = pd.to_numeric(out_df["INTERFACE_BIN"], errors="coerce").fillna(0).astype(int)

        # For each unit-lot group, determine if the latest run passed (INTERFACE_BIN == 1)
        latest_rows = out_df[latest_mask][grp_cols + ["_ibin_int"]].copy()
        latest_rows["_latest_passed"] = latest_rows["_ibin_int"] == 1
        latest_pass_map = latest_rows.drop_duplicates(subset=grp_cols).set_index(grp_cols)["_latest_passed"]

        out_df["_latest_passed"] = out_df[grp_cols].apply(
            lambda r: latest_pass_map.get(tuple(r.values), False), axis=1
        )

        # Assign Retest_Recovery values
        out_df["Retest_Recovery"] = "N"  # default for non-latest rows
        out_df.loc[latest_mask, "Retest_Recovery"] = "LATEST"
        out_df.loc[(~latest_mask) & out_df["_latest_passed"], "Retest_Recovery"] = "Y"
        out_df.drop(columns=["_ibin_int", "_latest_passed"], inplace=True)

        counts = out_df["Retest_Recovery"].value_counts()
        print(f"  Retest_Recovery  LATEST: {counts.get('LATEST', 0):,} | Y: {counts.get('Y', 0):,} | N: {counts.get('N', 0):,}")
    else:
        out_df["Retest_Recovery"] = ""
        print("  [warn] Within_LOTS_Latest_Flag or INTERFACE_BIN not available; Retest_Recovery left empty.")

    _log_step("Stage 4/4 - Building final curated dataframe and writing CSV")
    # 3) Select and order final columns for the curated output.
    for col in FINAL_COLUMNS:
        if col not in out_df.columns:
            out_df[col] = ""
    out_df = out_df[FINAL_COLUMNS]

    out_df.to_csv(final_csv, index=False)
    print(f"  Final CSV : {final_csv}")
    print(f"  Final rows: {len(out_df):,}")

    # By default, remove intermediate raw output once curated CSV is created.
    if args.keep_raw:
        print(f"  Raw CSV kept (--keep-raw): {raw_csv}")
    else:
        try:
            raw_csv.unlink(missing_ok=True)
            print(f"  Raw CSV deleted: {raw_csv}")
        except OSError as exc:
            print(f"  [warn] Failed to delete raw CSV {raw_csv}: {exc}")

    # Quick summary
    if "LOT" in out_df.columns:
        print(f"  Unique LOTs     : {out_df['LOT'].nunique():,}")
    if "FACILITY" in out_df.columns:
        print(f"  Unique FACILITYs: {out_df['FACILITY'].nunique():,}")
    if "INTERFACE_BIN" in out_df.columns:
        print(f"  Unique INTERFACE_BINs: {out_df['INTERFACE_BIN'].nunique():,}")

    _log_step("Stage 4/4 - Completed")
    print("[vpo_bin_attributes_pull] Done.")

    # Trigger HTML report generation if requested
    if args.create_report:
        _log_step("Stage 5/4 - Generating interactive HTML report")
        _generate_html_report(final_csv)

    _cleanup_runtime_resources()


if __name__ == "__main__":
    main()
