"""
Yield_Retest_Report_Create.py
-----------------------------
Generate a Yield/Retest HTML report from a curated CSV output.

This script recreates the report in TOOLS/output_dir and includes:
- KPI cards
- Summary tables
- Clickable bar chart: INTERFACE_BIN -> FUNCTIONAL_BIN -> DATA_BIN
- A checkbox toggle that defaults to excluding INTERFACE_BIN = 1

Usage examples:
    python TOOLS/Yield_Retest_Report_Create.py --csv TOOLS/output_dir/vpo_bin_attrs_GNR_15d_20260416_114356.csv
    python TOOLS/Yield_Retest_Report_Create.py --csv TOOLS/output_dir/vpo_bin_attrs_GNR_15d_20260416_114356.csv --output TOOLS/output_dir/custom_report.html
    python TOOLS/Yield_Retest_Report_Create.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path


def _cell(row: dict[str, str], key: str) -> str:
    return (row.get(key, "") or "").strip()


def _build_table(headers: list[str], records: list[dict[str, str]]) -> str:
    header_html = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body_rows: list[str] = []
    for record in records:
        cells = "".join(f"<td>{escape(str(record.get(h, '')))}</td>" for h in headers)
        body_rows.append(f"<tr>{cells}</tr>")
    body_html = "".join(body_rows)
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"


def _top_counts(counter: Counter[str], label: str, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]:
        rows.append({label: name, "Count": f"{count:,}"})
    return rows


def _build_operation_type_table(op_lot_sets: dict[str, set[str]], op_visual_sets: dict[str, set[str]]) -> str:
  if not op_lot_sets:
    return '<p class="sub">No operation types available.</p>'

  rows_html = "".join(
    f"<tr>"
    f"<td><button type=\"button\" class=\"operation-filter-btn\" data-operation=\"{escape(name)}\">{escape(name)}</button></td>"
    f"<td>{len(lots):,}</td>"
    f"<td>{len(op_visual_sets.get(name, set())):,}</td>"
    f"</tr>"
    for name, lots in sorted(op_lot_sets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
  )
  return (
    "<table><thead><tr><th>Operation type</th><th>VPO count</th><th>Unit count</th></tr></thead>"
    f"<tbody>{rows_html}</tbody></table>"
  )


def _build_flag_table(
    fus_n_count: int, non_fus_n_count: int, all_n_count: int, all_count: int
) -> str:
  def _pct_of_total(count: int, total: int) -> str:
    if total <= 0:
      return "0.00%"
    return f"{(count / total) * 100:.2f}%"

  rows_html = "".join([
    f"<tr>"
    f"<td><button type=\"button\" class=\"retest-type-btn\" data-retest-type=\"fuse\">"
    f"Fuse fail bins</button></td>"
    f"<td>{fus_n_count:,}</td>"
    f"<td>{_pct_of_total(fus_n_count, all_count)}</td>"
    f"</tr>",
    f"<tr>"
    f"<td><button type=\"button\" class=\"retest-type-btn\" data-retest-type=\"non-fuse\">"
    f"Non-Fuse fail bins</button></td>"
    f"<td>{non_fus_n_count:,}</td>"
    f"<td>{_pct_of_total(non_fus_n_count, all_count)}</td>"
    f"</tr>",
    f"<tr>"
    f"<td>Total</td>"
    f"<td>{all_n_count:,}</td>"
    f"<td>{_pct_of_total(all_n_count, all_count)}</td>"
    f"</tr>",
  ])
  return (
    "<table><thead><tr><th>Retest Type</th><th>Retest runs</th><th>Retest rate (%)</th></tr></thead>"
    f"<tbody>{rows_html}</tbody></table>"
  )


def _build_yield_by_workweek_table(
    weekly_all_totals: dict[tuple[int, int], int],
    weekly_n_totals: dict[tuple[int, int], int],
    weekly_latest_totals: dict[tuple[int, int], int],
    weekly_latest_bin1: dict[tuple[int, int], int],
) -> str:
  if not weekly_all_totals:
    return '<p class="sub">No dated rows available for workweek metrics.</p>'

  rows_html = "".join(
    f"<tr>"
    f"<td><button type=\"button\" class=\"workweek-filter-btn\" data-workweek=\"{_format_workweek_key((iso_year, iso_week))}\">{_format_workweek_key((iso_year, iso_week))}</button></td>"
    f"<td>{(((weekly_n_totals.get((iso_year, iso_week), 0) / all_total) * 100) if all_total > 0 else 0.0):.2f}%</td>"
    f"<td>{(((weekly_latest_bin1.get((iso_year, iso_week), 0) / latest_total) * 100) if latest_total > 0 else 0.0):.2f}%</td>"
    f"</tr>"
    for (iso_year, iso_week), all_total in sorted(
      weekly_all_totals.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True
    )
    for latest_total in [weekly_latest_totals.get((iso_year, iso_week), 0)]
  )

  return (
    "<table><thead><tr><th>Workweek</th><th>Retest rate (%)</th><th>Yield(%)</th></tr></thead>"
    f"<tbody>{rows_html}</tbody></table>"
  )


FACILITY_LABELS: dict[str, str] = {
    "CRVC": "Costa Rica",
    "A04": "Malaysia (Penang)",
    "A15": "Malaysia (Kulim)",
    "A90": "Israel",
    "SVC": "Silicon Valley, California",
}


def _build_facility_kpi_legend(facility_counts: Counter[str]) -> str:
  facility_names = sorted(facility_counts.keys())
  if not facility_names:
    return '<div class="facility-kpi-legend empty">No facility values in source CSV.</div>'

  chips = []
  for facility in facility_names:
    label = FACILITY_LABELS.get(facility, "Unknown location")
    chips.append(f'<strong>{escape(facility)}</strong>: {escape(label)}')
  joined = " , ".join(chips)
  return f'<div class="facility-kpi-legend">{joined}</div>'


def _summarize_failing_instances(counter: Counter[str], limit: int = 3) -> str:
  values = [(name, count) for name, count in counter.items() if name]
  if not values:
    return "(blank)"

  ordered = sorted(values, key=lambda kv: (-kv[1], kv[0]))
  shown = [name for name, _ in ordered[:limit]]
  if len(ordered) > limit:
    shown.append(f"+{len(ordered) - limit} more")
  return ", ".join(shown)


def _parse_dt(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None

    # Common MIDAS CSV datetime formats observed across reports.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    # Fallback: try ISO parsing after normalizing trailing Z.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_workweek_key(value: str) -> tuple[int, int] | None:
  text = (value or "").strip().upper()
  if not text:
    return None
  match = re.match(r"^(\d{4})-W{1,2}(\d{1,2})$", text)
  if not match:
    return None
  year = int(match.group(1))
  week = int(match.group(2))
  if week < 1 or week > 53:
    return None
  return (year, week)


def _format_workweek_key(key: tuple[int, int]) -> str:
  year, week = key
  return f"{year}-WW{week:02d}"


def _resolve_input_csv(csv_path: str | None, output_dir: Path) -> Path:
    if csv_path:
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV not found: {path}")
        return path

    candidates = sorted(
        [p for p in output_dir.glob("vpo_bin_attrs_*_*.csv") if "_raw_" not in p.name],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("No curated CSV files found in TOOLS/output_dir")
    return candidates[0]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create Yield/Retest HTML report from curated CSV.",
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        default=None,
        help="Path to curated CSV. If omitted, latest non-raw CSV in TOOLS/output_dir is used.",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        default=None,
        help="Path to output HTML. If omitted, writes vpo_bin_attrs_interactive_report_<csv_stem>.html beside the CSV.",
    )
    parser.add_argument(
        "--preview-rows",
        dest="preview_rows",
        type=int,
        default=200,
        help="Number of rows to show in preview table (default: 200).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    output_dir = Path(__file__).resolve().parent / "output_dir"
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = _resolve_input_csv(args.csv_path, output_dir)

    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows.extend(reader)

    row_count = len(rows)
    unique_lots = len({_cell(r, "LOT") for r in rows if _cell(r, "LOT")})
    unique_visual = len({_cell(r, "VISUAL_ID") for r in rows if _cell(r, "VISUAL_ID")})
    unique_facility = len({_cell(r, "FACILITY") for r in rows if _cell(r, "FACILITY")})
    latest_rows = [r for r in rows if _cell(r, "Within_LOTS_Latest_Flag").upper() == "Y"]
    latest_total_count = len(latest_rows)
    latest_pass_bin1_count = sum(1 for r in latest_rows if _cell(r, "INTERFACE_BIN") == "1")
    yield_pct = (latest_pass_bin1_count / latest_total_count * 100) if latest_total_count else 0.0
    yield_pct_display = f"{yield_pct:.2f}%"

    # Extract unique product prefixes (first 3 letters of PROGRAM_OR_BI_RECIPE_NAME)
    product_prefixes = {
        _cell(r, "PROGRAM_OR_BI_RECIPE_NAME")[:3].upper()
        for r in rows
        if _cell(r, "PROGRAM_OR_BI_RECIPE_NAME") and len(_cell(r, "PROGRAM_OR_BI_RECIPE_NAME")) >= 3
    }
    unique_products = ", ".join(sorted(product_prefixes)) if product_prefixes else "N/A"

    # Extract unique operations from OPERGROUP column
    opergroups = {_cell(r, "OPERGROUP") for r in rows if _cell(r, "OPERGROUP")}
    operations_included = ", ".join(sorted(opergroups)) if opergroups else "N/A"

    start_values = [_parse_dt(_cell(r, "LOTS Start Date Time")) for r in rows]
    end_values = [_parse_dt(_cell(r, "LOTS End Date Time")) for r in rows]
    valid_starts = [d for d in start_values if d is not None]
    valid_ends = [d for d in end_values if d is not None]

    date_range_html = "Date range: N/A"
    if valid_starts or valid_ends:
        range_start = min(valid_starts) if valid_starts else min(valid_ends)
        range_end = max(valid_ends) if valid_ends else max(valid_starts)
        start_ww = range_start.isocalendar()[1]
        start_wd = range_start.isocalendar()[2]
        end_ww = range_end.isocalendar()[1]
        end_wd = range_end.isocalendar()[2]
        num_days = (range_end - range_start).days + 1
        date_range_html = (
            f"<b>Included lots date range: {range_start.strftime('%Y-%m-%d')} (WW{start_ww:02d}.{start_wd}) to "
            f"{range_end.strftime('%Y-%m-%d')} (WW{end_ww:02d}.{end_wd}) | {num_days} days</b>"
        )

    fus_n_count = sum(
        1
        for r in rows
        if _cell(r, "Within_LOTS_Latest_Flag").upper() == "N"
        and _cell(r, "Failing_Instance").startswith("FUS_")
    )
    non_fus_n_count = sum(
        1
        for r in rows
        if _cell(r, "Within_LOTS_Latest_Flag").upper() == "N"
        and not _cell(r, "Failing_Instance").startswith("FUS_")
    )
    all_n_count = sum(
        1
        for r in rows
        if _cell(r, "Within_LOTS_Latest_Flag").upper() == "N"
    )
    all_count = len(rows)

    weekly_all_totals: dict[tuple[int, int], int] = defaultdict(int)
    weekly_n_totals: dict[tuple[int, int], int] = defaultdict(int)
    weekly_latest_totals: dict[tuple[int, int], int] = defaultdict(int)
    weekly_latest_bin1: dict[tuple[int, int], int] = defaultdict(int)
    for row in rows:
      ww_key = _parse_workweek_key(_cell(row, "Workweek"))
      if ww_key is None:
        continue
      weekly_all_totals[ww_key] += 1

      row_flag = _cell(row, "Within_LOTS_Latest_Flag").upper()
      if row_flag == "N":
        weekly_n_totals[ww_key] += 1
      if row_flag == "Y":
        weekly_latest_totals[ww_key] += 1
        if _cell(row, "INTERFACE_BIN") == "1":
          weekly_latest_bin1[ww_key] += 1

    facility_counts = Counter(_cell(r, "FACILITY") or "(blank)" for r in rows)
    facility_lot_sets: dict[str, set[str]] = defaultdict(set)
    facility_visual_sets: dict[str, set[str]] = defaultdict(set)
    operation_lot_sets: dict[str, set[str]] = defaultdict(set)
    operation_visual_sets: dict[str, set[str]] = defaultdict(set)
    for row in rows:
      facility = _cell(row, "FACILITY") or "(blank)"
      lot = _cell(row, "LOT")
      visual = _cell(row, "VISUAL_ID")
      operation_type = _cell(row, "OPERGROUP") or "(blank)"
      if lot:
        facility_lot_sets[facility].add(lot)
        operation_lot_sets[operation_type].add(lot)
      if visual:
        facility_visual_sets[facility].add(visual)
        operation_visual_sets[operation_type].add(visual)

    interface_counts = Counter(_cell(r, "INTERFACE_BIN") or "(blank)" for r in rows)
    functional_counts = Counter(_cell(r, "FUNCTIONAL_BIN") or "(blank)" for r in rows)
    lot_counts = Counter(_cell(r, "LOT") or "(blank)" for r in rows)

    bin_quad_counts: Counter[tuple[str, str, str, str, str]] = Counter(
        (
            _cell(r, "FACILITY") or "(blank)",
        _cell(r, "OPERGROUP") or "(blank)",
            _cell(r, "INTERFACE_BIN") or "(blank)",
            _cell(r, "FUNCTIONAL_BIN") or "(blank)",
            _cell(r, "DATA_BIN") or "(blank)",
        )
        for r in rows
    )
    quad_failing_instances: dict[tuple[str, str, str, str, str], Counter[str]] = defaultdict(Counter)
    quad_flag_y_count: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    quad_flag_n_count: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    quad_fus_count: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    quad_flag_y_fus_count: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    quad_flag_n_fus_count: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    quad_count_by_week: dict[tuple[str, str, str, str, str], Counter[str]] = defaultdict(Counter)
    quad_count_fus_by_week: dict[tuple[str, str, str, str, str], Counter[str]] = defaultdict(Counter)
    quad_count_y_by_week: dict[tuple[str, str, str, str, str], Counter[str]] = defaultdict(Counter)
    quad_count_n_by_week: dict[tuple[str, str, str, str, str], Counter[str]] = defaultdict(Counter)
    quad_count_y_fus_by_week: dict[tuple[str, str, str, str, str], Counter[str]] = defaultdict(Counter)
    quad_count_n_fus_by_week: dict[tuple[str, str, str, str, str], Counter[str]] = defaultdict(Counter)
    quad_recovery_pair_sets: dict[tuple[str, str, str, str, str], set[tuple[str, str]]] = defaultdict(set)
    quad_recovery_pair_fus_sets: dict[tuple[str, str, str, str, str], set[tuple[str, str]]] = defaultdict(set)
    quad_recovery_ny_pair_sets: dict[tuple[str, str, str, str, str], set[tuple[str, str]]] = defaultdict(set)
    quad_recovery_ny_pair_fus_sets: dict[tuple[str, str, str, str, str], set[tuple[str, str]]] = defaultdict(set)
    quad_recovery_pair_sets_by_week: dict[tuple[str, str, str, str, str], dict[str, set[tuple[str, str]]]] = defaultdict(lambda: defaultdict(set))
    quad_recovery_pair_fus_sets_by_week: dict[tuple[str, str, str, str, str], dict[str, set[tuple[str, str]]]] = defaultdict(lambda: defaultdict(set))
    quad_recovery_ny_pair_sets_by_week: dict[tuple[str, str, str, str, str], dict[str, set[tuple[str, str]]]] = defaultdict(lambda: defaultdict(set))
    quad_recovery_ny_pair_fus_sets_by_week: dict[tuple[str, str, str, str, str], dict[str, set[tuple[str, str]]]] = defaultdict(lambda: defaultdict(set))
    quad_workweeks: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
    for row in rows:
        quad_key = (
            _cell(row, "FACILITY") or "(blank)",
        _cell(row, "OPERGROUP") or "(blank)",
            _cell(row, "INTERFACE_BIN") or "(blank)",
            _cell(row, "FUNCTIONAL_BIN") or "(blank)",
            _cell(row, "DATA_BIN") or "(blank)",
        )
        failing_instance = _cell(row, "Failing_Instance") or "(blank)"
        quad_failing_instances[quad_key][failing_instance] += 1
        is_fus = failing_instance.startswith("FUS_")
        if is_fus:
            quad_fus_count[quad_key] += 1
        flag_val = _cell(row, "Within_LOTS_Latest_Flag").upper()
        if flag_val == "Y":
            quad_flag_y_count[quad_key] += 1
            if is_fus:
                quad_flag_y_fus_count[quad_key] += 1
        elif flag_val == "N":
            quad_flag_n_count[quad_key] += 1
            if is_fus:
                quad_flag_n_fus_count[quad_key] += 1

        # Recovery chart metric: unique LOT+VISUAL_ID pairs with Retest_Recovery == 'Y', counted once per bin.
        retest_recovery = _cell(row, "Retest_Recovery").upper()
        lot = _cell(row, "LOT")
        visual = _cell(row, "VISUAL_ID")
        
        # Track workweeks for this bin quad
        ww_key = _parse_workweek_key(_cell(row, "Workweek"))
        ww_label = ""
        if ww_key is not None:
          ww_label = _format_workweek_key(ww_key)
          quad_workweeks[quad_key].add(ww_label)
          quad_count_by_week[quad_key][ww_label] += 1
          if is_fus:
            quad_count_fus_by_week[quad_key][ww_label] += 1
          if flag_val == "Y":
            quad_count_y_by_week[quad_key][ww_label] += 1
            if is_fus:
              quad_count_y_fus_by_week[quad_key][ww_label] += 1
          elif flag_val == "N":
            quad_count_n_by_week[quad_key][ww_label] += 1
            if is_fus:
              quad_count_n_fus_by_week[quad_key][ww_label] += 1
        
        if lot and visual:
            pair_key = (lot, visual)
            if retest_recovery == "Y":
                quad_recovery_pair_sets[quad_key].add(pair_key)
                if is_fus:
                    quad_recovery_pair_fus_sets[quad_key].add(pair_key)
            if ww_label:
              quad_recovery_pair_sets_by_week[quad_key][ww_label].add(pair_key)
              if is_fus:
                quad_recovery_pair_fus_sets_by_week[quad_key][ww_label].add(pair_key)
            if retest_recovery in {"Y", "N"}:
                quad_recovery_ny_pair_sets[quad_key].add(pair_key)
                if is_fus:
                    quad_recovery_ny_pair_fus_sets[quad_key].add(pair_key)
            if ww_label:
              quad_recovery_ny_pair_sets_by_week[quad_key][ww_label].add(pair_key)
              if is_fus:
                quad_recovery_ny_pair_fus_sets_by_week[quad_key][ww_label].add(pair_key)

    chart_json = json.dumps(
        [
            {
                "facility": facility,
              "operation": operation,
                "interface": iface,
                "functional": func,
                "data": data_bin,
                "count": count,
              "count_fus": quad_fus_count[(facility, operation, iface, func, data_bin)],
              "count_y": quad_flag_y_count[(facility, operation, iface, func, data_bin)],
              "count_n": quad_flag_n_count[(facility, operation, iface, func, data_bin)],
              "count_y_fus": quad_flag_y_fus_count[(facility, operation, iface, func, data_bin)],
              "count_n_fus": quad_flag_n_fus_count[(facility, operation, iface, func, data_bin)],
              "week_counts": dict(quad_count_by_week[(facility, operation, iface, func, data_bin)]),
              "week_counts_fus": dict(quad_count_fus_by_week[(facility, operation, iface, func, data_bin)]),
              "week_counts_y": dict(quad_count_y_by_week[(facility, operation, iface, func, data_bin)]),
              "week_counts_n": dict(quad_count_n_by_week[(facility, operation, iface, func, data_bin)]),
              "week_counts_y_fus": dict(quad_count_y_fus_by_week[(facility, operation, iface, func, data_bin)]),
              "week_counts_n_fus": dict(quad_count_n_fus_by_week[(facility, operation, iface, func, data_bin)]),
              "recovered_pairs": len(quad_recovery_pair_sets[(facility, operation, iface, func, data_bin)]),
              "recovered_pairs_fus": len(quad_recovery_pair_fus_sets[(facility, operation, iface, func, data_bin)]),
              "recovery_ny_pairs": len(quad_recovery_ny_pair_sets[(facility, operation, iface, func, data_bin)]),
              "recovery_ny_pairs_fus": len(quad_recovery_ny_pair_fus_sets[(facility, operation, iface, func, data_bin)]),
              "week_recovered_pairs": {
                ww: len(pairs)
                for ww, pairs in quad_recovery_pair_sets_by_week[(facility, operation, iface, func, data_bin)].items()
              },
              "week_recovered_pairs_fus": {
                ww: len(pairs)
                for ww, pairs in quad_recovery_pair_fus_sets_by_week[(facility, operation, iface, func, data_bin)].items()
              },
              "week_recovery_ny_pairs": {
                ww: len(pairs)
                for ww, pairs in quad_recovery_ny_pair_sets_by_week[(facility, operation, iface, func, data_bin)].items()
              },
              "week_recovery_ny_pairs_fus": {
                ww: len(pairs)
                for ww, pairs in quad_recovery_ny_pair_fus_sets_by_week[(facility, operation, iface, func, data_bin)].items()
              },
                "has_fus_prefix": any(
                name.startswith("FUS_") for name in quad_failing_instances[(facility, operation, iface, func, data_bin)]
                ),
              "has_flag_y": quad_flag_y_count[(facility, operation, iface, func, data_bin)] > 0,
              "has_flag_n": quad_flag_n_count[(facility, operation, iface, func, data_bin)] > 0,
                "failing_instance": _summarize_failing_instances(
                quad_failing_instances[(facility, operation, iface, func, data_bin)]
                ),
              "workweeks": sorted(quad_workweeks[(facility, operation, iface, func, data_bin)]),
            }
            for (facility, operation, iface, func, data_bin), count in sorted(
                bin_quad_counts.items(),
              key=lambda kv: (-kv[1], kv[0][0], kv[0][1], kv[0][2], kv[0][3], kv[0][4]),
            )
        ]
    )

    flag_table = _build_flag_table(fus_n_count, non_fus_n_count, all_n_count, all_count)
    yield_by_workweek_table = _build_yield_by_workweek_table(
      weekly_all_totals,
      weekly_n_totals,
      weekly_latest_totals,
      weekly_latest_bin1,
    )

    facility_rows_html = "".join(
        f"<tr>"
        f"<td><button type=\"button\" class=\"facility-filter-btn\" data-facility=\"{escape(facility)}\">{escape(facility)}</button></td>"
        f"<td>{len(facility_lot_sets.get(facility, set())):,}</td>"
        f"<td>{len(facility_visual_sets.get(facility, set())):,}</td>"
        f"</tr>"
        for facility, _ in sorted(facility_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    )
    facility_table = (
        "<table><thead><tr><th>Facility</th><th>VPO Count</th><th>Unit Count</th></tr></thead>"
        f"<tbody>{facility_rows_html}</tbody></table>"
    )
    facility_kpi_legend = _build_facility_kpi_legend(facility_counts)
    operation_type_table = _build_operation_type_table(operation_lot_sets, operation_visual_sets)
    functional_table = _build_table(
        ["FUNCTIONAL_BIN", "Count"],
        _top_counts(functional_counts, "FUNCTIONAL_BIN", 10),
    )
    lot_table = _build_table(
        ["LOT", "Count"],
        _top_counts(lot_counts, "LOT", 20),
    )

    preview_columns = [
        c
        for c in [
            "OPERGROUP",
            "LOT",
            "VISUAL_ID",
            "FACILITY",
            "PROGRAM_OR_BI_RECIPE_NAME",
            "Testing Entity",
            "UNIT_TESTER_SITE_ID",
            "Within_LOTS_Latest_Flag",
            "Within_LOTS_Seq_Num",
            "S_SPEC",
            "FUNCTIONAL_BIN",
            "INTERFACE_BIN",
            "DATA_BIN",
            "Failing_Instance",
            "FRV_SPEC",
            "LOTS Start Date Time",
            "Workweek",
        ]
        if c in fieldnames
    ]

    full_table_rows: list[dict[str, str]] = []
    for row in rows:
      row_dict = {col: _cell(row, col) for col in preview_columns}
      ww_key = _parse_workweek_key(_cell(row, "Workweek"))
      row_dict["_WW"] = _format_workweek_key(ww_key) if ww_key is not None else ""
      full_table_rows.append(row_dict)
    full_table_json = json.dumps(full_table_rows)
    full_table_json_serialized = json.dumps(full_table_json)

    html = f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Fuse Yield & Retest Report - {escape(csv_path.name)}</title>
  <style>
    :root {{ --bg:#e8eff6; --card:#ffffff; --ink:#122033; --muted:#445a72; --line:#bfccda; --accent:#204f7a; }}
    body {{ margin:0; font-family:"Segoe UI","Trebuchet MS",sans-serif; color:var(--ink); background:var(--bg); }}
    .wrap {{ width:min(98vw, 1800px); margin:0 auto; padding:22px 10px 36px; }}
    h1 {{ margin:0 0 6px; }}
    .sub {{ color:var(--muted); margin:0 0 14px; }}
    .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; margin-bottom:14px; }}
    .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px; }}
    .k {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
    .v {{ font-size:28px; color:var(--accent); font-weight:700; }}
    .facility-kpi-legend {{ font-size:11px; font-style:italic; color:var(--muted); margin:0 0 8px; line-height:1.6; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:10px; }}
    section {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px; margin-top:10px; }}
    h2 {{ margin:0 0 8px; font-size:17px; }}
    .table-scroll {{ max-height:360px; overflow:auto; border:1px solid var(--line); border-radius:8px; position:relative; background:#ffffff; }}
    .table-scroll-3rows {{ max-height:150px; }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; font-size:12px; background:#ffffff; }}
    th, td {{ border:1px solid var(--line); padding:6px 8px; text-align:left; }}
    thead th {{ position:sticky; top:0; background:#e8eff6; color:var(--ink); font-weight:600; z-index:4; background-clip:padding-box; box-shadow:inset 0 -1px 0 #c7d4e1; }}
    .filter-row th {{ position:sticky; top:31px; background:#dde7f1; z-index:5; box-shadow:inset 0 -1px 0 #c3d1df; }}
    #datasetTable thead th {{ background:#d7e3ef; color:#102032; font-weight:700; box-shadow:inset 0 -1px 0 #aebfd0; }}
    #datasetTable .filter-row th {{ background:#cad9e8; box-shadow:inset 0 -1px 0 #a5b8cc; }}
    tbody td {{ background:#ffffff; }}
    .col-filter-cell {{ display:flex; gap:0; width:100%; align-items:stretch; border:1px solid #d1d5db; border-radius:4px; background:#fff; overflow:hidden; }}
    .col-filter {{ flex:1; border:none; border-radius:0; padding:4px 6px; font-size:11px; background:#fff; outline:none; }}
    .col-filter:focus {{ outline:2px solid var(--accent); outline-offset:-2px; }}
    .col-filter-select {{ flex:0 0 auto; border:none; border-radius:0; padding:4px 6px; font-size:11px; background:#f8fafc; color:var(--ink); cursor:pointer; border-left:1px solid #dce4ee; outline:none; min-width:60px; }}
    .col-filter-select:hover {{ background:#eef3f8; }}
    .col-filter-select:focus {{ outline:2px solid var(--accent); outline-offset:-2px; }}
    #activeFiltersText {{ color:#dc2626; font-size:12px; margin:6px 0; font-style:italic; min-height:14px; }}
    .dataset-meta {{ font-size:12px; color:var(--muted); margin-bottom:8px; display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center; }}
    .dataset-actions {{ display:flex; gap:8px; align-items:center; }}
    .clear-filters-wrap {{ position:relative; display:inline-flex; align-items:flex-start; }}
    .clear-filters-notice {{
      position:absolute;
      right:-0.55in;
      bottom:calc(100% + 4px);
      color:#dc2626;
      font-size:11px;
      line-height:1.2;
      white-space:nowrap;
      text-align:right;
      pointer-events:none;
    }}
    .dataset-controls {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }}
    .dataset-graph-status {{ margin-left:auto; font-size:12px; font-weight:600; }}
    .dataset-graph-status.off {{ color:var(--muted); }}
    .dataset-graph-status.on {{ color:var(--accent); }}
    .btn-clear {{ border:1px solid var(--accent); border-radius:6px; background:#ffffff; color:var(--accent); font-size:12px; font-weight:600; padding:4px 8px; cursor:pointer; }}
    .btn-clear:hover {{ background:#eef3f8; }}
    .btn-load {{ border:1px solid var(--accent); border-radius:6px; background:var(--accent); color:#fff; font-size:12px; font-weight:600; padding:4px 8px; cursor:pointer; }}
    .btn-load:hover {{ background:#163e62; }}
    .dataset-pagination {{ display:flex; justify-content:space-between; align-items:center; gap:8px; margin-top:8px; font-size:12px; color:var(--muted); flex-wrap:wrap; }}
    .pager-controls {{ display:flex; align-items:center; gap:8px; }}
    .pager-btn {{ border:1px solid var(--accent); border-radius:6px; background:#ffffff; color:var(--accent); font-size:12px; padding:3px 8px; cursor:pointer; }}
    .pager-btn:disabled {{ opacity:0.45; cursor:not-allowed; }}
    .page-size {{ border:1px solid var(--accent); border-radius:4px; padding:3px 6px; font-size:12px; background:#ffffff; color:var(--ink); }}
    .dataset-hint {{ font-size:12px; color:var(--muted); margin:0; }}
    .tree-wrap {{ border:1px solid var(--line); border-radius:8px; background:#f8fbff; padding:8px; }}
    .chart-compare-wrap {{ display:block; }}
    .chart-compare-wrap.dual {{ display:block; }}
    .recovery-tree-wrap {{ display:none; }}
    .chart-compare-wrap.dual .recovery-tree-wrap {{ display:block; margin-top:10px; }}
    .retest-linked-section {{ margin-top:10px; }}
    #treeTitle {{ display:block; font-size:13px; font-weight:700; color:var(--ink); text-align:center; }}
    #recoveryTreeTitle {{ font-size:13px; font-weight:700; color:var(--ink); text-align:center; }}
    .tree-meta {{ display:flex; justify-content:space-between; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }}
    .tree-nav {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
    .crumbs {{ font-size:12px; color:var(--muted); }}
    .crumbs button {{ border:1px solid var(--accent); border-radius:999px; background:#ffffff; color:var(--accent); cursor:pointer; padding:2px 8px; font-size:12px; font-weight:700; }}
    .facility-filter-btn {{ border:none; background:transparent; color:#1f3f5e; cursor:pointer; padding:0; font-size:12px; font-weight:600; text-decoration:underline; text-underline-offset:2px; }}
    .facility-filter-btn:hover {{ color:#17314a; }}
    .facility-filter-btn.active {{ color:#17314a; font-weight:700; text-decoration-thickness:2px; }}
    .facility-row-active td {{ background:#eaf3ff; }}
    .operation-filter-btn {{ border:none; background:transparent; color:#1f3f5e; cursor:pointer; padding:0; font-size:12px; font-weight:600; text-decoration:underline; text-underline-offset:2px; }}
    .operation-filter-btn:hover {{ color:#17314a; }}
    .operation-filter-btn.active {{ color:#17314a; font-weight:700; text-decoration-thickness:2px; }}
    .retest-type-btn {{ border:none; background:transparent; color:#1f3f5e; cursor:pointer; padding:0; font-size:12px; font-weight:600; text-decoration:underline; text-underline-offset:2px; }}
    .retest-type-btn:hover {{ color:#17314a; }}
    .retest-type-btn.active {{ color:#17314a; font-weight:700; text-decoration-thickness:2px; }}
    .operation-row-active td {{ background:#eef4ff; }}
    .hidden {{ display:none !important; }}
    #selectedFacilityChip {{ display:inline-flex; align-items:center; }}
    .facility-chip {{ border:1px solid #7f96ad; border-radius:999px; background:#f4f8fc; color:#203952; padding:2px 8px; font-size:12px; font-weight:700; display:inline-flex; align-items:center; gap:6px; }}
    .facility-chip button {{ border:none; background:transparent; color:#203952; cursor:pointer; font-size:12px; font-weight:700; line-height:1; padding:0; }}
    #selectedOperationChip {{ display:inline-flex; align-items:center; }}
    .operation-chip {{ border:1px solid #6f93b5; border-radius:999px; background:#eaf2fa; color:#1f4568; padding:2px 8px; font-size:12px; font-weight:700; display:inline-flex; align-items:center; gap:6px; }}
    .operation-chip button {{ border:none; background:transparent; color:#1f4568; cursor:pointer; font-size:12px; font-weight:700; line-height:1; padding:0; }}
    .retest-linked-chip-row {{ margin-top:8px; }}
    .data-bin-chip {{ border:1px solid #6f93b5; border-radius:999px; background:#eaf2fa; color:#1d4466; padding:2px 8px; font-size:12px; font-weight:700; display:inline-flex; align-items:center; gap:6px; }}
    .data-bin-chip button {{ border:none; background:transparent; color:#1d4466; cursor:pointer; font-size:12px; font-weight:700; line-height:1; padding:0; }}
    #treeLegend {{ font-size:12px; color:var(--muted); }}
    #recoveryTreeLegend {{ font-size:12px; color:var(--muted); }}
    #treeSvg {{ width:100%; height:auto; min-height:140px; display:block; }}
    .ctl {{ margin-bottom:8px; font-size:13px; color:var(--muted); display:flex; gap:14px; flex-wrap:wrap; align-items:center; }}
    .ctl label {{ display:inline-flex; align-items:center; gap:6px; }}
    .ctl label.disabled {{ color:#9ca3af; cursor:not-allowed; }}
    .ctl input:disabled {{ cursor:not-allowed; }}
    .ctl select {{ padding:4px 6px; border:1px solid var(--accent); border-radius:4px; background:#ffffff; color:var(--ink); font-size:12px; cursor:pointer; }}
    .ctl #filterContainer {{ margin-left:auto; display:inline-flex; align-items:center; gap:6px; }}
    .tree-total {{ text-align:right; font-size:12px; color:#374151; margin-top:6px; font-weight:700; }}
    .chart-legend {{ display:flex; justify-content:space-between; font-size:12px; color:var(--muted); margin-top:8px; padding-top:8px; border-top:1px solid var(--line); }}
    .workweek-filter-btn {{ border:none; background:transparent; color:#1f3f5e; cursor:pointer; padding:0; font-size:12px; font-weight:600; text-decoration:underline; text-underline-offset:2px; }}
    .workweek-filter-btn:hover {{ color:#17314a; }}
    .workweek-filter-btn.active {{ color:#17314a; font-weight:700; text-decoration-thickness:2px; }}
    .workweek-row-active td {{ background:#eef2ff; }}
    #selectedWorkweekChip {{ display:inline-flex; align-items:center; }}
    .workweek-chip {{ border:1px solid #7b99b8; border-radius:999px; background:#eaf2fa; color:#234c70; padding:2px 8px; font-size:12px; font-weight:700; display:inline-flex; align-items:center; gap:6px; }}
    .workweek-chip button {{ border:none; background:transparent; color:#234c70; cursor:pointer; font-size:12px; font-weight:700; line-height:1; padding:0; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Fuse Yield & Retest Report</h1>
    <p class="sub">{date_range_html}</p>

    <div class="kpis">
      <div class="kpi"><div class="k">Product</div><div class="v">{escape(unique_products)}</div></div>
      <div class="kpi"><div class="k">Operations Included</div><div class="v">{escape(operations_included)}</div></div>
      <div class="kpi"><div class="k">Facilities</div><div class="v">{unique_facility:,}</div></div>
      <div class="kpi"><div class="k">Number of VPOs</div><div class="v">{unique_lots:,}</div></div>
      <div class="kpi"><div class="k">Number of units</div><div class="v">{unique_visual:,}</div></div>
      <div class="kpi"><div class="k">Yield</div><div class="v">{yield_pct_display}</div></div>
    </div>

    <div class="grid">
      <section><h2 style="display: flex; justify-content: space-between; align-items: center; margin: 0 0 8px;">By facility <span id="facilityWorkweekChip"></span></h2>{facility_kpi_legend}<div id="facilityTableContainer" class="table-scroll">{facility_table}</div></section>
      <section><h2 style="display: flex; justify-content: space-between; align-items: center; margin: 0 0 8px;">By operation <span id="operationWorkweekChip"></span></h2><div id="operationTypeTableContainer" class="table-scroll">{operation_type_table}</div></section>
      <section><h2 style="display: flex; justify-content: space-between; align-items: center; margin: 0 0 8px;">Retest rate <span id="retestRateWorkweekChip"></span></h2><div id="flagTableContainer" class="table-scroll">{flag_table}</div></section>
      <section id="retestLinkedSection" class="retest-linked-section"><h2 id="retestLinkedTitle"><span id="retestLinkedTitleText">Top 0 by Retest run cells</span> <em style="font-size:12px; color:#6b7280;">(Dynamic table tied to the bar chart)</em></h2><div id="retestLinkedTableContainer" class="table-scroll"></div><div id="retestLinkedDataBinBubble" class="retest-linked-chip-row hidden"></div></section>
      <section><h2>By workweek</h2><div id="yieldByWorkweekTableContainer" class="table-scroll table-scroll-3rows">{yield_by_workweek_table}</div></section>
    </div>

    <section>
      <h2>Analysis chart (Click bar to navigate: Interface Bin -> Functional Bin -> Data Bin)</h2>
      <div class="ctl">
        <label><input type="checkbox" id="fusOnlyToggle" checked /> Show only Fuse bins</label>
        <label id="excludeBin1Label"><input type="checkbox" id="excludeBin1" checked /> Exclude bin 1</label>
        <label><input type="checkbox" id="linkChartFiltersToggle" /> Apply graph filters to the Complete Dataset</label>
        <div id=\"filterContainer\">
          <label>Retest filter:
            <select id=\"flagFilterSelect\">
              <option value=\"y\">Only include latest runs</option>
              <option value=\"n\">Only include retest fail bins</option>
            </select>
          </label>
        </div>
      </div>
      <p id="percentageLegendText" style="font-size:11px; color:#6b7280; margin:0 0 8px 0; font-style:italic;"><strong>Percentage legend:</strong> Format is (true% / relative%) where <strong>true%</strong> is the bin percentage composition of the total latest runs (yield), and <strong>relative%</strong> is the share within the current filtered/displayed chart.</p>
      <div id="chartCompareWrap" class="chart-compare-wrap">
        <div class="tree-wrap">
        <div id="treeTitle">Unit retest runs by bin</div>
        <div class="tree-meta">
          <div class="tree-nav">
            <div id="selectedFacilityChip"></div>
            <div id="selectedOperationChip"></div>
            <div id="selectedWorkweekChip"></div>
            <div class="crumbs" id="treeCrumbs"></div>
          </div>
          <div id="treeLegend">Click a bar to drill down</div>
        </div>
        <svg id="treeSvg" viewBox="0 0 980 420" aria-label="Bin hierarchy bar chart"></svg>
        <div id="treeTotal" class="tree-total"></div>
        <div class="chart-legend">
          <div>Bin number</div>
          <div id="treeMetricLegend">Number of units (true %/ relative %)</div>
        </div>
        </div>
        <div id="recoveryTreeWrap" class="tree-wrap recovery-tree-wrap">
          <div id="recoveryTreeTitle">Unit recovery rate by bin</div>
          <div class="tree-meta">
            <div class="tree-nav"></div>
            <div id="recoveryTreeLegend"><em style="font-size:11px;">Mirrors current drill level</em></div>
          </div>
          <svg id="recoveryTreeSvg" viewBox="0 0 980 420" aria-label="Units recovery rate by bin"></svg>
          <div id="recoveryTreeTotal" class="tree-total"></div>
          <div class="chart-legend">
            <div>Bin number</div>
            <div id="recoveryMetricLegend">Units recovered to bin 1 (%)</div>
          </div>
        </div>
      </div>
    </section>

    <section>
      <h2>Complete Dataset</h2>
      <div class="dataset-meta">
        <div>All rows loaded: {row_count:,}</div>
        <div class="dataset-actions">
          <button type="button" id="exportFilteredDataset" class="btn-load">Export to CSV</button>
          <div class="clear-filters-wrap">
            <div id="clearFiltersNotice" class="clear-filters-notice"></div>
            <button type="button" id="clearDatasetFilters" class="btn-clear">Clear all filters</button>
          </div>
          <div id="datasetCount"></div>
        </div>
      </div>
      <div id="activeFiltersText" style="color:#dc2626; font-size:12px; margin:6px 0; font-style:italic;"></div>
      <div id="datasetContainer">
        <div class="dataset-controls">
          <span>Rows per page:</span>
          <select id="datasetPageSize" class="page-size">
            <option value="100">100</option>
            <option value="200" selected>200</option>
            <option value="500">500</option>
            <option value="1000">1000</option>
          </select>
          <span id="datasetGraphStatus" class="dataset-graph-status"></span>
        </div>
        <div class="table-scroll">
          <table id="datasetTable">
            <thead>
              <tr>
                {''.join(f'<th>{escape(col)}</th>' for col in preview_columns)}
              </tr>
              <tr class="filter-row">
                {''.join(f'<th><div class="col-filter-cell"><input class="col-filter" data-col="{escape(col)}" placeholder="Filter..." /><select class="col-filter-select" data-col="{escape(col)}"><option value="">▼</option></select></div></th>' for col in preview_columns)}
              </tr>
            </thead>
            <tbody id="datasetBody"></tbody>
          </table>
        </div>
        <div class="dataset-pagination">
          <div id="datasetPageSummary"></div>
          <div class="pager-controls">
            <button type="button" id="datasetPrev" class="pager-btn">Previous</button>
            <div id="datasetPageInfo"></div>
            <button type="button" id="datasetNext" class="pager-btn">Next</button>
          </div>
        </div>
      </div>
    </section>

    <p class="sub" style="margin-top:24px; border-top:1px solid var(--line); padding-top:14px;">Source CSV: {escape(csv_path.name)} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
  </div>

  <script>
    const chartRows = {chart_json};
    const datasetRowsSerialized = {full_table_json_serialized};
    const datasetColumns = {json.dumps(preview_columns)};
    const colors = ['#1d4569','#24567f','#2f6591','#3b74a2','#4b83b0','#173954','#234862','#2e556d','#396274','#467286','#1e5f7d','#2c7090'];
    const DISPLAY_NODE_LIMIT = 0; // 0 means no display cap
    let currentLevel = 0;
    const drillPath = {{ interface: null, functional: null }};
    const selectedFacilities = new Set();
    const selectedOperations = new Set();
    const selectedWorkweeks = new Set();
    let datasetRows = null;
    let datasetFilteredRows = [];
    let datasetCurrentPage = 1;
    let datasetPageSize = 200;
    let datasetLoaded = false;
    let datasetDebounceTimer = null;
    let suppressFilterInputLinkToggle = false;
    let dropdownAvailableValueSetByCol = null;
    let clearFiltersNoticeTimer = null;
    let retestLinkedDataBinInjected = '';
    let lastFuseOnlyToggleState = null;
    let lastExcludeBin1ToggleState = null;
    let lastRetestFlagFilterState = null;
    let originalFacilityTableHtml = null;
    let originalOperationTableHtml = null;
    let originalFlagTableHtml = null;

    function getAvailableWorkweekSet() {{
      const allWorkweeks = new Set();
      chartRows.forEach((row) => {{
        const rowWorkweeks = Array.isArray(row.workweeks) ? row.workweeks : [];
        rowWorkweeks.forEach((ww) => allWorkweeks.add(String(ww || '').trim()));
      }});
      allWorkweeks.delete('');
      return allWorkweeks;
    }}

    function isWorkweekFilterActive() {{
      if (selectedWorkweeks.size === 0) return false;
      const allWorkweeks = getAvailableWorkweekSet();
      if (allWorkweeks.size === 0) return false;
      return selectedWorkweeks.size < allWorkweeks.size;
    }}

    function sumSelectedWeekCounts(countMap) {{
      if (!countMap) return 0;
      let total = 0;
      selectedWorkweeks.forEach((ww) => {{
        total += Number(countMap[ww] || 0);
      }});
      return total;
    }}

    function _matchesCurrentChartFilter(row, exclude, fusOnly, flagFilter) {{
      const interfaceBin = String(row.INTERFACE_BIN || '').trim();
      const functionalBin = String(row.FUNCTIONAL_BIN || '').trim();
      const dataBin = String(row.DATA_BIN || '').trim();
      const facility = String(row.FACILITY || '').trim();
      const operation = String(row.OPERGROUP || '').trim();
      const failingInstance = String(row.Failing_Instance || '');
      const rowFlag = String(row['Within_LOTS_Latest_Flag'] || '').trim().toUpperCase();
      const pathInterface = String(drillPath.interface || '').trim();
      const pathFunctional = String(drillPath.functional || '').trim();
      const dataBinFilter = String((getFilterInputByCol('DATA_BIN')?.value || '')).trim();

      if (selectedFacilities.size > 0 && !selectedFacilities.has(facility)) return false;
      if (selectedOperations.size > 0 && !selectedOperations.has(operation)) return false;
      if (isWorkweekFilterActive()) {{
        const rowWorkweek = String(row['_WW'] || '').trim();
        if (!rowWorkweek || !selectedWorkweeks.has(rowWorkweek)) return false;
      }}
      if (exclude && interfaceBin === '1') return false;
      if (fusOnly && !failingInstance.startsWith('FUS_')) return false;
      if (flagFilter === 'y' && rowFlag !== 'Y') return false;
      if (flagFilter === 'n' && rowFlag !== 'N') return false;
      if (currentLevel >= 1 && pathInterface && interfaceBin !== pathInterface) return false;
      if (currentLevel >= 2 && pathFunctional && functionalBin !== pathFunctional) return false;
      if (retestLinkedDataBinInjected && dataBin !== retestLinkedDataBinInjected) return false;
      if (currentLevel >= 2 && dataBinFilter && dataBin !== dataBinFilter) return false;
      return true;
    }}

    function renderRetestLinkedDataBinBubble() {{
      const bubbleHost = document.getElementById('retestLinkedDataBinBubble');
      if (!bubbleHost) return;

      if (!retestLinkedDataBinInjected) {{
        bubbleHost.classList.add('hidden');
        bubbleHost.innerHTML = '';
        return;
      }}

      bubbleHost.classList.remove('hidden');
      bubbleHost.innerHTML = `<span class="data-bin-chip">Data Bin: ${{escapeHtml(retestLinkedDataBinInjected)}} <button type="button" id="clearRetestLinkedDataBin" aria-label="Clear Data Bin table filter">X</button></span>`;
      const clearBtn = document.getElementById('clearRetestLinkedDataBin');
      if (clearBtn) {{
        clearBtn.addEventListener('click', () => {{
          retestLinkedDataBinInjected = '';
          renderRetestLinkedDataBinBubble();
          refreshRetestLinkedTableFromControls();
        }});
      }}
    }}

    function refreshRetestLinkedTableFromControls() {{
      syncToggleState();
      const exclude = document.getElementById('excludeBin1').checked;
      const fusOnly = document.getElementById('fusOnlyToggle').checked;
      const flagFilter = document.getElementById('flagFilterSelect').value;
      renderRetestLinkedTable(exclude, fusOnly, flagFilter);
    }}

    function applyRetestLinkedScrollWindow(container) {{
      if (!container) return;
      const table = container.querySelector('table');
      const header = table ? table.querySelector('thead') : null;
      const bodyRows = table ? Array.from(table.querySelectorAll('tbody tr')) : [];

      if (!table || !header || bodyRows.length <= 4) {{
        container.style.maxHeight = '';
        return;
      }}

      // Keep header visible and allow exactly 4 body rows before scrolling.
      const headerHeight = header.getBoundingClientRect().height;
      const bodyHeight = bodyRows.slice(0, 4).reduce((sum, row) => sum + row.getBoundingClientRect().height, 0);
      container.style.maxHeight = `${{Math.ceil(headerHeight + bodyHeight + 2)}}px`;
    }}

    function renderRetestLinkedTable(exclude, fusOnly, flagFilter) {{
      const section = document.getElementById('retestLinkedSection');
      const container = document.getElementById('retestLinkedTableContainer');
      const titleEl = document.getElementById('retestLinkedTitleText');
      if (!section || !container) return;

      // Keep this table always visible and sync it with the currently-selected chart mode.
      section.classList.remove('hidden');
      renderRetestLinkedDataBinBubble();

      ensureDatasetLoaded();
      const sourceRows = datasetRows || [];
      const grouped = new Map();

      sourceRows.forEach((row) => {{
        if (!_matchesCurrentChartFilter(row, exclude, fusOnly, flagFilter)) return;

        const facility = String(row.FACILITY || '').trim() || '(blank)';
        const testingEntity = String(row['Testing Entity'] || '').trim() || '(blank)';
        const siteId = String(row.UNIT_TESTER_SITE_ID || '').trim() || '(blank)';
        const key = `${{facility}}\u001f${{testingEntity}}\u001f${{siteId}}`;
        grouped.set(key, (grouped.get(key) || 0) + 1);
      }});

      if (!grouped.size) {{
        if (titleEl) titleEl.textContent = 'Top 0 by Retest run cells';
        container.innerHTML = '<p class="sub" style="text-align:center;font-style:italic;padding:48px 0;margin:0;">No data for current chart filters.</p>';
        container.style.maxHeight = '';
        return;
      }}

      const rows = Array.from(grouped.entries())
        .map(([key, count]) => {{
          const [facility, testingEntity, siteId] = key.split('\u001f');
          return {{
            facility,
            testingEntity,
            siteId,
            count,
            label: `${{facility}} | ${{testingEntity}} | ${{siteId}}`,
          }};
        }})
        .sort((a, b) => b.count - a.count || a.facility.localeCompare(b.facility) || a.testingEntity.localeCompare(b.testingEntity) || a.siteId.localeCompare(b.siteId))
          .slice(0, 10);

      if (titleEl) titleEl.textContent = `Top ${{rows.length}} cells`;

      const bodyHtml = rows.map((row) =>
        `<tr><td>${{escapeHtml(row.facility)}}</td><td>${{escapeHtml(row.testingEntity)}}</td><td>${{escapeHtml(row.siteId)}}</td><td>${{row.count.toLocaleString()}}</td></tr>`
      ).join('');

      container.innerHTML = `
        <table>
          <thead>
            <tr>
              <th>Facility</th>
              <th>Tester</th>
              <th>Cell</th>
              <th>Bin count</th>
            </tr>
          </thead>
          <tbody>${{bodyHtml}}</tbody>
        </table>`;
      applyRetestLinkedScrollWindow(container);
    }}

    function buildHierarchy(excludeBin1, fusOnly, flagFilter) {{
      const root = new Map();
      const hasFacilityFilter = selectedFacilities.size > 0;
      const hasOperationFilter = selectedOperations.size > 0;
      const workweekFilterActive = isWorkweekFilterActive();
      chartRows.forEach((r) => {{
        const rowFacility = String(r.facility || '').trim();
        const rowOperation = String(r.operation || '').trim();
        if (hasFacilityFilter && !selectedFacilities.has(rowFacility)) return;
        if (hasOperationFilter && !selectedOperations.has(rowOperation)) return;
        if (workweekFilterActive) {{
          const rowWorkweeks = r.workweeks || [];
          const hasIntersection = rowWorkweeks.some(ww => selectedWorkweeks.has(ww));
          if (!hasIntersection) return;
        }}
        if (excludeBin1 && String(r.interface).trim() === '1') return;
        let effectiveCount = 0;
        if (workweekFilterActive) {{
          if (flagFilter === 'y') {{
            effectiveCount = fusOnly ? sumSelectedWeekCounts(r.week_counts_y_fus) : sumSelectedWeekCounts(r.week_counts_y);
          }} else if (flagFilter === 'n') {{
            effectiveCount = fusOnly ? sumSelectedWeekCounts(r.week_counts_n_fus) : sumSelectedWeekCounts(r.week_counts_n);
          }} else if (fusOnly) {{
            effectiveCount = sumSelectedWeekCounts(r.week_counts_fus);
          }} else {{
            effectiveCount = sumSelectedWeekCounts(r.week_counts);
          }}
        }} else {{
          effectiveCount = Number(r.count || 0);
          if (flagFilter === 'y') {{
            effectiveCount = fusOnly ? Number(r.count_y_fus || 0) : Number(r.count_y || 0);
          }} else if (flagFilter === 'n') {{
            effectiveCount = fusOnly ? Number(r.count_n_fus || 0) : Number(r.count_n || 0);
          }} else if (fusOnly) {{
            effectiveCount = Number(r.count_fus || 0);
          }}
        }}
        if (effectiveCount <= 0) return;
        if (!root.has(r.interface)) root.set(r.interface, new Map());
        const funcMap = root.get(r.interface);
        if (!funcMap.has(r.functional)) funcMap.set(r.functional, new Map());
        const dataMap = funcMap.get(r.functional);
        const existing = dataMap.get(r.data) || {{ count: 0, failingInstance: '(blank)' }};
        dataMap.set(r.data, {{
          count: existing.count + effectiveCount,
          failingInstance: r.failing_instance || existing.failingInstance || '(blank)',
        }});
      }});
      return root;
    }}

    function buildRecoveryHierarchy(excludeBin1, fusOnly) {{
      const root = new Map();
      const hasFacilityFilter = selectedFacilities.size > 0;
      const hasOperationFilter = selectedOperations.size > 0;
      const workweekFilterActive = isWorkweekFilterActive();
      chartRows.forEach((r) => {{
        const rowFacility = String(r.facility || '').trim();
        const rowOperation = String(r.operation || '').trim();
        if (hasFacilityFilter && !selectedFacilities.has(rowFacility)) return;
        if (hasOperationFilter && !selectedOperations.has(rowOperation)) return;
        if (workweekFilterActive) {{
          const rowWorkweeks = r.workweeks || [];
          const hasIntersection = rowWorkweeks.some(ww => selectedWorkweeks.has(ww));
          if (!hasIntersection) return;
        }}
        if (excludeBin1 && String(r.interface).trim() === '1') return;

        const recoveredCount = workweekFilterActive
          ? (fusOnly ? sumSelectedWeekCounts(r.week_recovered_pairs_fus) : sumSelectedWeekCounts(r.week_recovered_pairs))
          : (fusOnly ? Number(r.recovered_pairs_fus || 0) : Number(r.recovered_pairs || 0));
        if (recoveredCount <= 0) return;

        if (!root.has(r.interface)) root.set(r.interface, new Map());
        const funcMap = root.get(r.interface);
        if (!funcMap.has(r.functional)) funcMap.set(r.functional, new Map());
        const dataMap = funcMap.get(r.functional);
        const existing = dataMap.get(r.data) || {{ count: 0, failingInstance: '(blank)' }};
        dataMap.set(r.data, {{
          count: existing.count + recoveredCount,
          failingInstance: r.failing_instance || existing.failingInstance || '(blank)',
        }});
      }});
      return root;
    }}

    function buildRecoveryDenominatorHierarchy(excludeBin1, fusOnly) {{
      const root = new Map();
      const hasFacilityFilter = selectedFacilities.size > 0;
      const hasOperationFilter = selectedOperations.size > 0;
      const workweekFilterActive = isWorkweekFilterActive();
      chartRows.forEach((r) => {{
        const rowFacility = String(r.facility || '').trim();
        const rowOperation = String(r.operation || '').trim();
        if (hasFacilityFilter && !selectedFacilities.has(rowFacility)) return;
        if (hasOperationFilter && !selectedOperations.has(rowOperation)) return;
        if (workweekFilterActive) {{
          const rowWorkweeks = r.workweeks || [];
          const hasIntersection = rowWorkweeks.some(ww => selectedWorkweeks.has(ww));
          if (!hasIntersection) return;
        }}
        if (excludeBin1 && String(r.interface).trim() === '1') return;

        const denomCount = workweekFilterActive
          ? (fusOnly ? sumSelectedWeekCounts(r.week_recovery_ny_pairs_fus) : sumSelectedWeekCounts(r.week_recovery_ny_pairs))
          : (fusOnly ? Number(r.recovery_ny_pairs_fus || 0) : Number(r.recovery_ny_pairs || 0));
        if (denomCount <= 0) return;

        if (!root.has(r.interface)) root.set(r.interface, new Map());
        const funcMap = root.get(r.interface);
        if (!funcMap.has(r.functional)) funcMap.set(r.functional, new Map());
        const dataMap = funcMap.get(r.functional);
        const existing = dataMap.get(r.data) || {{ count: 0, failingInstance: '(blank)' }};
        dataMap.set(r.data, {{
          count: existing.count + denomCount,
          failingInstance: r.failing_instance || existing.failingInstance || '(blank)',
        }});
      }});
      return root;
    }}

    function renderRecoveryChart(showRecoveryChart, recoveryHierarchy, recoveryDenomByLabel, fusOnly) {{
      const compareWrap = document.getElementById('chartCompareWrap');
      const treeTitle = document.getElementById('treeTitle');
      const recoveryWrap = document.getElementById('recoveryTreeWrap');
      const recoverySvg = document.getElementById('recoveryTreeSvg');
      const recoveryLegend = document.getElementById('recoveryTreeLegend');
      const recoveryTotalEl = document.getElementById('recoveryTreeTotal');
      if (!compareWrap || !recoveryWrap || !recoverySvg || !recoveryLegend || !recoveryTotalEl) return;

      if (!showRecoveryChart) {{
        compareWrap.classList.remove('dual');
        if (treeTitle) treeTitle.textContent = 'Yield Analysis';
        recoverySvg.innerHTML = '';
        recoveryLegend.innerHTML = '<em style="font-size:11px;">Mirrors current drill level</em>';
        recoveryTotalEl.textContent = '';
        return;
      }}

      compareWrap.classList.add('dual');
      if (treeTitle) treeTitle.textContent = 'Unit retest runs by bin';
      const textFontSize = '9';
      const recoveryData = getDisplayNodes(getLevelNodes(recoveryHierarchy), DISPLAY_NODE_LIMIT);
      const recoveryTotal = recoveryData.reduce((acc, node) => acc + Number(node.count || 0), 0);
      recoverySvg.innerHTML = '';

      if (!recoveryTotal) {{
        const noRecoveryMsg = fusOnly ? 'No recovered units for current filters' : 'No data';
        recoveryLegend.textContent = '';
        recoveryTotalEl.textContent = 'Total: 0';
        recoverySvg.style.minHeight = '88px';
        recoverySvg.setAttribute('viewBox', '0 0 980 120');
        recoverySvg.innerHTML = `<text x="490" y="60" dominant-baseline="middle" text-anchor="middle" fill="#6b7280" font-size="10">${{noRecoveryMsg}}</text>`;
        return;
      }}

      recoverySvg.style.minHeight = '140px';

      recoveryLegend.innerHTML = currentLevel < 2
        ? '<em style="font-size:11px;">Mirrors current top graph drill level</em>'
        : 'Hover on bar for failing instance details';
      recoveryTotalEl.textContent = `Total: ${{recoveryTotal.toLocaleString()}}`;

      const width = 980;
      const margin = {{ top: 6, right: 90, bottom: 8, left: 170 }};
      const minChartHeight = 140;
      const rowHeight = 18;
      const chartHeight = Math.max(minChartHeight, recoveryData.length * rowHeight);
      const height = margin.top + margin.bottom + chartHeight;
      const chartWidth = width - margin.left - margin.right;
      const barHeight = Math.max(12, rowHeight - 6);
      const maxCount = Math.max(...recoveryData.map((d) => Number(d.count || 0)), 1);

      recoverySvg.setAttribute('viewBox', `0 0 ${{width}} ${{height}}`);

      const axis = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      axis.setAttribute('x1', String(margin.left));
      axis.setAttribute('x2', String(margin.left));
      axis.setAttribute('y1', String(margin.top));
      axis.setAttribute('y2', String(margin.top + chartHeight));
      axis.setAttribute('stroke', '#d1d5db');
      axis.setAttribute('stroke-width', '1');
      recoverySvg.appendChild(axis);

      recoveryData.forEach((d, i) => {{
        const y = margin.top + i * rowHeight;
        const count = Number(d.count || 0);
        const barWidth = (count / maxCount) * chartWidth;
        const denomCount = Number(recoveryDenomByLabel.get(String(d.label)) || 0);
        const pct = denomCount > 0 ? ((count / denomCount) * 100).toFixed(3) : '0.000';
        const canDrillDown = currentLevel < 2 && !d.isOther;
        const canFilterDataBin = currentLevel === 2 && !d.isOther && Boolean(getFilterInputByCol('DATA_BIN'));

        const handleRecoveryBinClick = () => {{
          if (canDrillDown) {{
            if (currentLevel === 0) {{
              currentLevel = 1;
              drillPath.interface = d.label;
              drillPath.functional = null;
            }} else if (currentLevel === 1) {{
              currentLevel = 2;
              drillPath.functional = d.label;
            }}
            renderChart();
            return;
          }}
          if (canFilterDataBin) {{
            const clickedDataBin = String(d.label || '').trim();
            if (clickedDataBin) {{
              retestLinkedDataBinInjected = clickedDataBin;
              renderRetestLinkedDataBinBubble();
            }}
            const linkChartFilters = document.getElementById('linkChartFiltersToggle').checked;
            if (linkChartFilters) {{
              suppressFilterInputLinkToggle = true;
              setFilterInputValue('DATA_BIN', String(d.label));
              suppressFilterInputLinkToggle = false;
              applyDatasetFilters(true);
            }}
            refreshRetestLinkedTableFromControls();
          }}
        }};

        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', String(margin.left - 8));
        label.setAttribute('y', String(y + barHeight / 2 + 4));
        label.setAttribute('text-anchor', 'end');
        label.setAttribute('fill', '#374151');
        label.setAttribute('font-size', textFontSize);
        label.style.cursor = canDrillDown || canFilterDataBin ? 'pointer' : 'default';
        if (canDrillDown || canFilterDataBin) label.addEventListener('click', handleRecoveryBinClick);
        label.textContent = d.label;
        recoverySvg.appendChild(label);

        const bar = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        bar.setAttribute('x', String(margin.left));
        bar.setAttribute('y', String(y));
        bar.setAttribute('width', String(Math.max(barWidth, 1)));
        bar.setAttribute('height', String(barHeight));
        bar.setAttribute('rx', '4');
        bar.setAttribute('fill', colors[i % colors.length]);
        if (currentLevel === 2 && d.failingInstance) {{
          const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
          title.textContent = `Failing Instance: ${{d.failingInstance}}`;
          bar.appendChild(title);
        }}
        bar.style.cursor = canDrillDown || canFilterDataBin ? 'pointer' : 'default';
        if (canDrillDown || canFilterDataBin) bar.addEventListener('click', handleRecoveryBinClick);
        recoverySvg.appendChild(bar);

        const valueText = `${{count.toLocaleString()}}/${{denomCount.toLocaleString()}} (${{pct}}%)`;
        const estimatedTextWidth = valueText.length * 6.5;
        const textX = margin.left + Math.max(barWidth, 1) + 8;
        const isOverflow = textX + estimatedTextWidth > width - margin.right;

        const value = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        value.setAttribute('y', String(y + barHeight / 2 + 4));
        value.setAttribute('font-size', textFontSize);

        if (isOverflow && barWidth > 60) {{
          value.setAttribute('x', String(margin.left + Math.max(barWidth, 1) - 4));
          value.setAttribute('text-anchor', 'end');
          value.setAttribute('fill', '#ffffff');
          value.setAttribute('font-weight', 'bold');
        }} else {{
          value.setAttribute('x', String(textX));
          value.setAttribute('text-anchor', 'start');
          value.setAttribute('fill', '#374151');
        }}
        value.textContent = valueText;
        recoverySvg.appendChild(value);
      }});
    }}

    function toNodes(mapObj) {{
      const nodes = [];
      mapObj.forEach((value, key) => {{
        if (value instanceof Map) {{
          let sum = 0;
          value.forEach((sub) => {{
            if (sub instanceof Map) {{
              sub.forEach((n) => (sum += n.count));
            }} else {{
              sum += Number(sub.count || sub || 0);
            }}
          }});
          nodes.push({{ label: String(key), count: sum, children: value }});
        }} else {{
          nodes.push({{
            label: String(key),
            count: Number(value.count || 0),
            children: null,
            failingInstance: value.failingInstance || '(blank)',
          }});
        }}
      }});
      return nodes.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
    }}

    function getLevelNodes(root) {{
      if (currentLevel === 0) return toNodes(root);
      const funcMap = root.get(drillPath.interface) || new Map();
      if (currentLevel === 1) return toNodes(funcMap);
      const dataMap = (funcMap.get(drillPath.functional) || new Map());
      return toNodes(dataMap);
    }}

    function getDisplayNodes(nodes, limit) {{
      if (!Number.isFinite(limit) || limit <= 0 || nodes.length <= limit) return nodes;
      // Keep only top N real bins; never synthesize an "Other" bucket.
      return nodes.slice(0, limit);
    }}

    function renderCrumbs() {{
      const el = document.getElementById('treeCrumbs');
      const parts = ['<button class="root-btn" data-level="0">INTERFACE_BIN</button>'];
      if (drillPath.interface !== null) parts.push(` / <button data-level="1">${{drillPath.interface}}</button>`);
      if (drillPath.functional !== null) parts.push(` / <button data-level="2">${{drillPath.functional}}</button>`);
      el.innerHTML = parts.join('');
      el.querySelectorAll('button').forEach((btn) => {{
        btn.addEventListener('click', () => {{
          const lvl = Number(btn.getAttribute('data-level'));
          if (lvl <= 0) {{
            currentLevel = 0;
            drillPath.interface = null;
            drillPath.functional = null;
          }} else if (lvl === 1) {{
            currentLevel = 1;
            drillPath.functional = null;
          }} else {{
            currentLevel = 2;
          }}
          // Auto-clear Data Bin filter when navigating away from level 2
          if (lvl < 2 && retestLinkedDataBinInjected) {{
            retestLinkedDataBinInjected = '';
            renderRetestLinkedDataBinBubble();
            refreshRetestLinkedTableFromControls();
          }}
          renderChart();
        }});
      }});
    }}

    function renderSelectedFacilityChip() {{
      const el = document.getElementById('selectedFacilityChip');
      if (!el) return;
      if (selectedFacilities.size === 0) {{
        el.innerHTML = '';
        return;
      }}

      const selectedList = Array.from(selectedFacilities);
      el.innerHTML = selectedList
        .map((facility) => `<span class="facility-chip">${{escapeHtml(facility)}} <button type="button" class="clear-facility-chip" data-facility="${{escapeHtml(facility)}}" aria-label="Clear facility filter for ${{escapeHtml(facility)}}">X</button></span>`)
        .join(' ');

      el.querySelectorAll('.clear-facility-chip').forEach((btn) => {{
        btn.addEventListener('click', () => {{
          const facility = String(btn.getAttribute('data-facility') || '').trim();
          if (facility) selectedFacilities.delete(facility);
          renderChart();
        }});
      }});
    }}

    function renderSelectedOperationChip() {{
      const el = document.getElementById('selectedOperationChip');
      if (!el) return;
      if (selectedOperations.size === 0) {{
        el.innerHTML = '';
        return;
      }}

      const selectedList = Array.from(selectedOperations);
      el.innerHTML = selectedList
        .map((operation) => `<span class="operation-chip">${{escapeHtml(operation)}} <button type="button" class="clear-operation-chip" data-operation="${{escapeHtml(operation)}}" aria-label="Clear operation filter for ${{escapeHtml(operation)}}">X</button></span>`)
        .join(' ');

      el.querySelectorAll('.clear-operation-chip').forEach((btn) => {{
        btn.addEventListener('click', () => {{
          const operation = String(btn.getAttribute('data-operation') || '').trim();
          if (operation) selectedOperations.delete(operation);
          renderChart();
        }});
      }});
    }}

    function syncFacilityTableSelection() {{
      const table = document.getElementById('facilityTableContainer');
      if (!table) return;
      table.querySelectorAll('.facility-filter-btn').forEach((btn) => {{
        const value = String(btn.getAttribute('data-facility') || '').trim();
        const isActive = selectedFacilities.has(value);
        btn.classList.toggle('active', isActive);
        const row = btn.closest('tr');
        if (row) row.classList.toggle('facility-row-active', isActive);
      }});
    }}

    function syncOperationTableSelection() {{
      const table = document.getElementById('operationTypeTableContainer');
      if (!table) return;
      table.querySelectorAll('.operation-filter-btn').forEach((btn) => {{
        const value = String(btn.getAttribute('data-operation') || '').trim();
        const isActive = selectedOperations.has(value);
        btn.classList.toggle('active', isActive);
        const row = btn.closest('tr');
        if (row) row.classList.toggle('operation-row-active', isActive);
      }});
    }}

    function renderSelectedWorkweekChip() {{
      const el = document.getElementById('selectedWorkweekChip');
      if (!el) return;
      if (selectedWorkweeks.size === 0) {{
        el.innerHTML = '';
        return;
      }}

      const selectedList = Array.from(selectedWorkweeks);
      el.innerHTML = selectedList
        .map((workweek) => `<span class="workweek-chip">${{escapeHtml(workweek)}} <button type="button" class="clear-workweek-chip" data-workweek="${{escapeHtml(workweek)}}" aria-label="Clear workweek filter for ${{escapeHtml(workweek)}}">X</button></span>`)
        .join(' ');

      el.querySelectorAll('.clear-workweek-chip').forEach((btn) => {{
        btn.addEventListener('click', () => {{
          const workweek = String(btn.getAttribute('data-workweek') || '').trim();
          if (workweek) selectedWorkweeks.delete(workweek);
          renderChart();
        }});
      }});
    }}

    function syncWorkweekTableSelection() {{
      const table = document.getElementById('yieldByWorkweekTableContainer');
      if (!table) return;
      table.querySelectorAll('.workweek-filter-btn').forEach((btn) => {{
        const value = String(btn.getAttribute('data-workweek') || '').trim();
        const isActive = selectedWorkweeks.has(value);
        btn.classList.toggle('active', isActive);
        const row = btn.closest('tr');
        if (row) row.classList.toggle('workweek-row-active', isActive);
      }});
    }}

    function formatWorkweekShort(fullWorkweek) {{
      // Convert "2026-WW16" to "WW16"
      const match = String(fullWorkweek || '').match(/WW(\\d+)/i);
      return match ? `WW${{match[1]}}` : fullWorkweek;
    }}

    function saveOriginalTableHtml() {{
      if (!originalFacilityTableHtml) {{
        const facilityContainer = document.getElementById('facilityTableContainer');
        if (facilityContainer) originalFacilityTableHtml = facilityContainer.innerHTML;
      }}
      if (!originalOperationTableHtml) {{
        const operationContainer = document.getElementById('operationTypeTableContainer');
        if (operationContainer) originalOperationTableHtml = operationContainer.innerHTML;
      }}
      if (!originalFlagTableHtml) {{
        const flagContainer = document.getElementById('flagTableContainer');
        if (flagContainer) originalFlagTableHtml = flagContainer.innerHTML;
      }}
    }}

    function restoreFacilityTable() {{
      const container = document.getElementById('facilityTableContainer');
      if (container && originalFacilityTableHtml) {{
        container.innerHTML = originalFacilityTableHtml;
      }}
    }}

    function restoreOperationTable() {{
      const container = document.getElementById('operationTypeTableContainer');
      if (container && originalOperationTableHtml) {{
        container.innerHTML = originalOperationTableHtml;
      }}
    }}

    function restoreFlagTable() {{
      const container = document.getElementById('flagTableContainer');
      if (container && originalFlagTableHtml) {{
        container.innerHTML = originalFlagTableHtml;
      }}
    }}

    function rebuildTablesForWorkweekFilter() {{
      if (isWorkweekFilterActive()) {{
        rebuildFacilityTable();
        rebuildOperationTable();
        rebuildFlagTable();
      }} else {{
        restoreFacilityTable();
        restoreOperationTable();
        restoreFlagTable();
      }}
    }}

    function renderFacilityWorkweekChip() {{
      const el = document.getElementById('facilityWorkweekChip');
      if (!el) return;
      if (!isWorkweekFilterActive()) {{
        el.innerHTML = '';
        return;
      }}
      const selectedList = Array.from(selectedWorkweeks).sort();
      el.innerHTML = selectedList
        .map((workweek) => `<span class="workweek-chip" style="margin-left: 4px;">${{escapeHtml(formatWorkweekShort(workweek))}} <button type="button" class="clear-facility-workweek-chip" data-workweek="${{escapeHtml(workweek)}}" aria-label="Clear workweek filter for ${{escapeHtml(workweek)}}">X</button></span>`)
        .join('');
      
      el.querySelectorAll('.clear-facility-workweek-chip').forEach((btn) => {{
        btn.addEventListener('click', () => {{
          const workweek = String(btn.getAttribute('data-workweek') || '').trim();
          if (workweek) selectedWorkweeks.delete(workweek);
          renderChart();
        }});
      }});
    }}

    function renderOperationWorkweekChip() {{
      const el = document.getElementById('operationWorkweekChip');
      if (!el) return;
      if (!isWorkweekFilterActive()) {{
        el.innerHTML = '';
        return;
      }}
      const selectedList = Array.from(selectedWorkweeks).sort();
      el.innerHTML = selectedList
        .map((workweek) => `<span class="workweek-chip" style="margin-left: 4px;">${{escapeHtml(formatWorkweekShort(workweek))}} <button type="button" class="clear-operation-workweek-chip" data-workweek="${{escapeHtml(workweek)}}" aria-label="Clear workweek filter for ${{escapeHtml(workweek)}}">X</button></span>`)
        .join('');
      
      el.querySelectorAll('.clear-operation-workweek-chip').forEach((btn) => {{
        btn.addEventListener('click', () => {{
          const workweek = String(btn.getAttribute('data-workweek') || '').trim();
          if (workweek) selectedWorkweeks.delete(workweek);
          renderChart();
        }});
      }});
    }}

    function renderRetestRateWorkweekChip() {{
      const el = document.getElementById('retestRateWorkweekChip');
      if (!el) return;
      if (!isWorkweekFilterActive()) {{
        el.innerHTML = '';
        return;
      }}
      const selectedList = Array.from(selectedWorkweeks).sort();
      el.innerHTML = selectedList
        .map((workweek) => `<span class="workweek-chip" style="margin-left: 4px;">${{escapeHtml(formatWorkweekShort(workweek))}} <button type="button" class="clear-retest-workweek-chip" data-workweek="${{escapeHtml(workweek)}}" aria-label="Clear workweek filter for ${{escapeHtml(workweek)}}">X</button></span>`)
        .join('');
      
      el.querySelectorAll('.clear-retest-workweek-chip').forEach((btn) => {{
        btn.addEventListener('click', () => {{
          const workweek = String(btn.getAttribute('data-workweek') || '').trim();
          if (workweek) selectedWorkweeks.delete(workweek);
          renderChart();
        }});
      }});
    }}

    function rebuildFacilityTable() {{
      const container = document.getElementById('facilityTableContainer');
      if (!container) return;
      
      if (!isWorkweekFilterActive()) return;
      
      ensureDatasetLoaded();
      const sourceRows = datasetRows || [];
      
      // Count facilities and visuals for selected workweeks only
      const facilityLots = {{}};
      const facilityVisuals = {{}};
      
      sourceRows.forEach((row) => {{
        const rowWorkweek = String(row['_WW'] || '').trim();
        if (!selectedWorkweeks.has(rowWorkweek)) return;
        
        const facility = String(row.FACILITY || '').trim() || '(blank)';
        const lot = String(row.LOT || '').trim();
        const visual = String(row.VISUAL_ID || '').trim();
        
        if (!facilityLots[facility]) {{
          facilityLots[facility] = new Set();
          facilityVisuals[facility] = new Set();
        }}
        if (lot) facilityLots[facility].add(lot);
        if (visual) facilityVisuals[facility].add(visual);
      }});
      
      // Get sorted facilities
      const sortedFacilities = Object.keys(facilityLots).sort((a, b) => {{
        const countA = facilityLots[a].size;
        const countB = facilityLots[b].size;
        return countB - countA || a.localeCompare(b);
      }});
      
      // Rebuild HTML
      const rows_html = sortedFacilities.map(facility => {{
        const lotCount = facilityLots[facility].size;
        const visualCount = facilityVisuals[facility].size;
        return `<tr>
          <td><button type="button" class="facility-filter-btn" data-facility="${{escapeHtml(facility)}}">${{escapeHtml(facility)}}</button></td>
          <td>${{lotCount.toLocaleString()}}</td>
          <td>${{visualCount.toLocaleString()}}</td>
        </tr>`;
      }}).join('');
      
      const table_html = `<table><thead><tr><th>Facility</th><th>VPO Count</th><th>Unit Count</th></tr></thead><tbody>${{rows_html}}</tbody></table>`;
      container.innerHTML = table_html;
    }}

    function rebuildOperationTable() {{
      const container = document.getElementById('operationTypeTableContainer');
      if (!container) return;
      
      if (!isWorkweekFilterActive()) return;
      
      ensureDatasetLoaded();
      const sourceRows = datasetRows || [];
      
      // Count operations and visuals for selected workweeks only
      const operationLots = {{}};
      const operationVisuals = {{}};
      
      sourceRows.forEach((row) => {{
        const rowWorkweek = String(row['_WW'] || '').trim();
        if (!selectedWorkweeks.has(rowWorkweek)) return;
        
        const operation = String(row.OPERGROUP || '').trim() || '(blank)';
        const lot = String(row.LOT || '').trim();
        const visual = String(row.VISUAL_ID || '').trim();
        
        if (!operationLots[operation]) {{
          operationLots[operation] = new Set();
          operationVisuals[operation] = new Set();
        }}
        if (lot) operationLots[operation].add(lot);
        if (visual) operationVisuals[operation].add(visual);
      }});
      
      // Get sorted operations
      const sortedOperations = Object.keys(operationLots).sort((a, b) => {{
        const countA = operationLots[a].size;
        const countB = operationLots[b].size;
        return countB - countA || a.localeCompare(b);
      }});
      
      // Rebuild HTML
      const rows_html = sortedOperations.map(operation => {{
        const lotCount = operationLots[operation].size;
        const visualCount = operationVisuals[operation].size;
        return `<tr>
          <td><button type="button" class="operation-filter-btn" data-operation="${{escapeHtml(operation)}}">${{escapeHtml(operation)}}</button></td>
          <td>${{lotCount.toLocaleString()}}</td>
          <td>${{visualCount.toLocaleString()}}</td>
        </tr>`;
      }}).join('');
      
      const table_html = `<table><thead><tr><th>Operation type</th><th>VPO count</th><th>Unit count</th></tr></thead><tbody>${{rows_html}}</tbody></table>`;
      container.innerHTML = table_html;
    }}

    function rebuildFlagTable() {{
      const container = document.getElementById('flagTableContainer');
      if (!container) return;
      
      if (!isWorkweekFilterActive()) return;
      
      ensureDatasetLoaded();
      const sourceRows = datasetRows || [];
      
      // Count retest flag values for selected workweeks only
      let fus_n_count = 0;
      let non_fus_n_count = 0;
      let all_n_count = 0;
      let all_count = 0;
      
      sourceRows.forEach((row) => {{
        const rowWorkweek = String(row['_WW'] || '').trim();
        if (!selectedWorkweeks.has(rowWorkweek)) return;
        
        all_count += 1;
        const flagVal = String(row['Within_LOTS_Latest_Flag'] || '').trim().toUpperCase();
        const failingInstance = String(row.Failing_Instance || '');
        
        if (flagVal === 'N') {{
          all_n_count += 1;
          if (failingInstance.startsWith('FUS_')) {{
            fus_n_count += 1;
          }} else {{
            non_fus_n_count += 1;
          }}
        }}
      }});
      
      const pctOfTotal = (count, total) => {{
        if (total <= 0) return '0.00%';
        return ((count / total) * 100).toFixed(2) + '%';
      }};
      
      const rows_html = [
        `<tr><td><button type="button" class="retest-type-btn" data-retest-type="fuse">Fuse fail bins</button></td><td>${{fus_n_count.toLocaleString()}}</td><td>${{pctOfTotal(fus_n_count, all_count)}}</td></tr>`,
        `<tr><td><button type="button" class="retest-type-btn" data-retest-type="non-fuse">Non-Fuse fail bins</button></td><td>${{non_fus_n_count.toLocaleString()}}</td><td>${{pctOfTotal(non_fus_n_count, all_count)}}</td></tr>`,
        `<tr><td>Total</td><td>${{all_n_count.toLocaleString()}}</td><td>${{pctOfTotal(all_n_count, all_count)}}</td></tr>`,
      ].join('');
      
      const table_html = `<table><thead><tr><th>Retest Type</th><th>Retest runs</th><th>Retest rate (%)</th></tr></thead><tbody>${{rows_html}}</tbody></table>`;
      container.innerHTML = table_html;
    }}

    function syncToggleState() {{
      const fusOnlyToggle = document.getElementById('fusOnlyToggle');
      const excludeBin1Toggle = document.getElementById('excludeBin1');
      const excludeBin1Label = document.getElementById('excludeBin1Label');
      const flagFilter = document.getElementById('flagFilterSelect').value;
      const disableExclude = fusOnlyToggle.checked || flagFilter === 'n';

      if (fusOnlyToggle.checked) excludeBin1Toggle.checked = true;
      if (flagFilter === 'n') excludeBin1Toggle.checked = true;
      excludeBin1Toggle.disabled = disableExclude;
      excludeBin1Label.classList.toggle('disabled', disableExclude);
    }}

    function renderChart() {{
      syncToggleState();
      const excludeBin1Toggle = document.getElementById('excludeBin1');
      const fusOnly = document.getElementById('fusOnlyToggle').checked;
      const flagFilter = document.getElementById('flagFilterSelect').value;
      const exclude = excludeBin1Toggle.checked;

      // Injected DATA_BIN is only valid at bottom drill level.
      // If chart navigation moves away from DATA_BIN level, clear it.
      if (currentLevel < 2 && retestLinkedDataBinInjected) {{
        retestLinkedDataBinInjected = '';
        renderRetestLinkedDataBinBubble();
        setFilterInputValue('DATA_BIN', '');
      }}

      // Ensure injected DATA_BIN filter is always cleared when either chart toggle changes,
      // including programmatic changes that do not fire the control change handlers.
      const togglesChanged =
        lastFuseOnlyToggleState === null
        || lastExcludeBin1ToggleState === null
        || lastFuseOnlyToggleState !== fusOnly
        || lastExcludeBin1ToggleState !== exclude;
      const retestFilterChanged =
        lastRetestFlagFilterState === null
        || lastRetestFlagFilterState !== flagFilter;
      if ((togglesChanged || retestFilterChanged) && retestLinkedDataBinInjected) {{
        retestLinkedDataBinInjected = '';
        renderRetestLinkedDataBinBubble();
        setFilterInputValue('DATA_BIN', '');
      }}
      lastFuseOnlyToggleState = fusOnly;
      lastExcludeBin1ToggleState = exclude;
      lastRetestFlagFilterState = flagFilter;

      const hierarchy = buildHierarchy(exclude, fusOnly, flagFilter);
      // Keep true% anchored to a fixed baseline for the active mode, including bin 1.
      const baselineFlagFilter = flagFilter === 'n' ? 'n' : 'y';
      const trueHierarchy = buildHierarchy(false, false, baselineFlagFilter);
      const trueRootNodes = toNodes(trueHierarchy);
      const data = getDisplayNodes(getLevelNodes(hierarchy), DISPLAY_NODE_LIMIT);
      const trueLevelNodes = getLevelNodes(trueHierarchy);
      const trueCountByLabel = new Map(trueLevelNodes.map((n) => [String(n.label), Number(n.count || 0)]));
      const total = data.reduce((a,b)=>a+b.count,0);
      const trueTotal = trueRootNodes.reduce((a,b)=>a+Number(b.count || 0),0);
      const svg = document.getElementById('treeSvg');
      const legend = document.getElementById('treeLegend');
      const metricLegend = document.getElementById('treeMetricLegend');
      const totalEl = document.getElementById('treeTotal');
      const percentageLegendText = document.getElementById('percentageLegendText');
      const showRecoveryChart = flagFilter === 'n';
      const textFontSize = '9';
      const recoveryHierarchy = showRecoveryChart ? buildRecoveryHierarchy(exclude, fusOnly) : new Map();
      const recoveryDenominatorHierarchy = showRecoveryChart ? buildRecoveryDenominatorHierarchy(exclude, fusOnly) : new Map();
      svg.innerHTML = '';
      renderSelectedFacilityChip();
      renderSelectedOperationChip();
      renderSelectedWorkweekChip();
      renderFacilityWorkweekChip();
      renderOperationWorkweekChip();
      renderRetestRateWorkweekChip();
      rebuildTablesForWorkweekFilter();
      syncFacilityTableSelection();
      syncOperationTableSelection();
      syncWorkweekTableSelection();
      renderCrumbs();
      const recoveryDenomNodes = showRecoveryChart ? getLevelNodes(recoveryDenominatorHierarchy) : [];
      const recoveryDenomByLabel = new Map(recoveryDenomNodes.map((node) => [String(node.label), Number(node.count || 0)]));
      renderRecoveryChart(showRecoveryChart, recoveryHierarchy, recoveryDenomByLabel, fusOnly);

      const hasSplitPercentages = data.some((d) => {{
        const relativePctVal = total > 0 ? ((Number(d.count || 0) / total) * 100).toFixed(3) : '0.000';
        const trueCountVal = Number(trueCountByLabel.get(String(d.label)) || 0);
        const truePctVal = trueTotal > 0 ? ((trueCountVal / trueTotal) * 100).toFixed(3) : '0.000';
        return truePctVal !== relativePctVal;
      }});

      const metricBaseLabel = flagFilter === 'n' ? 'Number of retest runs' : 'Number of units';

      if (metricLegend) {{
        metricLegend.textContent = hasSplitPercentages
          ? `${{metricBaseLabel}} (true % / relative %)`
          : `${{metricBaseLabel}} (%)`;
      }}

      if (percentageLegendText) {{
        if (hasSplitPercentages) {{
          percentageLegendText.style.display = 'block';
          if (flagFilter === 'n') {{
            percentageLegendText.innerHTML = '<strong>Percentage legend:</strong> Format is (true% / relative%) where <strong>true%</strong> is the bin percentage composition of total retest fail runs, and <strong>relative%</strong> is the share by bin within the current filtered/displayed chart.';
          }} else {{
            percentageLegendText.innerHTML = '<strong>Percentage legend:</strong> Format is (true% / relative%) where <strong>true%</strong> is the bin percentage composition of the total latest runs (yield), and <strong>relative%</strong> is the share by bin within the current filtered/displayed chart.';
          }}
        }} else {{
          percentageLegendText.style.display = 'block';
          percentageLegendText.innerHTML = '<strong>Percentage legend:</strong> Single percentage mode (%) because each bin currently has one effective percentage basis.';
        }}
      }}


      if (!total) {{
        const noDataMsg = fusOnly ? 'No Fuse fail bins to report' : 'No data for current filter';
        legend.textContent = noDataMsg;
        totalEl.textContent = 'Total: 0';
        svg.style.minHeight = '88px';
        svg.setAttribute('viewBox', '0 0 980 120');
        svg.innerHTML = `<text x="490" y="60" dominant-baseline="middle" text-anchor="middle" fill="#6b7280" font-size="10">${{noDataMsg}}</text>`;
        if (datasetLoaded && document.getElementById('linkChartFiltersToggle').checked) {{
          syncGraphFiltersToDatasetInputs();
          applyDatasetFilters(true);
        }}
        refreshRetestLinkedTableFromControls();
        return;
      }}

      svg.style.minHeight = '140px';

      totalEl.textContent = `Total: ${{total.toLocaleString()}}`;

      legend.textContent = currentLevel < 2 ? 'Click a bar to drill down' : 'Hover on bar for failing instance details';

      const width = 980;
      const margin = {{ top: 6, right: 90, bottom: 8, left: 170 }};
      const minChartHeight = 140;
      const rowHeight = 18;
      const chartHeight = Math.max(minChartHeight, data.length * rowHeight);
      const height = margin.top + margin.bottom + chartHeight;
      const chartWidth = width - margin.left - margin.right;
      const barHeight = Math.max(12, rowHeight - 6);
      const maxCount = Math.max(...data.map((d) => d.count), 1);

      svg.setAttribute('viewBox', `0 0 ${{width}} ${{height}}`);

      const axis = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      axis.setAttribute('x1', String(margin.left));
      axis.setAttribute('x2', String(margin.left));
      axis.setAttribute('y1', String(margin.top));
      axis.setAttribute('y2', String(margin.top + chartHeight));
      axis.setAttribute('stroke', '#d1d5db');
      axis.setAttribute('stroke-width', '1');
      svg.appendChild(axis);

      data.forEach((d, i) => {{
        const y = margin.top + i * rowHeight;
        const barWidth = (d.count / maxCount) * chartWidth;
        const relativePct = total > 0 ? ((d.count / total) * 100).toFixed(3) : '0.000';
        const trueCount = Number(trueCountByLabel.get(String(d.label)) || 0);
        const truePct = trueTotal > 0 ? ((trueCount / trueTotal) * 100).toFixed(3) : '0.000';
        const canDrillDown = currentLevel < 2 && !d.isOther;
        const canFilterDataBin = currentLevel === 2 && !d.isOther && Boolean(getFilterInputByCol('DATA_BIN'));

        const handleBinClick = () => {{
          if (canDrillDown) {{
            if (currentLevel === 0) {{
              currentLevel = 1;
              drillPath.interface = d.label;
              drillPath.functional = null;
            }} else if (currentLevel === 1) {{
              currentLevel = 2;
              drillPath.functional = d.label;
            }}
            renderChart();
            return;
          }}
          if (canFilterDataBin) {{
            const clickedDataBin = String(d.label || '').trim();
            if (clickedDataBin) {{
              retestLinkedDataBinInjected = clickedDataBin;
              renderRetestLinkedDataBinBubble();
            }}
            const linkChartFilters = document.getElementById('linkChartFiltersToggle').checked;
            if (linkChartFilters) {{
              suppressFilterInputLinkToggle = true;
              setFilterInputValue('DATA_BIN', String(d.label));
              suppressFilterInputLinkToggle = false;
              applyDatasetFilters(true);
            }}
            refreshRetestLinkedTableFromControls();
          }}
        }};

        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', String(margin.left - 8));
        label.setAttribute('y', String(y + barHeight / 2 + 4));
        label.setAttribute('text-anchor', 'end');
        label.setAttribute('fill', '#374151');
        label.setAttribute('font-size', textFontSize);
        label.style.cursor = canDrillDown || canFilterDataBin ? 'pointer' : 'default';
        if (canDrillDown || canFilterDataBin) label.addEventListener('click', handleBinClick);
        label.textContent = d.label;
        svg.appendChild(label);

        const bar = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        bar.setAttribute('x', String(margin.left));
        bar.setAttribute('y', String(y));
        bar.setAttribute('width', String(Math.max(barWidth, 1)));
        bar.setAttribute('height', String(barHeight));
        bar.setAttribute('rx', '4');
        bar.setAttribute('fill', colors[i % colors.length]);
        if (currentLevel === 2 && d.failingInstance) {{
          const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
          title.textContent = `Failing Instance: ${{d.failingInstance}}`;
          bar.appendChild(title);
        }}
        bar.style.cursor = canDrillDown || canFilterDataBin ? 'pointer' : 'default';
        if (canDrillDown || canFilterDataBin) bar.addEventListener('click', handleBinClick);
        svg.appendChild(bar);

        const pctText = truePct === relativePct
          ? `${{truePct}}%`
          : `${{truePct}}% / ${{relativePct}}%`;
        const valueText = `${{d.count.toLocaleString()}} (${{pctText}})`;
        const estimatedTextWidth = valueText.length * 6.5;
        const textX = margin.left + Math.max(barWidth, 1) + 8;
        const isOverflow = textX + estimatedTextWidth > width - margin.right;
        
        const value = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        value.setAttribute('y', String(y + barHeight / 2 + 4));
        value.setAttribute('font-size', textFontSize);
        
        if (isOverflow && barWidth > 60) {{
          value.setAttribute('x', String(margin.left + Math.max(barWidth, 1) - 4));
          value.setAttribute('text-anchor', 'end');
          value.setAttribute('fill', '#ffffff');
          value.setAttribute('font-weight', 'bold');
        }} else {{
          value.setAttribute('x', String(textX));
          value.setAttribute('text-anchor', 'start');
          value.setAttribute('fill', '#374151');
        }}
        value.textContent = valueText;
        svg.appendChild(value);
      }});

      if (datasetLoaded && document.getElementById('linkChartFiltersToggle').checked) {{
        syncGraphFiltersToDatasetInputs();
        applyDatasetFilters(true);
      }}

      renderRetestLinkedTable(exclude, fusOnly, flagFilter);
    }}

    function escapeHtml(value) {{
      return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }}

    function ensureDatasetLoaded() {{
      if (datasetRows !== null) return;
      datasetRows = JSON.parse(datasetRowsSerialized);
    }}

    function escapeCsvCell(value) {{
      const text = String(value ?? '');
      const hasComma = text.includes(',');
      const hasQuote = text.includes('"');
      const hasLineFeed = text.indexOf(String.fromCharCode(10)) !== -1;
      const hasCarriageReturn = text.indexOf(String.fromCharCode(13)) !== -1;
      if (hasComma || hasQuote || hasLineFeed || hasCarriageReturn) return `"${{text.replace(/"/g, '""')}}"`;
      return text;
    }}

    function exportFilteredDatasetToExcel() {{
      if (!datasetLoaded) return;

      const lineBreak = String.fromCharCode(13, 10);
      const header = datasetColumns.map((col) => escapeCsvCell(col)).join(',');
      const body = datasetFilteredRows
        .map((row) => datasetColumns.map((col) => escapeCsvCell(row[col] || '')).join(','))
        .join(lineBreak);
      const csv = String.fromCharCode(0xFEFF) + header + lineBreak + body;

      const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '_').slice(0, 15);
      const blob = new Blob([csv], {{ type: 'text/csv;charset=utf-8;' }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `complete_dataset_filtered_${{datasetFilteredRows.length}}rows_${{stamp}}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }}

    function renderDatasetTable() {{
      if (!datasetLoaded) return;
      const body = document.getElementById('datasetBody');
      const countEl = document.getElementById('datasetCount');
      const pageInfoEl = document.getElementById('datasetPageInfo');
      const pageSummaryEl = document.getElementById('datasetPageSummary');
      const prevBtn = document.getElementById('datasetPrev');
      const nextBtn = document.getElementById('datasetNext');

      const totalRows = datasetFilteredRows.length;
      const totalPages = Math.max(1, Math.ceil(totalRows / datasetPageSize));
      if (datasetCurrentPage > totalPages) datasetCurrentPage = totalPages;

      const startIndex = (datasetCurrentPage - 1) * datasetPageSize;
      const endIndex = Math.min(startIndex + datasetPageSize, totalRows);
      const pageRows = datasetFilteredRows.slice(startIndex, endIndex);

      const rowsHtml = pageRows.map((row) => `\n        <tr>${{datasetColumns.map((col) => `<td>${{escapeHtml(row[col] || '')}}</td>`).join('')}}</tr>`).join('');
      body.innerHTML = rowsHtml;
      countEl.textContent = `Filtered rows: ${{totalRows.toLocaleString()}} / ${{datasetRows.length.toLocaleString()}}`;
      pageInfoEl.textContent = `Page ${{datasetCurrentPage.toLocaleString()}} / ${{totalPages.toLocaleString()}}`;
      pageSummaryEl.textContent = totalRows
        ? `Showing rows ${{(startIndex + 1).toLocaleString()}}-${{endIndex.toLocaleString()}}`
        : 'Showing rows 0-0';
      prevBtn.disabled = datasetCurrentPage <= 1;
      nextBtn.disabled = datasetCurrentPage >= totalPages;
    }}

    function applyDatasetFilters(resetPage) {{
      if (!datasetLoaded) return;
      const filters = {{}};
      document.querySelectorAll('.col-filter').forEach((input) => {{
        filters[input.getAttribute('data-col')] = input.value.trim().toLowerCase();
      }});

      const linkChartFilters = document.getElementById('linkChartFiltersToggle').checked;
      let exclude = false;
      let fusOnly = false;
      let flagFilter = 'y';
      if (linkChartFilters) {{
        syncToggleState();
        const excludeBin1Toggle = document.getElementById('excludeBin1');
        fusOnly = document.getElementById('fusOnlyToggle').checked;
        flagFilter = document.getElementById('flagFilterSelect').value;
        exclude = excludeBin1Toggle.checked;
      }}

      datasetFilteredRows = datasetRows.filter((row) => datasetColumns.every((col) => {{
        const needle = filters[col] || '';
        if (!needle) return true;
        return String(row[col] || '').toLowerCase().includes(needle);
      }}) && (() => {{
        if (!linkChartFilters) return true;
        const interfaceBin = String(row.INTERFACE_BIN || '').trim();
        const functionalBin = String(row.FUNCTIONAL_BIN || '').trim();
        const facility = String(row.FACILITY || '').trim();
        const operation = String(row.OPERGROUP || '').trim();
        const failingInstance = String(row.Failing_Instance || '');
        const rowFlag = String(row['Within_LOTS_Latest_Flag'] || '').trim().toUpperCase();
        const rowWorkweek = String(row['_WW'] || '').trim();
        const pathInterface = String(drillPath.interface || '').trim();
        const pathFunctional = String(drillPath.functional || '').trim();

        if (selectedFacilities.size > 0 && !selectedFacilities.has(facility)) return false;
        if (selectedOperations.size > 0 && !selectedOperations.has(operation)) return false;
        if (isWorkweekFilterActive() && (!rowWorkweek || !selectedWorkweeks.has(rowWorkweek))) return false;
        if (exclude && interfaceBin === '1') return false;
        if (fusOnly && !failingInstance.startsWith('FUS_')) return false;
        if (flagFilter === 'y' && rowFlag !== 'Y') return false;
        if (flagFilter === 'n' && rowFlag !== 'N') return false;
        if (currentLevel >= 1 && pathInterface && interfaceBin !== pathInterface) return false;
        if (currentLevel >= 2 && pathFunctional && functionalBin !== pathFunctional) return false;
        return true;
      }})());

      dropdownAvailableValueSetByCol = buildAvailableValueSetByCol(datasetFilteredRows);
      updateActiveFiltersDisplay(filters, linkChartFilters, fusOnly, exclude, flagFilter);

      if (resetPage) datasetCurrentPage = 1;
      renderDatasetTable();
      refreshAllColumnDropdowns();
    }}

    function updateActiveFiltersDisplay(columnFilters, linkChartFilters, fusOnly, exclude, flagFilter) {{
      const activeFilterParts = [];
      const hasLinkedFacilities = linkChartFilters && selectedFacilities.size > 0;
      const hasLinkedOperations = linkChartFilters && selectedOperations.size > 0;

      // Collect column filters
      for (const [col, value] of Object.entries(columnFilters)) {{
        if (hasLinkedFacilities && col === 'FACILITY') continue;
        if (hasLinkedOperations && col === 'OPERGROUP') continue;
        if (value) activeFilterParts.push(`${{col}}: "${{value}}"`);
      }}

      // Collect linked chart filters
      if (linkChartFilters) {{
        if (selectedFacilities.size > 0) activeFilterParts.push(`Facilities: ${{Array.from(selectedFacilities).join(', ')}}`);
        if (selectedOperations.size > 0) activeFilterParts.push(`Operations: ${{Array.from(selectedOperations).join(', ')}}`);
        if (isWorkweekFilterActive()) activeFilterParts.push(`Workweeks: ${{Array.from(selectedWorkweeks).join(', ')}}`);
        const pathInterface = String(drillPath.interface || '').trim();
        const pathFunctional = String(drillPath.functional || '').trim();
        if (exclude) activeFilterParts.push('Exclude bin 1: on');
        if (fusOnly) activeFilterParts.push('Fuse bins only: on');
        if (flagFilter === 'n') activeFilterParts.push('Retest fail bins only: on');
        if (pathInterface) activeFilterParts.push(`Interface Bin: ${{pathInterface}}`);
        if (pathFunctional) activeFilterParts.push(`Functional Bin: ${{pathFunctional}}`);
      }}

      const activeFiltersEl = document.getElementById('activeFiltersText');
      if (activeFilterParts.length > 0) {{
        activeFiltersEl.textContent = 'Active filters: ' + activeFilterParts.join(' | ');
      }} else {{
        activeFiltersEl.textContent = '';
      }}
    }}

    function scheduleDatasetFilter() {{
      if (datasetDebounceTimer) clearTimeout(datasetDebounceTimer);
      datasetDebounceTimer = setTimeout(() => applyDatasetFilters(true), 250);
    }}

    function getFilterInputByCol(colName) {{
      return document.querySelector(`.col-filter[data-col="${{colName}}"]`);
    }}

    function setFilterInputValue(colName, value) {{
      const input = getFilterInputByCol(colName);
      if (!input) return;
      input.value = value;
      input.dispatchEvent(new Event('input', {{ bubbles: true }}));

      const select = document.querySelector(`.col-filter-select[data-col="${{colName}}"]`);
      if (select) {{
        const hasOption = Array.from(select.options).some((opt) => opt.value === value);
        select.value = hasOption ? value : '';
      }}
    }}

    function clearAllDatasetColumnFilters() {{
      document.querySelectorAll('.col-filter').forEach((input) => {{
        input.value = '';
      }});
      document.querySelectorAll('.col-filter-select').forEach((select) => {{
        select.value = '';
      }});
    }}

    function updateDatasetGraphStatus() {{
      const statusEl = document.getElementById('datasetGraphStatus');
      if (!statusEl) return;
      const linkChartFilters = document.getElementById('linkChartFiltersToggle').checked;
      if (!linkChartFilters) {{
        statusEl.classList.remove('on');
        statusEl.classList.add('off');
        statusEl.textContent = 'Graph filters not linked';
        return;
      }}

      statusEl.classList.remove('off');
      statusEl.classList.add('on');
      statusEl.textContent = 'Graph filters linked';
    }}

    function syncGraphFiltersToDatasetInputs() {{
      const linkChartFilters = document.getElementById('linkChartFiltersToggle').checked;
      if (!linkChartFilters) {{
        updateDatasetGraphStatus();
        return;
      }}

      const fusOnly = document.getElementById('fusOnlyToggle').checked;
      const flagFilter = document.getElementById('flagFilterSelect').value;
      const facility = selectedFacilities.size === 1 ? Array.from(selectedFacilities)[0] : '';
      const operation = selectedOperations.size === 1 ? Array.from(selectedOperations)[0] : '';
      const pathInterface = currentLevel >= 1 ? String(drillPath.interface || '').trim() : '';
      const pathFunctional = currentLevel >= 2 ? String(drillPath.functional || '').trim() : '';

      suppressFilterInputLinkToggle = true;
      setFilterInputValue('INTERFACE_BIN', pathInterface);
      setFilterInputValue('FUNCTIONAL_BIN', pathFunctional);
      // DATA_BIN is only an explicit click filter at the last chart level; clear it during normal level sync.
      setFilterInputValue('DATA_BIN', '');
      setFilterInputValue('FACILITY', facility);
      setFilterInputValue('OPERGROUP', operation);
      setFilterInputValue('Within_LOTS_Latest_Flag', flagFilter === 'y' ? 'Y' : (flagFilter === 'n' ? 'N' : ''));
      setFilterInputValue('Failing_Instance', fusOnly ? 'FUS_' : '');
      suppressFilterInputLinkToggle = false;
      updateDatasetGraphStatus();
    }}

    const colValueCache = {{}};
    const dropdownRefreshers = {{}};

    function buildAvailableValueSetByCol(rows) {{
      const availableByCol = {{}};
      datasetColumns.forEach((col) => {{
        availableByCol[col] = new Set();
      }});

      rows.forEach((row) => {{
        datasetColumns.forEach((col) => {{
          const val = String(row[col] || '').trim();
          if (val) availableByCol[col].add(val);
        }});
      }});

      return availableByCol;
    }}

    function refreshAllColumnDropdowns() {{
      datasetColumns.forEach((col) => {{
        const refresher = dropdownRefreshers[col];
        if (typeof refresher === 'function') refresher();
      }});
    }}
    
    function buildColumnDropdowns() {{
      if (!datasetLoaded || !datasetRows) return;
      const valueFreqByCol = {{}};
      
      datasetColumns.forEach((col) => {{
        colValueCache[col] = [];
        valueFreqByCol[col] = {{}};
      }});
      
      datasetRows.forEach((row) => {{
        datasetColumns.forEach((col) => {{
          const val = String(row[col] || '').trim();
          if (val) {{
            valueFreqByCol[col][val] = (valueFreqByCol[col][val] || 0) + 1;
          }}
        }});
      }});
      
      datasetColumns.forEach((col) => {{
        colValueCache[col] = Object.entries(valueFreqByCol[col] || {{}})
          .map(([val, freq]) => ({{ val, freq }}))
          .sort((a, b) => b.freq - a.freq)
          .map((item) => item.val);
      }});
      
      document.querySelectorAll('.col-filter-select').forEach((select) => {{
        const col = select.getAttribute('data-col');
        const input = document.querySelector(`.col-filter[data-col="${{col}}"]`);
        
        const updateDropdown = () => {{
          const query = (input.value || '').toLowerCase().trim();
          const availableSet = dropdownAvailableValueSetByCol ? dropdownAvailableValueSetByCol[col] : null;
          select.innerHTML = '<option value="">-- ' + col + ' --</option>';
          
          const matches = colValueCache[col]
            .filter((val) => (!availableSet || availableSet.has(val)) && (query === '' || val.toLowerCase().includes(query)))
            .slice(0, 50);
          
          matches.forEach((val) => {{
            const opt = document.createElement('option');
            opt.value = val;
            opt.textContent = val;
            select.appendChild(opt);
          }});
        }};

        dropdownRefreshers[col] = updateDropdown;
        
        updateDropdown();
        input.addEventListener('input', updateDropdown);
        
        select.addEventListener('change', (event) => {{
          if (input) {{
            const linkToggle = document.getElementById('linkChartFiltersToggle');
            input.value = event.target.value || '';
            if (String(event.target.value || '').trim() && linkToggle.checked) {{
              linkToggle.checked = false;
              updateDatasetGraphStatus();
            }}
            updateDropdown();
            scheduleDatasetFilter();
          }}
        }});
      }});
    }}

    document.getElementById('excludeBin1').addEventListener('change', () => {{
      currentLevel = 0;
      drillPath.interface = null;
      drillPath.functional = null;
      retestLinkedDataBinInjected = '';
      renderRetestLinkedDataBinBubble();
      setFilterInputValue('DATA_BIN', '');
      renderChart();
      if (document.getElementById('linkChartFiltersToggle').checked) applyDatasetFilters(true);
    }});
    document.getElementById('fusOnlyToggle').addEventListener('change', () => {{
      currentLevel = 0;
      drillPath.interface = null;
      drillPath.functional = null;
      retestLinkedDataBinInjected = '';
      renderRetestLinkedDataBinBubble();
      setFilterInputValue('DATA_BIN', '');
      renderChart();
      if (document.getElementById('linkChartFiltersToggle').checked) applyDatasetFilters(true);
    }});
    document.getElementById('flagFilterSelect').addEventListener('change', () => {{
      renderChart();
      if (document.getElementById('linkChartFiltersToggle').checked) applyDatasetFilters(true);
    }});
    const flagTableContainer = document.getElementById('flagTableContainer');
    if (flagTableContainer) {{
      flagTableContainer.addEventListener('click', (event) => {{
        const btn = event.target.closest('.retest-type-btn');
        if (!btn) return;
        const retestType = String(btn.getAttribute('data-retest-type') || '').trim();
        if (!retestType) return;
        const flagFilterSelect = document.getElementById('flagFilterSelect');
        const fusOnlyToggle = document.getElementById('fusOnlyToggle');
        if (retestType === 'fuse') {{
          flagFilterSelect.value = 'n';
          fusOnlyToggle.checked = true;
        }} else if (retestType === 'non-fuse') {{
          flagFilterSelect.value = 'n';
          fusOnlyToggle.checked = false;
        }} else if (retestType === 'all') {{
          flagFilterSelect.value = 'y';
          fusOnlyToggle.checked = false;
        }}
        currentLevel = 0;
        drillPath.interface = null;
        drillPath.functional = null;
        renderChart();
      }});
    }}
    const facilityTableContainer = document.getElementById('facilityTableContainer');
    if (facilityTableContainer) {{
      facilityTableContainer.addEventListener('click', (event) => {{
        const btn = event.target.closest('.facility-filter-btn');
        if (!btn) return;
        const facility = String(btn.getAttribute('data-facility') || '').trim();
        if (!facility) return;
        if (selectedFacilities.has(facility)) {{
          selectedFacilities.delete(facility);
        }} else {{
          selectedFacilities.add(facility);
        }}
        currentLevel = 0;
        drillPath.interface = null;
        drillPath.functional = null;
        renderChart();
      }});
    }}
    const operationTypeTableContainer = document.getElementById('operationTypeTableContainer');
    if (operationTypeTableContainer) {{
      operationTypeTableContainer.addEventListener('click', (event) => {{
        const btn = event.target.closest('.operation-filter-btn');
        if (!btn) return;
        const operation = String(btn.getAttribute('data-operation') || '').trim();
        if (!operation) return;
        if (selectedOperations.has(operation)) {{
          selectedOperations.delete(operation);
        }} else {{
          selectedOperations.add(operation);
        }}
        currentLevel = 0;
        drillPath.interface = null;
        drillPath.functional = null;
        renderChart();
      }});
    }}
    const yieldByWorkweekTableContainer = document.getElementById('yieldByWorkweekTableContainer');
    if (yieldByWorkweekTableContainer) {{
      yieldByWorkweekTableContainer.addEventListener('click', (event) => {{
        const btn = event.target.closest('.workweek-filter-btn');
        if (!btn) return;
        const workweek = String(btn.getAttribute('data-workweek') || '').trim();
        if (!workweek) return;
        if (selectedWorkweeks.has(workweek)) {{
          selectedWorkweeks.delete(workweek);
        }} else {{
          selectedWorkweeks.add(workweek);
        }}
        currentLevel = 0;
        drillPath.interface = null;
        drillPath.functional = null;
        renderChart();
      }});
    }}
    document.querySelectorAll('.col-filter').forEach((input) => {{
      input.addEventListener('input', (event) => {{
        if (suppressFilterInputLinkToggle) return;
        const linkToggle = document.getElementById('linkChartFiltersToggle');
        if (String(event.target.value || '').trim() && linkToggle.checked) {{
          linkToggle.checked = false;
          updateDatasetGraphStatus();
        }}
        scheduleDatasetFilter();
      }});
    }});
    document.getElementById('linkChartFiltersToggle').addEventListener('change', () => {{
      const linkToggle = document.getElementById('linkChartFiltersToggle');
      if (linkToggle.checked) {{
        suppressFilterInputLinkToggle = true;
        clearAllDatasetColumnFilters();
        suppressFilterInputLinkToggle = false;
      }} else {{
        if (clearFiltersNoticeTimer) {{
          clearTimeout(clearFiltersNoticeTimer);
          clearFiltersNoticeTimer = null;
        }}
        document.getElementById('clearFiltersNotice').textContent = '';
      }}
      document.getElementById('activeFiltersText').textContent = '';
      syncGraphFiltersToDatasetInputs();
      applyDatasetFilters(true);
    }});
    document.getElementById('datasetPageSize').addEventListener('change', (event) => {{
      datasetPageSize = Number(event.target.value) || 200;
      datasetCurrentPage = 1;
      renderDatasetTable();
    }});
    document.getElementById('datasetPrev').addEventListener('click', () => {{
      if (datasetCurrentPage > 1) {{
        datasetCurrentPage -= 1;
        renderDatasetTable();
      }}
    }});
    document.getElementById('datasetNext').addEventListener('click', () => {{
      const totalPages = Math.max(1, Math.ceil(datasetFilteredRows.length / datasetPageSize));
      if (datasetCurrentPage < totalPages) {{
        datasetCurrentPage += 1;
        renderDatasetTable();
      }}
    }});
    document.getElementById('clearDatasetFilters').addEventListener('click', () => {{
      const linkChartFilters = document.getElementById('linkChartFiltersToggle').checked;
      const clearFiltersNotice = document.getElementById('clearFiltersNotice');
      document.querySelectorAll('.col-filter').forEach((input) => {{
        input.value = '';
      }});
      document.querySelectorAll('.col-filter-select').forEach((select) => {{
        select.value = '';
      }});
      if (linkChartFilters) {{
        clearFiltersNotice.textContent = 'Graph filters cannot be cleared while they are linked.';
        if (clearFiltersNoticeTimer) clearTimeout(clearFiltersNoticeTimer);
        clearFiltersNoticeTimer = setTimeout(() => {{
          clearFiltersNotice.textContent = '';
          clearFiltersNoticeTimer = null;
        }}, 4000);
        syncGraphFiltersToDatasetInputs();
      }} else {{
        if (clearFiltersNoticeTimer) {{
          clearTimeout(clearFiltersNoticeTimer);
          clearFiltersNoticeTimer = null;
        }}
        clearFiltersNotice.textContent = '';
        document.getElementById('activeFiltersText').textContent = '';
      }}
      updateDatasetGraphStatus();
      applyDatasetFilters(true);
    }});
    document.getElementById('exportFilteredDataset').addEventListener('click', () => {{
      exportFilteredDatasetToExcel();
    }});
    saveOriginalTableHtml();
    renderChart();
    ensureDatasetLoaded();
    datasetLoaded = true;
    buildColumnDropdowns();
    updateDatasetGraphStatus();
    applyDatasetFilters(true);
  </script>
</body>
</html>
'''

    output_path = Path(args.output_path) if args.output_path else Path(f"yield_and_retest_report_{csv_path.stem}.html")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    print(f"REPORT_CREATED: {output_path}")
    print(f"SOURCE_CSV: {csv_path}")
    print(f"ROWS: {row_count}")


if __name__ == "__main__":
    main()
