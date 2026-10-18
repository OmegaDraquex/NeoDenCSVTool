#!/usr/bin/env python3
"""
NeoDen 4 Pick-and-Place CSV & KiCad .pos Converter Utility (v5.0)
-----------------------------------------------------------------
What's New in v5.0:
  - Responsive 2-Column Split Layout: On desktop/non-mobile screens, all
    settings sit cleanly on the left and the full Component Table occupies the
    entire right pane, with fixed/truncated column widths so long values never
    push columns off-screen.
  - Browse-Only File Input: Removed the auto-detected files dropdown; starts
    cleanly with a direct Browse File selector.
  - Two-Stage KiCad .pos Workflow:
      * Stage 1 (Ingest KiCad .pos CSV): Automatically hides all NeoDen
        alignment/rotation/batch settings to prevent confusion, and exports an
        initial NeoDen 4 CSV (preserving raw CAD coordinates) ready to load onto
        the NeoDen 4 machine to align physical Fiducials (mark) and SMD1 (mirror).
      * Stage 2 (Ingest NeoDen 4 CSV): Reveals all alignment, fiducial rotation,
        skip toggle, and batch modification controls to produce the final
        machine-aligned NeoDen 4 CSV.
  - Batch Modify with Undo & Redo: Select Feeder, Footprint, or Nozzle from a
    dropdown, enter the new value/number, specify target component #s (e.g.
    '1-12, 15, 20' or 'all'), and click Apply — with full multi-step Undo and
    Redo buttons.
  - Clear Active Fiducial Display: Section 3 lists every fiducial from 'mark'
    with non-editable X and Y textboxes and badges showing which exact fiducials
    are actively used for rotation vs. excluded as keying/outlier marks.
  - Explicit Defaults & Locked Auto Angle: 'Board Angle' is greyed out and
    uneditable when using 'Auto (Default)', and 'Auto', 'Add board rotation',
    and 'Reset bogus PCB angle' are explicitly labeled as (Default).
  - Minimized Diagnostics Dropdown: The dark diagnostics log is inside a
    collapsible dropdown that is minimized by default.
  - Dedicated Help Page in New Tab: Clicking the Help box opens '/help' in a
    new browser tab explaining every function and the step-by-step workflow.
"""

import argparse
import copy
import csv
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import math
import os
import re
import sys
import threading
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.parse
import webbrowser

if hasattr(sys.stdout, "reconfigure"):
  try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
  except Exception:
    pass

MAX_CSV_BYTES = 5 * 1024 * 1024  # Hard 5 MB cap

# ---------------------------------------------------------------------------
# Built-in NeoDen 4 Machine Template (Used when converting a KiCad .pos file)
# ---------------------------------------------------------------------------

DEFAULT_NEODEN_STACK_LINES = [
    "#Feeder,Feeder ID,Type,Nozzle,X,Y,Angle,Footprint,Value,Pick height,Pick delay,Place Height,Place Delay,Vacuum detection,Threshold,Vision Alignment,Speed,",
    "stack,1,0,1,407.12,87.48,90.00,,0805/0.01uF,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,2,0,1,408.86,101.14,90.00,,,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,3,0,1,405.97,114.97,90.00,,0805/Green,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,4,0,1,409.04,128.28,90.00,,0805/Red,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,5,0,1,408.40,141.54,90.00,,0805/Blue,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,6,0,1,408.68,155.33,90.00,,0805/0.1uF,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,7,0,1,408.35,170.48,90.00,,0805/2M,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,8,0,1,408.54,249.31,90.00,,0805/Zen,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,9,0,1,408.70,263.18,90.00,,0805/10K,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,10,0,1,408.62,276.50,90.00,,0805/1.5k,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,11,0,1,408.23,290.03,90.00,,0805/1K,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,12,0,1,408.93,303.73,90.00,,0805/1M,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,13,0,1,408.25,316.17,90.00,,,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,14,0,1,408.57,330.21,90.00,,,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,15,0,1,408.81,343.96,90.00,,,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,16,0,1,408.52,357.44,90.00,,,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,17,0,1,407.14,374.14,90.00,,,0.50,0,0.00,0,No,-40,1,100,8,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,18,0,1,409.20,388.46,90.00,,,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,19,0,1,413.00,400.00,90.00,,,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,20,0,1,21.33,23.71,90.00,,SOP24/SOIC-24,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,26,0,1,21.75,104.61,-90.00,,,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,28,0,1,22.30,132.29,-90.00,,,0.50,0,0.00,0,No,-40,1,100,4,50,80,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "stack,98,1,1,391.22,49.20,0.00,,SOP16S/counter,0.50,10,0.00,10,No,-40,1,10,1,1,390.78,49.73,1,1,No,No,-40,-40,-40,-40,-1,-1,0,0,",
    "pcb,Manual,Lock,100,100,303.02,174.48,Front,10,10,0,",
]

DEFAULT_REF_PROFILE: Dict[str, Tuple[str, str, str, str, float, str]] = {
    "C1": ("6", "1", "0.01uF", "0805", 0.0, "No"),
    "C2": ("6", "2", "0.01uF", "0805", 0.0, "Yes"),
    "C3": ("4", "1", "0.1uF", "0805", 0.0, "Yes"),
    "D1": ("4", "2", "Green", "0805", 0.0, "Yes"),
    "D2": ("4", "1", "Red", "0805", 0.0, "Yes"),
    "D3": ("4", "2", "Red", "0805", 0.0, "Yes"),
    "D4": ("4", "1", "Red", "0805", 0.0, "Yes"),
    "D5": ("4", "2", "Red", "0805", 0.0, "Yes"),
    "D6": ("4", "1", "Red", "0805", 0.0, "Yes"),
    "D7": ("4", "2", "Red", "0805", 0.0, "Yes"),
    "D8": ("4", "1", "Red", "0805", 0.0, "Yes"),
    "D9": ("4", "2", "Red", "0805", 0.0, "Yes"),
    "D10": ("4", "1", "Red", "0805", 0.0, "Yes"),
    "D11": ("4", "2", "Red", "0805", 0.0, "Yes"),
    "D12": ("5", "1", "Blue", "0805", 0.0, "Yes"),
    "D13": ("5", "2", "Blue", "0805", 0.0, "Yes"),
    "D14": ("5", "1", "Blue", "0805", 0.0, "Yes"),
    "D15": ("5", "2", "Blue", "0805", 0.0, "Yes"),
    "Q1": ("20", "1", "2V", "SOT23", 180.0, "Yes"),
    "Q2": ("20", "2", "2V", "SOT23", 180.0, "Yes"),
    "Q3": ("20", "1", "2V", "SOT23", 180.0, "Yes"),
    "Q4": ("20", "2", "2V", "SOT23", 180.0, "Yes"),
    "Q5": ("20", "1", "2V", "SOT23", 180.0, "Yes"),
    **{f"R{i}": ("20", "1", "1K", "1206", 0.0, "Yes") for i in range(1, 13)},
    **{f"R{i}": ("7", "1", "1K", "0603", 0.0, "Yes") for i in range(13, 25)},
    **{f"R{i}": ("8", "1", "1K", "0402", 0.0, "Yes") for i in range(25, 49)},
    "R49": ("12", "2", "1M", "0805", 0.0, "Yes"),
    "R50": ("8", "1", "Zen", "0805", 0.0, "Yes"),
    "D16": ("8", "1", "Zen", "0805", 0.0, "Yes"),
    "R51": ("9", "2", "10K", "0805", 0.0, "Yes"),
    "R52": ("12", "1", "1M", "0805", 0.0, "Yes"),
    "R53": ("10", "2", "1.5k", "0805", 0.0, "Yes"),
    "R54": ("10", "1", "1.5k", "0805", 0.0, "Yes"),
    "R55": ("10", "2", "1.5k", "0805", 0.0, "Yes"),
    "R56": ("10", "1", "1.5k", "0805", 0.0, "Yes"),
    "R57": ("10", "2", "1.5k", "0805", 0.0, "Yes"),
    "R58": ("10", "1", "1.5k", "0805", 0.0, "Yes"),
    "R59": ("10", "2", "1.5k", "0805", 0.0, "Yes"),
    "R60": ("10", "1", "1.5k", "0805", 0.0, "Yes"),
    "R61": ("10", "2", "1.5k", "0805", 0.0, "Yes"),
    "R62": ("10", "1", "1.5k", "0805", 0.0, "Yes"),
    "R63": ("10", "2", "1.5k", "0805", 0.0, "Yes"),
    "R64": ("9", "1", "10K", "0805", 0.0, "Yes"),
    "R65": ("11", "2", "1K", "0805", 0.0, "Yes"),
    "R66": ("10", "1", "1.5k", "0805", 0.0, "Yes"),
    "R67": ("10", "2", "1.5k", "0805", 0.0, "Yes"),
    "R68": ("10", "1", "1.5k", "0805", 0.0, "Yes"),
    "R69": ("10", "2", "1.5k", "0805", 0.0, "Yes"),
    "U1": ("14", "1", "SOIC-24", "SOP24", 0.0, "Yes"),
    "U2": ("15", "1", "TSSOP-24", "TSSOP24", 0.0, "Yes"),
    "U3": ("16", "1", "LQFP-32", "LQFP32", 0.0, "Yes"),
    "U4": ("17", "1", "TQFP-44", "TQFP44", 0.0, "Yes"),
    "U5": ("18", "1", "TQFP-64", "TQFP64", 0.0, "Yes"),
    "U6": ("19", "1", "QFN-32", "QFN32", 0.0, "Yes"),
    "U7": ("19", "1", "QFN-32", "QFN32", 0.0, "Yes"),
    "U8": ("19", "1", "QFN-32", "QFN32", 0.0, "Yes"),
    "U10": ("98", "3", "counter", "SOP16S", -90.0, "Yes"),
}


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass
class Fiducial:
  index: int
  x: float
  y: float
  corner_label: str = ""
  used_for_rotation: bool = True
  role_note: str = "Used for Rotation"


@dataclass
class PanelMirror:
  index: int
  line_idx: int
  x: float
  y: float
  rotation: float
  skip: str


@dataclass
class ComponentRow:
  line_idx: int
  feeder_id: str
  nozzle: str
  name: str
  value: str
  footprint: str
  x: float
  y: float
  rotation: float
  skip: str
  orig_skip: str = "Yes"
  new_x: float = 0.0
  new_y: float = 0.0
  new_rotation: float = 0.0


@dataclass
class NeoDenCSVAnalysis:
  filepath: str
  source_format: str = "neoden"  # "neoden" or "kicad_pos"
  raw_lines: List[str] = field(default_factory=list)
  had_bom: bool = False
  line_ending: str = "CRLF"
  had_excel_padding: bool = False
  feeder_ids: List[str] = field(default_factory=list)
  pcb_mode: str = ""
  pcb_feed_type: str = ""
  mark_scope: str = ""
  mark_mode: str = ""
  mark_line_idx: int = -1
  fiducials: List[Fiducial] = field(default_factory=list)
  cad_fiducials: List[Fiducial] = field(default_factory=list)
  excluded_parts: List[str] = field(default_factory=list)
  mirror_create_line_idx: int = -1
  mirror_cols: int = 1
  mirror_rows: int = 1
  mirror_pcb_angle: float = 0.0
  panels: List[PanelMirror] = field(default_factory=list)
  components: List[ComponentRow] = field(default_factory=list)

  edge_angles: Dict[str, float] = field(default_factory=dict)
  recommended_angle_deg: float = 0.0
  recommended_method: str = ""
  used_fiducial_indices: List[int] = field(default_factory=list)
  outlier_fiducial_note: str = ""
  warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Format Detection & KiCad Footprint Normalization
# ---------------------------------------------------------------------------


def detect_csv_type_from_head(head_text: str) -> Optional[str]:
  """
  Returns 'neoden' for a NeoDen 4 Job CSV, 'kicad_pos' for a KiCad .pos CSV,
  or None if unrecognized.
  """
  clean = head_text.lstrip("\ufeff").strip()
  if "#Feeder" in clean or clean.startswith("stack,"):
    return "neoden"
  if "Ref,Val,Package,PosX,PosY,Rot,Side" in clean or (
      "PosX" in clean and "PosY" in clean and "Package" in clean
  ):
    return "kicad_pos"
  return None


def normalize_kicad_footprint(pkg: str, val: str = "") -> Tuple[str, float]:
  """
  Converts verbose KiCad package strings into NeoDen 4's short footprint names
  and returns (neoden_footprint, default_rot_offset_deg).
  """
  p = pkg.strip()
  if "SOIC127P600X175-16N" in p or "SOP16" in p.upper():
    return "SOP16S", -90.0
  if p.startswith("SOIC-24") or p.startswith("SOP-24") or "SOP24" in p:
    return "SOP24", 0.0
  if p.startswith("TSSOP-24") or "TSSOP24" in p:
    return "TSSOP24", 0.0
  if p.startswith("LQFP-32") or "LQFP32" in p:
    return "LQFP32", 0.0
  if p.startswith("TQFP-44") or "TQFP44" in p:
    return "TQFP44", 0.0
  if p.startswith("TQFP-64") or "TQFP64" in p:
    return "TQFP64", 0.0
  if p.startswith("QFN-32") or "QFN32" in p:
    return "QFN32", 0.0
  if p in ("SOT-23", "SOT23") or p.startswith("SOT-23"):
    return "SOT23", 180.0

  m_passive = re.search(
      r"(?:^|_)(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?:_|$)", p
  )
  if m_passive:
    return m_passive.group(1), 0.0

  m_ic = re.match(
      r"^(SOIC|SOP|TSSOP|SSOP|MSOP|LQFP|TQFP|QFN|DFN|SOT)-?(\d+)",
      p,
      re.IGNORECASE,
  )
  if m_ic:
    family = m_ic.group(1).upper()
    pins = m_ic.group(2)
    if family == "SOIC":
      family = "SOP"
    return f"{family}{pins}", 0.0

  return p[:16], 0.0


def should_exclude_kicad_part(ref: str, val: str, pkg: str) -> Tuple[bool, str]:
  """Identifies non-SMD / hand-soldered / mechanical parts in a KiCad .pos export."""
  r_up = ref.upper()
  v_up = val.upper()
  p_up = pkg.upper()

  if r_up.startswith("FID") or "FIDUCIAL" in v_up or "FIDUCIAL" in p_up:
    return True, "Fiducial mark (moved to mark section)"
  if p_up.startswith("DIP-") or "DIP_" in p_up:
    return True, f"Through-hole DIP package ({pkg})"
  if r_up.startswith("BATTERY") or "BATTERY" in v_up:
    return True, f"Battery holder/contact ({ref})"
  if r_up.startswith("SW") or "SWITCH" in v_up or "SK12D07" in p_up:
    return True, f"Mechanical switch ({ref})"
  if r_up.startswith("TP") or "TESTPOINT" in p_up:
    return True, f"Test point ({ref})"
  if (
      r_up.startswith("J")
      or r_up.startswith("CONN")
      or "HEADER" in p_up
      or "PINHEADER" in p_up
  ):
    return True, f"Connector/header ({ref})"
  return False, ""


def assign_feeder_for_kicad_part(
    ref: str, val: str, pkg: str
) -> Tuple[str, str, str, str, float, str]:
  """
  Returns (feeder_id, nozzle, neoden_val, neoden_fp, rot_offset_deg, default_skip).
  Also cleans up unannotated KiCad Values that accidentally repeat the footprint name.
  """
  if ref in DEFAULT_REF_PROFILE:
    return DEFAULT_REF_PROFILE[ref]

  fp, rot_off = normalize_kicad_footprint(pkg, val)
  v_clean = val.strip()
  # If KiCad Value is identical to the verbose package name (common on unannotated footprints),
  # shorten it to the normalized footprint so it doesn't clutter the NeoDen UI.
  if v_clean == pkg.strip() and len(v_clean) > 12:
    v_clean = fp

  val_up = v_clean.upper()
  if fp == "0805":
    if val_up in ("0.01UF", "10NF"):
      return ("6", "1", "0.01uF", "0805", rot_off, "Yes")
    if val_up in ("0.1UF", "100NF"):
      return ("4", "1", "0.1uF", "0805", rot_off, "Yes")
    if "GREEN" in val_up:
      return ("3", "1", "Green", "0805", rot_off, "Yes")
    if "RED" in val_up or val_up == "2V":
      return ("4", "1", "Red", "0805", rot_off, "Yes")
    if "BLUE" in val_up or val_up == "3.2V":
      return ("5", "1", "Blue", "0805", rot_off, "Yes")
    if val_up == "10K":
      return ("9", "1", "10K", "0805", rot_off, "Yes")
    if val_up in ("1.5K", "680"):
      return ("10", "1", "1.5k", "0805", rot_off, "Yes")
    if val_up == "1K":
      return ("11", "1", "1K", "0805", rot_off, "Yes")
    if val_up in ("1M", "2M"):
      return ("12", "1", "1M", "0805", rot_off, "Yes")
    return ("1", "1", v_clean, "0805", rot_off, "Yes")

  if fp == "0603":
    return ("7", "1", v_clean, "0603", rot_off, "Yes")
  if fp == "0402":
    return ("8", "1", v_clean, "0402", rot_off, "Yes")
  if fp == "1206":
    return ("20", "1", v_clean, "1206", rot_off, "Yes")
  if fp == "SOT23":
    return ("20", "1", v_clean, "SOT23", rot_off, "Yes")
  if fp == "SOP24":
    return ("14", "1", v_clean, "SOP24", rot_off, "Yes")
  if fp == "TSSOP24":
    return ("15", "1", v_clean, "TSSOP24", rot_off, "Yes")
  if fp == "LQFP32":
    return ("16", "1", v_clean, "LQFP32", rot_off, "Yes")
  if fp == "TQFP44":
    return ("17", "1", v_clean, "TQFP44", rot_off, "Yes")
  if fp == "TQFP64":
    return ("18", "1", v_clean, "TQFP64", rot_off, "Yes")
  if fp == "QFN32":
    return ("19", "1", v_clean, "QFN32", rot_off, "Yes")
  if fp == "SOP16S":
    return ("98", "3", v_clean, "SOP16S", rot_off, "Yes")

  return ("1", "1", v_clean, fp, rot_off, "Yes")


def convert_kicad_pos_text_to_neoden_lines(
    pos_text: str,
    exclude_non_smd: bool = True,
    rename_r50_to_d16: bool = True,
) -> Tuple[List[str], List[Fiducial], List[str]]:
  """
  Stage 1 Converter: Converts a KiCad .pos CSV string into an initial NeoDen 4
  Job CSV preserving the raw KiCad CAD coordinates (so the user can load it onto
  the NeoDen 4 machine, align physical fiducials & SMD1 origins, and export the
  machine CSV for Stage 2).
  """
  active_lines = [
      line
      for line in pos_text.splitlines()
      if line.strip() and not line.lstrip().startswith("#")
  ]
  reader = csv.DictReader(io.StringIO("\n".join(active_lines)))

  cad_fids: List[Fiducial] = []
  excluded_info: List[str] = []
  comp_lines: List[str] = []
  first_comp_xy: Optional[Tuple[float, float]] = None

  for row in reader:
    ref = (row.get("Ref") or "").strip()
    val = (row.get("Val") or "").strip()
    pkg = (row.get("Package") or "").strip()
    side = (row.get("Side") or "top").strip().lower()
    if not ref:
      continue

    try:
      posx = float(row.get("PosX") or 0.0)
      posy = float(row.get("PosY") or 0.0)
      rot = float(row.get("Rot") or 0.0)
    except ValueError:
      continue

    if (
        ref.upper().startswith("FID")
        or "FIDUCIAL" in val.upper()
        or "FIDUCIAL" in pkg.upper()
    ):
      cad_fids.append(
          Fiducial(index=len(cad_fids) + 1, x=posx, y=posy, corner_label=ref)
      )
      continue

    is_excl, reason = should_exclude_kicad_part(ref, val, pkg)
    if exclude_non_smd and is_excl:
      excluded_info.append(f"{ref} ({val}, {pkg}) -> {reason}")
      continue

    if side not in ("top", "t", "front", "f", ""):
      excluded_info.append(f"{ref} ({val}, {pkg}) -> Bottom side ({side})")
      continue

    feeder_id, nozzle, n4_val, n4_fp, rot_off, def_skip = (
        assign_feeder_for_kicad_part(ref, val, pkg)
    )

    out_ref = ref
    if rename_r50_to_d16 and ref == "R50" and n4_val == "Zen":
      out_ref = "D16"

    cad_x = round(posx, 2)
    cad_y = round(posy, 2)
    cad_rot = wrap_neoden_angle(rot + rot_off)

    if len(comp_lines) == 0:
      def_skip = "No"
      first_comp_xy = (cad_x, carry_y := cad_y)

    comp_lines.append(
        f"comp,{feeder_id},{nozzle},{out_ref},{n4_val},{n4_fp},"
        f"{cad_x:.2f},{cad_y:.2f},{cad_rot:.2f},{def_skip},"
    )

  # Build the initial NeoDen 4 header using CAD fiducials (if present in .pos)
  # and setting initial mirror SMD1 to the 1st component's CAD coordinate
  header_lines = list(DEFAULT_NEODEN_STACK_LINES)
  if cad_fids:
    coords_str = ",".join(f"{f.x:.4f},{f.y:.4f}" for f in cad_fids[:4])
    header_lines.append(f"mark,Whole,Auto,{coords_str},")
    for idx_f in range(len(cad_fids[:4])):
      header_lines.append(f"markext,{idx_f},0.8,3,1,0,")
  else:
    header_lines.append("mark,Whole,Auto,0.0000,0.0000,0.0000,0.0000,")
    header_lines.append("markext,0,0.8,3,1,0,")
    header_lines.append("markext,1,0.8,3,1,0,")

  header_lines.append("test,No,")
  c1x, c1y = first_comp_xy if first_comp_xy else (0.0, 0.0)
  header_lines.append(
      f"mirror_create,1,2,{c1x:.2f},{c1y:.2f},{c1x:.2f},{c1y:.2f},0,0,0,"
  )
  header_lines.append(f"mirror,{c1x:.2f},{c1y:.2f},0,No,")
  header_lines.append(f"mirror,{c1x:.2f},{c1y:.2f},0,No,")
  header_lines.append(
      "#SMD,Feeder ID,Nozzle,Name,Value,Footprint,X,Y,Rotation,Skip"
  )

  return header_lines + comp_lines, cad_fids, excluded_info


# ---------------------------------------------------------------------------
# Core Geometry & NeoDen 4 CSV Parser / Formatter
# ---------------------------------------------------------------------------


def wrap_neoden_angle(angle_deg: float) -> float:
  """Wraps an angle in degrees into NeoDen 4's valid [-180.0, +180.0] range."""
  wrapped = (angle_deg + 180.0) % 360.0 - 180.0
  if math.isclose(wrapped, -180.0, abs_tol=1e-6):
    return 180.0
  if math.isclose(wrapped, 0.0, abs_tol=1e-6):
    return 0.0
  return wrapped


def angle_diff_deg(a: float, b: float) -> float:
  """Smallest signed difference (a - b) in degrees within [-180, 180]."""
  return wrap_neoden_angle(a - b)


def clean_excel_corrupted_line(line: str) -> Tuple[str, bool]:
  """
  Repairs a single line if Microsoft Excel padded it with extra trailing commas
  or stripped trailing commas required by the NeoDen 4 C++ parser.
  """
  orig = line
  stripped = line.strip()
  if not stripped:
    return orig, False

  tokens = stripped.split(",")
  prefix = tokens[0].strip()

  if prefix == "#SMD":
    clean_tokens = [t for t in tokens if t != ""][:10]
    cleaned = ",".join(clean_tokens)
    return cleaned, (cleaned != orig)

  expected_fields = {
      "#Feeder": 17,
      "pcb": 11,
      "markext": 6,
      "test": 2,
      "mirror_create": 10,
      "mirror": 5,
      "comp": 10,
  }

  if prefix == "stack":
    feeder_type = tokens[2].strip() if len(tokens) > 2 else "0"
    n_fields = 33 if feeder_type == "1" else 30
    if len(tokens) > n_fields + 1 and all(
        t == "" for t in tokens[n_fields + 1 :]
    ):
      cleaned = ",".join(tokens[:n_fields]) + ","
      return cleaned, True
    if not orig.endswith(","):
      return orig + ",", True
    return orig, False

  if prefix == "mark":
    while len(tokens) > 2 and tokens[-1] == "" and tokens[-2] == "":
      tokens.pop()
    if tokens[-1] != "":
      tokens.append("")
    cleaned = ",".join(tokens)
    return cleaned, (cleaned != orig)

  if prefix in expected_fields:
    n_fields = expected_fields[prefix]
    if len(tokens) > n_fields + 1 and all(
        t == "" for t in tokens[n_fields + 1 :]
    ):
      tokens = tokens[:n_fields] + [""]
    elif tokens[-1] != "":
      tokens = tokens[:n_fields] + [""]

    if prefix == "comp" and len(tokens) >= 10:
      for col_idx in (6, 7, 8):
        try:
          val = float(tokens[col_idx])
          tokens[col_idx] = f"{val:.2f}"
        except ValueError:
          pass

    cleaned = ",".join(tokens)
    return cleaned, (cleaned != orig)

  return orig, False


def analyze_fiducials(
    fiducials: List[Fiducial],
    cad_dx: Optional[float] = None,
    cad_dy: Optional[float] = None,
) -> Tuple[Dict[str, float], float, str, List[int], str]:
  """
  Classifies 2, 3, or 4 fiducials, calculates PCB rotation angle (deg CCW),
  and marks which exact fiducials are used for rotation vs. excluded.
  Returns (edge_angles, recommended_angle, method_name, used_indices, outlier_note).
  """
  edge_angles: Dict[str, float] = {}
  outlier_note = ""
  used_indices: List[int] = []

  n = len(fiducials)
  if n < 2:
    for f in fiducials:
      f.used_for_rotation = False
      f.role_note = "Insufficient Fiducials (<2)"
    return edge_angles, 0.0, "Insufficient fiducials (<2)", used_indices, outlier_note

  if n == 2:
    p1, p2 = fiducials[0], fiducials[1]
    dx = p2.x - p1.x
    dy = p2.y - p1.y
    raw_angle = math.degrees(math.atan2(dy, dx))
    p1.corner_label = "Pt1"
    p2.corner_label = "Pt2"
    p1.used_for_rotation = True
    p2.used_for_rotation = True
    p1.role_note = "Used (Point 1)"
    p2.role_note = "Used (Point 2)"
    used_indices = [p1.index, p2.index]

    if abs(dy) < 0.15 * abs(dx):
      rot = wrap_neoden_angle(raw_angle if dx > 0 else raw_angle - 180.0)
      edge_angles["Horizontal Edge (Fid1->Fid2)"] = rot
      return edge_angles, rot, "2-Fiducial Horizontal Edge", used_indices, outlier_note
    if abs(dx) < 0.15 * abs(dy):
      rot = wrap_neoden_angle(
          raw_angle - 90.0 if dy > 0 else raw_angle + 90.0
      )
      edge_angles["Vertical Edge (Fid1->Fid2)"] = rot
      return edge_angles, rot, "2-Fiducial Vertical Edge", used_indices, outlier_note

    if cad_dx is not None and cad_dy is not None and (
        abs(cad_dx) > 1e-3 or abs(cad_dy) > 1e-3
    ):
      cad_dx_signed = math.copysign(abs(cad_dx), dx)
      cad_dy_signed = math.copysign(abs(cad_dy), dy)
      nominal_diag = math.degrees(math.atan2(cad_dy_signed, cad_dx_signed))
      rot = wrap_neoden_angle(raw_angle - nominal_diag)
      edge_angles[f"Rect Diagonal (nominal {nominal_diag:.2f} deg)"] = rot
      return (
          edge_angles,
          rot,
          "2-Fiducial Rectangular Diagonal (CAD dX/dY)",
          used_indices,
          outlier_note,
      )
    else:
      nominal_diag = math.degrees(
          math.atan2(math.copysign(1.0, dy), math.copysign(1.0, dx))
      )
      rot = wrap_neoden_angle(raw_angle - nominal_diag)
      edge_angles[f"Square Diagonal (nominal {nominal_diag:.1f} deg)"] = rot
      ratio = abs(dx) / max(abs(dy), 1e-6)
      if not (0.85 <= ratio <= 1.18):
        outlier_note = (
            f"Note: 2 fiducials have |dX/dY| = {ratio:.2f} (not a 1:1 square)."
            " Supply CAD dX & dY or use 3-4 fiducials for rectangular boards."
        )
      return (
          edge_angles,
          rot,
          "2-Fiducial Square Diagonal (45 deg)",
          used_indices,
          outlier_note,
      )

  cx = sum(f.x for f in fiducials) / n
  cy = sum(f.y for f in fiducials) / n
  corners: Dict[str, Fiducial] = {}
  for f in fiducials:
    horiz = "R" if f.x >= cx else "L"
    vert = "T" if f.y >= cy else "B"
    label = f"{vert}{horiz}"
    f.corner_label = label
    corners[label] = f

  if "BL" in corners and "BR" in corners:
    dx = corners["BR"].x - corners["BL"].x
    dy = corners["BR"].y - corners["BL"].y
    edge_angles["Bottom Edge (BL->BR)"] = math.degrees(math.atan2(dy, dx))
  if "TL" in corners and "TR" in corners:
    dx = corners["TR"].x - corners["TL"].x
    dy = corners["TR"].y - corners["TL"].y
    edge_angles["Top Edge (TL->TR)"] = math.degrees(math.atan2(dy, dx))
  if "BL" in corners and "TL" in corners:
    dx = corners["TL"].x - corners["BL"].x
    dy = corners["TL"].y - corners["BL"].y
    edge_angles["Left Edge (BL->TL)"] = (
        math.degrees(math.atan2(dy, dx)) - 90.0
    )
  if "BR" in corners and "TR" in corners:
    dx = corners["TR"].x - corners["BR"].x
    dy = corners["TR"].y - corners["BR"].y
    edge_angles["Right Edge (BR->TR)"] = (
        math.degrees(math.atan2(dy, dx)) - 90.0
    )

  if not edge_angles:
    return edge_angles, 0.0, "Could not classify corner edges", used_indices, outlier_note

  corner_pairs = [
      (
          "TL Corner (Left + Top Edges)",
          "Left Edge (BL->TL)",
          "Top Edge (TL->TR)",
          "BR",
          ("BL", "TL", "TR"),
      ),
      (
          "BL Corner (Left + Bottom Edges)",
          "Left Edge (BL->TL)",
          "Bottom Edge (BL->BR)",
          "TR",
          ("TL", "BL", "BR"),
      ),
      (
          "TR Corner (Right + Top Edges)",
          "Right Edge (BR->TR)",
          "Top Edge (TL->TR)",
          "BL",
          ("TL", "TR", "BR"),
      ),
      (
          "BR Corner (Right + Bottom Edges)",
          "Right Edge (BR->TR)",
          "Bottom Edge (BL->BR)",
          "TL",
          ("BL", "BR", "TR"),
      ),
  ]

  valid_pairs = []
  for pair_name, e_vert, e_horiz, opposite_label, active_corners in corner_pairs:
    if e_vert in edge_angles and e_horiz in edge_angles:
      a_v = edge_angles[e_vert]
      a_h = edge_angles[e_horiz]
      ortho_err = abs(angle_diff_deg(a_v, a_h))
      mean_angle = 0.5 * (a_v + a_h)
      valid_pairs.append((
          ortho_err,
          mean_angle,
          pair_name,
          e_vert,
          e_horiz,
          opposite_label,
          active_corners,
      ))

  all_vals = list(edge_angles.values())
  spread = max(all_vals) - min(all_vals)

  if valid_pairs:
    valid_pairs.sort(key=lambda item: item[0])
    (
        best_err,
        best_mean,
        best_name,
        best_v,
        best_h,
        opp_lbl,
        active_corners,
    ) = valid_pairs[0]

    if len(edge_angles) == 4 and spread > 0.35 and best_err < 0.25:
      opp_fid = corners.get(opp_lbl)
      for f in fiducials:
        if f.corner_label in active_corners:
          f.used_for_rotation = True
          f.role_note = f"Used ({best_name.split()[0]} Orthogonal Edge)"
          used_indices.append(f.index)
        else:
          f.used_for_rotation = False
          f.role_note = "Excluded (Offset / Keying Fiducial)"

      if opp_fid is not None:
        if opp_lbl == "BR":
          exp_x = corners["BL"].x + (corners["TR"].x - corners["TL"].x)
          exp_y = corners["BL"].y + (corners["TR"].y - corners["TL"].y)
        elif opp_lbl == "TR":
          exp_x = corners["TL"].x + (corners["BR"].x - corners["BL"].x)
          exp_y = corners["TL"].y + (corners["BR"].y - corners["BL"].y)
        elif opp_lbl == "BL":
          exp_x = corners["BR"].x - (corners["TR"].x - corners["TL"].x)
          exp_y = corners["BR"].y - (corners["TR"].y - corners["TL"].y)
        else:
          exp_x = corners["TR"].x - (corners["BR"].x - corners["BL"].x)
          exp_y = corners["TR"].y - (corners["BR"].y - corners["BL"].y)

        off_x = opp_fid.x - exp_x
        off_y = opp_fid.y - exp_y
        outlier_note = (
            f"Fiducial {opp_fid.index} ({opp_lbl}) is offset by"
            f" ({off_x:+.2f} mm, {off_y:+.2f} mm) from a symmetric rectangle"
            " (anti-reversal keying mark or camera offset). Rotation computed"
            f" from orthogonal {best_name} (90 deg error: {best_err:.4f} deg)."
        )
      return (
          edge_angles,
          best_mean,
          f"Orthogonal Corner ({best_name})",
          used_indices,
          outlier_note,
      )

  for f in fiducials:
    f.used_for_rotation = True
    f.role_note = "Used (All-Edge Average)"
    used_indices.append(f.index)
  avg_angle = sum(all_vals) / len(all_vals)
  return (
      edge_angles,
      avg_angle,
      f"Average of {len(all_vals)} Fiducial Edges",
      used_indices,
      outlier_note,
  )


def update_fiducial_usage_for_mode(
    analysis: NeoDenCSVAnalysis, rot_mode: str
) -> None:
  """Updates each fiducial's used_for_rotation flag and role_note for the active UI mode."""
  if rot_mode == "auto":
    analyze_fiducials(analysis.fiducials)
  elif rot_mode == "avg":
    for f in analysis.fiducials:
      f.used_for_rotation = True
      f.role_note = "Used (All-Edge Average)"
  elif rot_mode in ("sq45", "rect2"):
    for idx, f in enumerate(analysis.fiducials):
      if idx < 2:
        f.used_for_rotation = True
        f.role_note = f"Used (Diagonal Pt {idx + 1})"
      else:
        f.used_for_rotation = False
        f.role_note = "Unused in 2-Fiducial Mode"
  elif rot_mode == "manual":
    for f in analysis.fiducials:
      f.used_for_rotation = False
      f.role_note = "Bypassed (Manual Angle Override)"


def parse_neoden_csv_bytes(
    raw_bytes: bytes,
    filepath: str = "uploaded.csv",
    cad_dx: Optional[float] = None,
    cad_dy: Optional[float] = None,
    exclude_non_smd: bool = True,
) -> NeoDenCSVAnalysis:
  """
  Parses raw bytes of either a NeoDen 4 Job CSV or a KiCad .pos CSV
  (strictly capped at 5 MB).
  """
  if len(raw_bytes) > MAX_CSV_BYTES:
    raise ValueError(
        f"File is too large ({len(raw_bytes) / (1024*1024):.1f} MB). Supported"
        " files are under 5 MB."
    )

  had_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
  if had_bom:
    raw_bytes = raw_bytes[3:]

  line_ending = "CRLF" if b"\r\n" in raw_bytes else "LF"
  text = raw_bytes.decode("utf-8", errors="replace")

  csv_type = detect_csv_type_from_head(text[:1024]) or "neoden"
  cad_fids: List[Fiducial] = []
  excluded_parts: List[str] = []

  if csv_type == "kicad_pos":
    raw_lines, cad_fids, excluded_parts = convert_kicad_pos_text_to_neoden_lines(
        text, exclude_non_smd=exclude_non_smd
    )
  else:
    raw_lines = text.splitlines()

  analysis = NeoDenCSVAnalysis(
      filepath=filepath,
      source_format=csv_type,
      raw_lines=[],
      had_bom=had_bom,
      line_ending=line_ending,
      cad_fiducials=cad_fids,
      excluded_parts=excluded_parts,
  )

  for idx, raw_line in enumerate(raw_lines):
    cleaned_line, was_modified = clean_excel_corrupted_line(raw_line)
    if was_modified and csv_type == "neoden":
      analysis.had_excel_padding = True
    analysis.raw_lines.append(cleaned_line)

    tokens = cleaned_line.split(",")
    prefix = tokens[0].strip() if tokens else ""

    if prefix == "stack" and len(tokens) > 1:
      analysis.feeder_ids.append(tokens[1].strip())
    elif prefix == "pcb" and len(tokens) > 2:
      analysis.pcb_mode = tokens[1].strip()
      analysis.pcb_feed_type = tokens[2].strip()
    elif prefix == "mark" and len(tokens) >= 5:
      analysis.mark_line_idx = idx
      analysis.mark_scope = tokens[1].strip()
      analysis.mark_mode = tokens[2].strip()
      coord_tokens = [t.strip() for t in tokens[3:] if t.strip() != ""]
      for fid_i in range(0, len(coord_tokens) - 1, 2):
        try:
          fx = float(coord_tokens[fid_i])
          fy = float(coord_tokens[fid_i + 1])
          analysis.fiducials.append(
              Fiducial(index=(fid_i // 2) + 1, x=fx, y=fy)
          )
        except ValueError:
          pass
    elif prefix == "mirror_create" and len(tokens) >= 10:
      analysis.mirror_create_line_idx = idx
      try:
        analysis.mirror_cols = int(tokens[1])
        analysis.mirror_rows = int(tokens[2])
        analysis.mirror_pcb_angle = float(tokens[9])
      except ValueError:
        pass
    elif prefix == "mirror" and len(tokens) >= 5:
      try:
        mx = float(tokens[1])
        my = float(tokens[2])
        mrot = float(tokens[3])
        mskip = tokens[4].strip()
        analysis.panels.append(
            PanelMirror(
                index=len(analysis.panels) + 1,
                line_idx=idx,
                x=mx,
                y=my,
                rotation=mrot,
                skip=mskip,
            )
        )
      except ValueError:
        pass
    elif prefix == "comp" and len(tokens) >= 10:
      try:
        cx = float(tokens[6])
        cy = float(tokens[7])
        crot = float(tokens[8])
        sk = tokens[9].strip()
        analysis.components.append(
            ComponentRow(
                line_idx=idx,
                feeder_id=tokens[1].strip(),
                nozzle=tokens[2].strip(),
                name=tokens[3].strip(),
                value=tokens[4].strip(),
                footprint=tokens[5].strip(),
                x=cx,
                y=cy,
                rotation=crot,
                skip=sk,
                orig_skip=sk,
                new_x=cx,
                new_y=cy,
                new_rotation=crot,
            )
        )
      except ValueError:
        pass

  (
      analysis.edge_angles,
      analysis.recommended_angle_deg,
      analysis.recommended_method,
      analysis.used_fiducial_indices,
      analysis.outlier_fiducial_note,
  ) = analyze_fiducials(analysis.fiducials, cad_dx=cad_dx, cad_dy=cad_dy)

  if csv_type == "kicad_pos":
    # In Stage 1 (KiCad .pos conversion), keep raw CAD coordinates (0 deg rotation)
    analysis.recommended_angle_deg = 0.0
    analysis.recommended_method = "Stage 1 KiCad Conversion (Raw CAD Coordinates)"
    analysis.warnings.append(
        f"Stage 1 (KiCad .pos -> Initial NeoDen CSV): Converted"
        f" {len(analysis.components)} SMD components, moved {len(cad_fids)}"
        " fiducial(s) to 'mark', and excluded"
        f" {len(excluded_parts)} non-SMD/THT row(s)."
    )
  else:
    if (
        analysis.had_bom
        or analysis.had_excel_padding
        or line_ending != "CRLF"
    ):
      analysis.warnings.append(
          "Formatting Notice: Detected non-standard CSV formatting (BOM, LF"
          " endings, or Excel extra commas); automatically repaired in memory."
      )

    if analysis.outlier_fiducial_note:
      analysis.warnings.append(
          f"Fiducial Geometry: {analysis.outlier_fiducial_note}"
      )

    if len(analysis.panels) >= 2:
      p1, p2 = analysis.panels[0], analysis.panels[1]
      dist = math.hypot(p2.x - p1.x, p2.y - p1.y)
      if dist < 5.0:
        analysis.warnings.append(
            f"PANEL 2 WARNING: Panel 1 ({p1.x:.2f}, {p1.y:.2f}) and"
            f" Panel 2 ({p2.x:.2f}, {p2.y:.2f}) are only {dist:.3f} mm apart!"
            " Update Panel 2 (Top) SMD1 X/Y in Section 2 if mounting 2 boards."
        )

    if analysis.components and analysis.panels:
      c1 = analysis.components[0]
      p1 = analysis.panels[0]
      if math.hypot(c1.x - p1.x, c1.y - p1.y) < 0.05:
        analysis.warnings.append(
            f"Re-Run Notice: 1st component {c1.name} ({c1.x:.2f}, {c1.y:.2f})"
            f" already matches Panel 1 mirror ({p1.x:.2f}, {p1.y:.2f})."
        )

    known_feeders = set(analysis.feeder_ids)
    missing_feeders = sorted(
        {
            c.feeder_id
            for c in analysis.components
            if c.feeder_id not in known_feeders
        },
        key=lambda x: int(x) if x.isdigit() else 999,
    )
    if missing_feeders:
      analysis.warnings.append(
          "Feeder Check: Components reference Feeder ID(s)"
          f" {', '.join(missing_feeders)} which are not defined in 'stack'."
      )

  return analysis


def parse_neoden_csv(
    filepath: str,
    cad_dx: Optional[float] = None,
    cad_dy: Optional[float] = None,
    exclude_non_smd: bool = True,
) -> NeoDenCSVAnalysis:
  st = os.stat(filepath)
  if st.st_size > MAX_CSV_BYTES:
    raise ValueError(
        f"File {os.path.basename(filepath)} is too large"
        f" ({st.st_size / (1024*1024):.1f} MB). Max supported size is 5 MB."
    )
  with open(filepath, "rb") as f:
    raw_bytes = f.read(MAX_CSV_BYTES + 1)
  return parse_neoden_csv_bytes(
      raw_bytes,
      filepath=filepath,
      cad_dx=cad_dx,
      cad_dy=cad_dy,
      exclude_non_smd=exclude_non_smd,
  )


# ---------------------------------------------------------------------------
# Batch Component Modification (# Selector Parser + Apply + Undo/Redo)
# ---------------------------------------------------------------------------


def parse_component_number_selector(
    selector_str: str, total_components: int
) -> List[int]:
  """
  Parses a 1-based component # selector string such as:
    '1-12, 15, 20-24' or '1 2 5' or 'all'
  and returns a sorted list of 0-based indices into analysis.components.
  """
  s = selector_str.strip().lower()
  if not s:
    return []
  if s in ("all", "*"):
    return list(range(total_components))

  matched: Set[int] = set()
  tokens = re.split(r"[,\s;]+", s)
  for tok in tokens:
    if not tok:
      continue
    if "-" in tok:
      parts = tok.split("-", 1)
      try:
        start_num = int(parts[0].strip())
        end_num = int(parts[1].strip())
        if start_num > end_num:
          start_num, end_num = end_num, start_num
        for num in range(start_num, end_num + 1):
          if 1 <= num <= total_components:
            matched.add(num - 1)
      except ValueError:
        continue
    else:
      try:
        num = int(tok)
        if 1 <= num <= total_components:
          matched.add(num - 1)
      except ValueError:
        continue

  return sorted(matched)


def apply_batch_component_edit(
    analysis: NeoDenCSVAnalysis,
    field_name: str,
    new_value: str,
    selector_str: str,
) -> Tuple[int, str]:
  """
  Applies a batch modification to 'feeder', 'footprint', or 'nozzle' across
  the selected 1-based component #s. Returns (count_modified, message).
  """
  val_clean = new_value.strip()
  if not val_clean:
    raise ValueError("Please enter a value/number to apply.")

  indices = parse_component_number_selector(
      selector_str, len(analysis.components)
  )
  if not indices:
    raise ValueError(
        f"No valid component #s matched '{selector_str}' (enter 1 to"
        f" {len(analysis.components)}, e.g. '1-12, 15' or 'all')."
    )

  f_key = field_name.strip().lower()
  for idx in indices:
    comp = analysis.components[idx]
    if f_key == "feeder":
      comp.feeder_id = val_clean
    elif f_key == "footprint":
      comp.footprint = val_clean
    elif f_key == "nozzle":
      comp.nozzle = val_clean
    else:
      raise ValueError(f"Unknown field '{field_name}'.")

  label_map = {
      "feeder": "Feeder ID",
      "footprint": "Footprint",
      "nozzle": "Nozzle",
  }
  return (
      len(indices),
      f"Updated {label_map.get(f_key, field_name)} to '{val_clean}' on"
      f" {len(indices)} component(s).",
  )


def apply_skip_mode(
    analysis: NeoDenCSVAnalysis,
    skip_mode: str,
    custom_skips: Optional[Dict[str, str]] = None,
) -> None:
  """Updates each component's .skip attribute according to skip_mode."""
  for idx, comp in enumerate(analysis.components):
    if skip_mode == "all_no":
      comp.skip = "No"
    elif skip_mode == "all_yes":
      comp.skip = "Yes"
    elif skip_mode == "first_no":
      comp.skip = "No" if idx == 0 else "Yes"
    elif skip_mode == "original":
      comp.skip = comp.orig_skip

    if custom_skips and comp.name in custom_skips:
      comp.skip = "Yes" if custom_skips[comp.name] == "Yes" else "No"


def apply_transformation(
    analysis: NeoDenCSVAnalysis,
    board_angle_deg: float,
    panel1_x: Optional[float] = None,
    panel1_y: Optional[float] = None,
    panel2_x: Optional[float] = None,
    panel2_y: Optional[float] = None,
    rotate_component_angles: bool = True,
    reset_mirror_create_angle: bool = True,
    skip_mode: str = "custom",
    custom_skips: Optional[Dict[str, str]] = None,
) -> List[str]:
  """
  Applies the SMD1 origin replacement, board rotation angle, and skip settings
  to all components, updates mirror / mirror_create, and returns the exact
  NeoDen 4 CSV lines ready to be joined with \\r\\n.
  """
  if not analysis.components:
    raise ValueError("No 'comp' rows found in the file.")

  if skip_mode != "custom" or custom_skips:
    apply_skip_mode(analysis, skip_mode, custom_skips)

  # Stage 1 KiCad .pos export: keep raw CAD coordinates without shifting/rotating
  if (
      analysis.source_format == "kicad_pos"
      and panel1_x is None
      and panel1_y is None
      and math.isclose(board_angle_deg, 0.0, abs_tol=1e-7)
  ):
    out_lines = list(analysis.raw_lines)
    for comp in analysis.components:
      comp.new_x = comp.x
      comp.new_y = comp.y
      comp.new_rotation = wrap_neoden_angle(comp.rotation)
      out_lines[comp.line_idx] = (
          f"comp,{comp.feeder_id},{comp.nozzle},{comp.name},{comp.value},"
          f"{comp.footprint},{comp.new_x:.2f},{comp.new_y:.2f},"
          f"{comp.new_rotation:.2f},{comp.skip},"
      )
    return out_lines

  if not analysis.panels and (panel1_x is None or panel1_y is None):
    raise ValueError(
        "No 'mirror' rows found and no Panel 1 (X, Y) override provided."
    )

  target_p1_x = panel1_x if panel1_x is not None else analysis.panels[0].x
  target_p1_y = panel1_y if panel1_y is not None else analysis.panels[0].y

  c1_orig_x = analysis.components[0].x
  c1_orig_y = analysis.components[0].y

  theta_rad = math.radians(board_angle_deg)
  cos_t = math.cos(theta_rad)
  sin_t = math.sin(theta_rad)

  out_lines = list(analysis.raw_lines)

  p2_x_val = analysis.panels[1].x if len(analysis.panels) >= 2 else 0.0
  p2_y_val = analysis.panels[1].y if len(analysis.panels) >= 2 else 0.0

  if analysis.panels:
    p1 = analysis.panels[0]
    out_lines[p1.line_idx] = (
        f"mirror,{target_p1_x:.2f},{target_p1_y:.2f},{p1.rotation:g},{p1.skip},"
    )

    if len(analysis.panels) >= 2:
      p2 = analysis.panels[1]
      if panel2_x is not None:
        p2_x_val = panel2_x
      if panel2_y is not None:
        p2_y_val = panel2_y
      out_lines[p2.line_idx] = (
          f"mirror,{p2_x_val:.2f},{p2_y_val:.2f},{p2.rotation:g},{p2.skip},"
      )

  if analysis.mirror_create_line_idx >= 0:
    mc_tokens = out_lines[analysis.mirror_create_line_idx].split(",")
    if len(mc_tokens) >= 10:
      mc_tokens[3] = f"{target_p1_x:.2f}"
      mc_tokens[4] = f"{target_p1_y:.2f}"
      if len(analysis.panels) >= 2:
        mc_tokens[5] = f"{p2_x_val:.2f}"
        mc_tokens[6] = f"{p2_y_val:.2f}"
      if reset_mirror_create_angle:
        mc_tokens[9] = "0"
      out_lines[analysis.mirror_create_line_idx] = (
          ",".join(mc_tokens[:10]) + ","
      )

  for idx, comp in enumerate(analysis.components):
    if idx == 0:
      comp.new_x = target_p1_x
      comp.new_y = target_p1_y
    else:
      dx = comp.x - c1_orig_x
      dy = comp.y - c1_orig_y
      comp.new_x = target_p1_x + (dx * cos_t - dy * sin_t)
      comp.new_y = target_p1_y + (dx * sin_t + dy * cos_t)

    if rotate_component_angles:
      comp.new_rotation = wrap_neoden_angle(comp.rotation + board_angle_deg)
    else:
      comp.new_rotation = wrap_neoden_angle(comp.rotation)

    out_lines[comp.line_idx] = (
        f"comp,{comp.feeder_id},{comp.nozzle},{comp.name},{comp.value},"
        f"{comp.footprint},{comp.new_x:.2f},{comp.new_y:.2f},"
        f"{comp.new_rotation:.2f},{comp.skip},"
    )

  return out_lines


def save_neoden_csv(filepath: str, lines: List[str]) -> None:
  """
  Writes lines in binary mode ('wb') with explicit Windows CRLF (\\r\\n) and no
  UTF-8 BOM. Binary mode prevents Windows from translating \\r\\n into \\r\\r\\n.
  """
  content = "\r\n".join(lines) + "\r\n"
  with open(filepath, "wb") as f:
    f.write(content.encode("ascii", errors="replace"))


# ---------------------------------------------------------------------------
# Dedicated Help Page HTML (/help — Opens in a New Tab)
# ---------------------------------------------------------------------------

HELP_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Help &amp; Workflow Guide — NeoDen 4 CSV Utility v5.0</title>
<style>
  :root { color-scheme: light; }
  * {
    box-sizing: border-box;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  body {
    margin: 0;
    padding: 28px 16px 48px;
    background: #f1f5f9;
    color: #0f172a;
    line-height: 1.55;
  }
  .container {
    max-width: 900px;
    margin: 0 auto;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
    overflow: hidden;
  }
  .header {
    background: linear-gradient(180deg, #1e3a8a 0%, #1d4ed8 100%);
    color: #ffffff;
    padding: 20px 26px;
  }
  .header h1 {
    margin: 0 0 6px 0;
    font-size: 20px;
  }
  .header p {
    margin: 0;
    font-size: 13.5px;
    opacity: 0.92;
  }
  .body {
    padding: 22px 26px;
  }
  h2 {
    font-size: 16px;
    color: #1e3a8a;
    border-bottom: 2px solid #e2e8f0;
    padding-bottom: 6px;
    margin-top: 24px;
    margin-bottom: 10px;
  }
  h2:first-child {
    margin-top: 0;
  }
  .step-card {
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-left: 4px solid #2563eb;
    border-radius: 5px;
    padding: 12px 16px;
    margin-bottom: 12px;
  }
  .step-card h3 {
    margin: 0 0 6px 0;
    font-size: 14px;
    color: #0f172a;
  }
  .step-card p, .step-card ul {
    margin: 4px 0;
    font-size: 13px;
    color: #334155;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    margin: 10px 0 16px;
  }
  th, td {
    border: 1px solid #cbd5e1;
    padding: 8px 10px;
    text-align: left;
    vertical-align: top;
  }
  th {
    background: #f1f5f9;
    font-weight: 700;
    color: #1e293b;
  }
  code {
    background: #e2e8f0;
    padding: 1px 5px;
    border-radius: 3px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px;
  }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>NeoDen 4 CSV &amp; KiCad .pos Utility (v5.0) — Help &amp; User Guide</h1>
    <p>Complete reference for the two-stage KiCad-to-NeoDen workflow, fiducial rotation math, manual SMD1 tweaking, and batch component editing.</p>
  </div>
  <div class="body">
    <h2>1. Standard Two-Stage Workflow (KiCad .pos &rarr; NeoDen 4 Machine &rarr; Final Aligned CSV)</h2>
    <div class="step-card">
      <h3>Stage 1: Ingest KiCad <code>.pos</code> CSV &amp; Convert for the Machine</h3>
      <ul>
        <li>Click <strong>Choose File / Browse</strong> in Section 1 and select the <code>.pos</code> CSV exported from KiCad (<code>File &rarr; Fabrication Outputs &rarr; Component Placement (.pos)</code> with Format=CSV, Units=mm).</li>
        <li>To prevent confusion before machine alignment, <strong>all coordinate/rotation settings are automatically hidden</strong> when a KiCad <code>.pos</code> file is loaded.</li>
        <li>The tool strips out <code>FID*</code> fiducial rows from the component list, filters out through-hole/non-SMD parts (<code>DIP-*</code>, <code>SW*</code>, <code>BATTERY*</code>), converts verbose KiCad footprint names into NeoDen codes (<code>0805</code>, <code>0603</code>, <code>SOT23</code>, <code>SOP24</code>, etc.), and keeps your raw KiCad CAD coordinates intact.</li>
        <li>Click <strong>CONVERT &amp; EXPORT INITIAL NEODEN 4 CSV</strong> and take that file to the NeoDen 4 via USB.</li>
      </ul>
    </div>
    <div class="step-card">
      <h3>On the NeoDen 4 Machine: Record Physical Fiducials &amp; Panel SMD1</h3>
      <ul>
        <li>Import the Stage 1 CSV on the NeoDen 4 and open <strong>Edit &rarr; PCB Information</strong>.</li>
        <li>Under <strong>Panelized PCB Origin (SMD1 Coordinates)</strong>, click <strong>Align</strong> on <code>Left Bottom</code> (Panel 1) and center the camera on the 1st component (<code>C1</code>) on the bottom board. If placing 2 boards, click <strong>Align</strong> on <code>Left top</code> (Panel 2) and center the camera on <code>C1</code> on the <strong>top board</strong>, then click <strong>Create panelized list</strong>.</li>
        <li>Under <strong>Fiducial Settings</strong>, align the camera to your board's physical fiducial marks (2, 3, or 4 fiducials).</li>
        <li>Save and export the updated CSV from the NeoDen 4 back to your USB drive.</li>
      </ul>
    </div>
    <div class="step-card">
      <h3>Stage 2: Ingest the NeoDen 4 CSV &amp; Generate the Final Aligned File</h3>
      <ul>
        <li>Browse and select the NeoDen 4 CSV in Section 1. All alignment, fiducial rotation, skip, and batch-edit panels will now appear on the left.</li>
        <li>The program replaces the 1st component's CAD coordinates with <code>Panel 1 / SMD1</code> (from <code>mirror</code>), calculates the exact board rotation angle from your physical fiducials (from <code>mark</code>), and rotates all remaining components around <code>SMD1</code>.</li>
        <li>Review or tweak any settings on the left, verify the live table on the right, and click <strong>START / EXPORT NEODEN 4 CSV</strong>.</li>
      </ul>
    </div>

    <h2>2. Guide to Each Control Section</h2>
    <table>
      <thead>
        <tr>
          <th style="width:26%;">Section / Control</th>
          <th>What It Does &amp; How to Use It</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>1. Input File</strong></td>
          <td>Click <strong>Choose File</strong> to load either a KiCad <code>.pos</code> CSV (Stage 1) or a NeoDen 4 Job CSV (Stage 2).</td>
        </tr>
        <tr>
          <td><strong>2. Manual Panel 1 / SMD1 &amp; Panel 2 Tweaking</strong></td>
          <td>
            <ul>
              <li><strong>Panel 1 / SMD1 X &amp; Y (mm)</strong>: Sets the physical machine coordinate of the 1st component (<code>C1</code>) on Board 1. Use the textboxes or the <code>-0.1</code> / <code>+0.1</code> mm nudge buttons to shift the entire board placement.</li>
              <li><strong>Panel 2 (Top) SMD1 X &amp; Y (mm)</strong>: Sets the physical machine coordinate of the 1st component on Board 2 (for 2-board panels). Make sure Panel 2 is set to the top board's <code>C1</code> location so Board 2 does not place on top of Board 1.</li>
              <li><strong>Reset <code>mirror_create</code> PCB Angle to 0&deg; (Default)</strong>: Clears bogus angles (like <code>-26.57&deg;</code>) written by the NeoDen 4 when Panel 1 and Panel 2 were accidentally aligned to the same point.</li>
            </ul>
          </td>
        </tr>
        <tr>
          <td><strong>3. Fiducial Rotation &amp; Global Skip Toggles</strong></td>
          <td>
            <ul>
              <li><strong>Fiducials Used for Rotation List</strong>: Displays every fiducial from the <code>mark</code> row in non-editable <code>X</code> and <code>Y</code> textboxes alongside a status badge (<strong>&#10003; Used for Rotation</strong> vs. <strong>&#10007; Excluded</strong>).</li>
              <li><strong>Rotation Method</strong>: Defaults to <strong>Auto &mdash; Orthogonal Corner / Outlier Rejection (Default)</strong>, which finds the true 90&deg; corner among 3 or 4 fiducials and automatically rejects inset polarization/keying fiducials.</li>
              <li><strong>Board Angle (&deg;CCW)</strong>: Locked (greyed out) when using automatic fiducial calculation so it cannot be accidentally overwritten. Select <strong>Manual Angle Override (&deg;)</strong> in the dropdown to unlock and type a custom angle.</li>
              <li><strong>Add board rotation to component Rotation column (Default)</strong>: Rotates each component's nozzle placement angle by the board tilt and wraps it within NeoDen 4's strict <code>[-180&deg;, +180&deg;]</code> range.</li>
              <li><strong>Toggle All Skips</strong>: One-click buttons to set <strong>All Skips OFF (No)</strong> (place entire board), <strong>All Skips ON (Yes)</strong> (skip entire board), <strong>1st Only (C1=No)</strong>, or <strong>File Default</strong>. You can also click any individual row's <code>Yes</code>/<code>No</code> badge in the table on the right.</li>
            </ul>
          </td>
        </tr>
        <tr>
          <td><strong>4. Batch Modify Components (with Undo &amp; Redo)</strong></td>
          <td>
            <ul>
              <li><strong>Field Dropdown</strong>: Choose <code>Feeder</code>, <code>Footprint</code>, or <code>Nozzle</code>.</li>
              <li><strong>Value / Number Textbox</strong>: Enter the new Feeder ID (e.g. <code>6</code>), Footprint name (e.g. <code>0805</code>), or Nozzle number (<code>1</code>&ndash;<code>4</code>).</li>
              <li><strong>Component #s Textbox</strong>: Enter the row number(s) from the <code>#</code> column of the table on the right. Supports single numbers (<code>5</code>), comma-separated lists (<code>1, 4, 9</code>), ranges (<code>1-12, 25-36</code>), or <code>all</code>.</li>
              <li><strong>Apply / Undo / Redo</strong>: Click <strong>Apply</strong> to update all matching rows immediately. Use <strong>Undo</strong> and <strong>Redo</strong> to step backward or forward through your edits.</li>
            </ul>
          </td>
        </tr>
        <tr>
          <td><strong>Pre-Flight Diagnostics Dropdown</strong></td>
          <td>Minimized by default at the bottom of the left column. Click its header bar to expand edge angle calculations, keying fiducial offsets, and pre-flight machine checks.</td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main Rufus-Style 2-Column Web GUI HTML (v5.0)
# ---------------------------------------------------------------------------

RUFUS_WEB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NeoDen 4 CSV &amp; KiCad .pos Utility v5.0</title>
<style>
  :root {
    color-scheme: light;
  }
  * {
    box-sizing: border-box;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  body {
    margin: 0;
    padding: 12px;
    background: #dde3ea;
    color: #0f172a;
  }
  .app-shell {
    width: 100%;
    max-width: 1560px;
    margin: 0 auto;
    background: #f8fafc;
    border: 1px solid #94a3b8;
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
    overflow: hidden;
  }
  .titlebar {
    background: linear-gradient(180deg, #1e3a8a 0%, #1d4ed8 100%);
    color: #ffffff;
    padding: 9px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .titlebar h1 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.2px;
  }
  .titlebar-right {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .help-box {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: #ffffff;
    color: #1e3a8a;
    border: 1px solid #bfdbfe;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 700;
    text-decoration: none;
    cursor: pointer;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
  }
  .help-box:hover {
    background: #eff6ff;
  }
  .badge {
    font-size: 11px;
    background: rgba(255, 255, 255, 0.2);
    padding: 3px 9px;
    border-radius: 999px;
  }

  /* Responsive Split Layout: Left Settings + Right Full Table on non-mobile */
  .workspace {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
  }
  @media (min-width: 920px) {
    .workspace {
      display: grid;
      grid-template-columns: 490px minmax(0, 1fr);
      gap: 14px;
      align-items: start;
    }
  }

  .left-pane {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
  }
  .right-pane {
    display: flex;
    flex-direction: column;
    min-width: 0;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    padding: 10px;
  }

  fieldset {
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #ffffff;
    margin: 0;
    padding: 9px 11px 11px;
    min-width: 0;
  }
  legend {
    font-size: 12px;
    font-weight: 700;
    color: #1e293b;
    padding: 0 6px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 6px;
    flex-wrap: wrap;
  }
  .row:last-child {
    margin-bottom: 0;
  }
  label {
    font-size: 12px;
    color: #334155;
  }
  input[type="text"], input[type="number"], select {
    background: #ffffff;
    color: #0f172a;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    padding: 4px 6px;
    font-size: 12px;
  }
  input[type="number"] {
    width: 82px;
    font-variant-numeric: tabular-nums;
  }
  input[readonly], input:disabled {
    background: #e2e8f0 !important;
    color: #475569 !important;
    border-color: #cbd5e1 !important;
    cursor: not-allowed;
  }
  select {
    flex: 1;
    min-width: 140px;
  }
  button {
    background: #e2e8f0;
    color: #0f172a;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    padding: 4px 9px;
    font-size: 11.5px;
    font-weight: 600;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    background: #cbd5e1;
  }
  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  button.nudge {
    padding: 3px 6px;
    font-size: 11px;
    background: #f1f5f9;
  }
  button.skip-btn {
    padding: 4px 8px;
    font-size: 11px;
  }
  button.skip-btn.active-no {
    background: #dcfce7;
    border-color: #16a34a;
    color: #14532d;
  }
  button.skip-btn.active-yes {
    background: #fee2e2;
    border-color: #dc2626;
    color: #7f1d1d;
  }
  button.primary {
    background: #1d4ed8;
    color: #ffffff;
    border-color: #1e40af;
    padding: 8px 16px;
    font-size: 12.5px;
  }
  button.primary:hover:not(:disabled) {
    background: #1e40af;
  }

  .smd1-banner {
    font-size: 11.5px;
    font-weight: 600;
    color: #0f172a;
    background: #f1f5f9;
    border-left: 3px solid #2563eb;
    padding: 5px 8px;
    margin-bottom: 7px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
  }
  .format-tag {
    font-size: 10.5px;
    padding: 2px 6px;
    border-radius: 3px;
    background: #dbeafe;
    color: #1e40af;
    font-weight: 700;
    white-space: nowrap;
  }
  .check-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 4px;
    font-size: 11.5px;
  }
  .default-tag {
    font-size: 10.5px;
    color: #1d4ed8;
    font-weight: 700;
  }

  /* Fiducial Non-Editable List in Section 3 */
  .fid-list-box {
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    padding: 6px 8px;
    margin-bottom: 8px;
  }
  .fid-list-title {
    font-size: 11px;
    font-weight: 700;
    color: #334155;
    margin-bottom: 5px;
  }
  .fid-row {
    display: flex;
    align-items: center;
    gap: 5px;
    margin-bottom: 4px;
    font-size: 11px;
  }
  .fid-row:last-child {
    margin-bottom: 0;
  }
  .fid-lbl {
    width: 68px;
    font-weight: 700;
    color: #1e293b;
  }
  .fid-coord {
    width: 72px !important;
    text-align: right;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 11px !important;
    padding: 2px 5px !important;
  }
  .fid-badge-used {
    background: #dcfce7;
    color: #166534;
    border: 1px solid #86efac;
    border-radius: 3px;
    padding: 1px 6px;
    font-size: 10.5px;
    font-weight: 700;
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .fid-badge-unused {
    background: #fef3c7;
    color: #92400e;
    border: 1px solid #fde68a;
    border-radius: 3px;
    padding: 1px 6px;
    font-size: 10.5px;
    font-weight: 600;
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* KiCad Stage 1 Notice Card */
  .kicad-stage1-card {
    background: #eff6ff;
    border: 1px solid #93c5fd;
    border-left: 4px solid #1d4ed8;
    border-radius: 5px;
    padding: 10px 12px;
    font-size: 12px;
    color: #1e3a8a;
    display: none;
  }
  .kicad-stage1-card h3 {
    margin: 0 0 5px 0;
    font-size: 13px;
    color: #1e40af;
  }
  .kicad-stage1-card ol {
    margin: 6px 0 0 18px;
    padding: 0;
  }
  .kicad-stage1-card li {
    margin-bottom: 4px;
  }

  /* Minimized Diagnostics Dropdown */
  details.diag-dropdown {
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #ffffff;
    padding: 6px 10px;
  }
  details.diag-dropdown summary {
    font-size: 12px;
    font-weight: 700;
    color: #334155;
    cursor: pointer;
    user-select: none;
  }
  .diag-box {
    background: #0f172a;
    color: #e2e8f0;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 11px;
    line-height: 1.45;
    padding: 8px 10px;
    border-radius: 4px;
    max-height: 150px;
    overflow-y: auto;
    white-space: pre-wrap;
    margin-top: 7px;
  }

  /* Right Pane Table Styling — Fixed/Constrained widths so nothing overflows */
  .right-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 7px;
    gap: 8px;
    flex-wrap: wrap;
  }
  .right-header h2 {
    margin: 0;
    font-size: 13px;
    font-weight: 700;
    color: #1e293b;
  }
  .right-header span {
    font-size: 11.5px;
    color: #475569;
  }
  .table-wrap {
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    max-height: calc(100vh - 125px);
    min-height: 360px;
    overflow-y: auto;
    overflow-x: auto;
    background: #ffffff;
  }
  table {
    width: 100%;
    table-layout: fixed;
    border-collapse: collapse;
    font-size: 11.5px;
  }
  th {
    position: sticky;
    top: 0;
    background: #f1f5f9;
    color: #1e293b;
    border-bottom: 1px solid #cbd5e1;
    padding: 6px 4px;
    text-align: center;
    font-weight: 700;
    z-index: 1;
  }
  td {
    border-bottom: 1px solid #f1f5f9;
    padding: 4px 4px;
    text-align: center;
    color: #1e293b;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  tr:nth-child(even) td {
    background: #f8fafc;
  }
  .highlight-new {
    color: #1d4ed8;
    font-weight: 600;
  }
  .row-skip-pill {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 3px;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
    user-select: none;
  }
  .row-skip-no {
    background: #dcfce7;
    color: #15803d;
    border: 1px solid #86efac;
  }
  .row-skip-yes {
    background: #fee2e2;
    color: #b91c1c;
    border: 1px solid #fca5a5;
  }
  .footer-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding-top: 2px;
  }
  .status-pill {
    font-size: 11.5px;
    color: #334155;
    background: #e2e8f0;
    border: 1px solid #cbd5e1;
    border-radius: 3px;
    padding: 6px 9px;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
</head>
<body>
<div class="app-shell">
  <div class="titlebar">
    <h1>NeoDen 4 CSV &amp; KiCad .pos Utility</h1>
    <div class="titlebar-right">
      <a href="/help" target="_blank" rel="noopener" class="help-box" title="Opens a detailed guide in a new tab">
        &#128214; Help &amp; Workflow Guide (Opens in New Tab)
      </a>
      <span class="badge">v5.0</span>
    </div>
  </div>

  <div class="workspace">
    <!-- LEFT COLUMN: All Settings & Controls -->
    <div class="left-pane">
      <!-- Section 1: Browse File Only -->
      <fieldset>
        <legend>1. Browse Input File (NeoDen 4 .csv OR KiCad .pos .csv)</legend>
        <div class="row">
          <label for="fileInput" style="font-weight:600;">Browse File:</label>
          <input type="file" id="fileInput" accept=".csv,.pos,text/csv" style="font-size:12px;flex:1;">
        </div>
      </fieldset>

      <!-- KiCad Stage 1 Banner (Shown ONLY when a KiCad .pos file is loaded) -->
      <div id="kicadStage1Card" class="kicad-stage1-card">
        <h3>KiCad .pos File Ingested &mdash; Stage 1: Convert for NeoDen 4 Machine</h3>
        <div>Alignment settings are hidden until you load a NeoDen 4 CSV. Follow these steps:</div>
        <ol>
          <li>Click <strong>CONVERT &amp; EXPORT INITIAL NEODEN 4 CSV</strong> below to download the NeoDen-formatted file.</li>
          <li>Load that file onto your <strong>NeoDen 4</strong> and align your physical <strong>Fiducials</strong> and <strong>SMD1 (Panel 1 / Panel 2)</strong> coordinates with the machine camera.</li>
          <li>Export the updated CSV from the NeoDen 4 and load it back here in <strong>Browse File</strong> above to unlock all Stage 2 alignment &amp; rotation settings.</li>
        </ol>
      </div>

      <!-- NeoDen Settings Wrapper (Hidden when KiCad .pos is ingested; shown when NeoDen CSV is ingested) -->
      <div id="neodenSettingsContainer" style="display:none;flex-direction:column;gap:10px;">
        <!-- Section 2: Manual Panel 1 / SMD1 & Panel 2 Position Tweaking -->
        <fieldset>
          <legend>2. Manual Panel 1 / SMD1 &amp; Panel 2 Position Tweaking (mirror)</legend>
          <div class="smd1-banner">
            <span id="cadSmd1Banner">1st Component (SMD1) CAD: &mdash;</span>
            <span id="sourceFormatTag" class="format-tag">NeoDen 4 CSV</span>
          </div>
          <div class="row">
            <label style="width:138px;font-weight:600;">Panel 1 / SMD1 X:</label>
            <input type="number" step="0.01" id="p1x" onchange="recalculate()">
            <button type="button" class="nudge" onclick="nudge('p1x', -0.1)">-0.1</button>
            <button type="button" class="nudge" onclick="nudge('p1x', 0.1)">+0.1</button>
            <label style="margin-left:4px;font-weight:600;">Y:</label>
            <input type="number" step="0.01" id="p1y" onchange="recalculate()">
            <button type="button" class="nudge" onclick="nudge('p1y', -0.1)">-0.1</button>
            <button type="button" class="nudge" onclick="nudge('p1y', 0.1)">+0.1</button>
          </div>
          <div class="row">
            <label style="width:138px;">Panel 2 (Top) SMD1 X:</label>
            <input type="number" step="0.01" id="p2x" onchange="recalculate()">
            <button type="button" class="nudge" onclick="nudge('p2x', -0.1)">-0.1</button>
            <button type="button" class="nudge" onclick="nudge('p2x', 0.1)">+0.1</button>
            <label style="margin-left:4px;">Y:</label>
            <input type="number" step="0.01" id="p2y" onchange="recalculate()">
            <button type="button" class="nudge" onclick="nudge('p2y', -0.1)">-0.1</button>
            <button type="button" class="nudge" onclick="nudge('p2y', 0.1)">+0.1</button>
          </div>
          <div class="check-row">
            <input type="checkbox" id="resetMcAngle" checked onchange="recalculate()">
            <label for="resetMcAngle">
              Reset <code>mirror_create</code> Bogus PCB Angle to 0&deg;
              <span class="default-tag">(Default)</span>
            </label>
          </div>
        </fieldset>

        <!-- Section 3: Fiducial Rotation & Global Skip Toggles -->
        <fieldset>
          <legend>3. Fiducial Rotation &amp; Global Skip Toggles (mark)</legend>
          <!-- Non-editable Fiducial X & Y boxes + Active Rotation status badges -->
          <div class="fid-list-box">
            <div class="fid-list-title">Fiducials in File &amp; Active Rotation Status:</div>
            <div id="fidListContainer">
              <div style="font-size:11px;color:#64748b;">No fiducials loaded.</div>
            </div>
          </div>

          <div class="row">
            <label for="rotMode" style="width:110px;font-weight:600;">Rotation Method:</label>
            <select id="rotMode" onchange="onModeChange()">
              <option value="auto">Auto &mdash; Orthogonal Corner / Outlier Rejection (Default)</option>
              <option value="avg">Average of All Fiducial Edges</option>
              <option value="sq45">2-Fiducial Square Diagonal (45&deg;)</option>
              <option value="rect2">2-Fiducial Rectangular Diagonal (Use CAD &Delta;X, &Delta;Y)</option>
              <option value="manual">Manual Angle Override (&deg;)</option>
            </select>
          </div>
          <div class="row">
            <label for="boardAngle" style="width:110px;">Board Angle (&deg;CCW):</label>
            <input type="number" step="0.0001" id="boardAngle" readonly disabled onchange="recalculate()" title="Locked in Auto mode. Select 'Manual Angle Override' above to edit manually.">
            <label style="margin-left:4px;">2-Fid CAD &Delta;X, &Delta;Y:</label>
            <input type="number" step="0.01" id="cadDx" placeholder="&Delta;X" style="width:60px;" onchange="onModeChange()">
            <input type="number" step="0.01" id="cadDy" placeholder="&Delta;Y" style="width:60px;" onchange="onModeChange()">
          </div>
          <div class="check-row">
            <input type="checkbox" id="rotComp" checked onchange="recalculate()">
            <label for="rotComp">
              Add board rotation to component <code>Rotation</code> column
              <span class="default-tag">(Default)</span>
            </label>
          </div>
          <div class="row" style="margin-top:6px;padding-top:6px;border-top:1px dashed #cbd5e1;">
            <label style="width:95px;font-weight:700;color:#0f172a;">Toggle Skips:</label>
            <button type="button" class="skip-btn active-no" onclick="setSkipMode('all_no')">All Skips OFF (No)</button>
            <button type="button" class="skip-btn active-yes" onclick="setSkipMode('all_yes')">All Skips ON (Yes)</button>
            <button type="button" class="skip-btn" onclick="setSkipMode('first_no')">1st Only</button>
            <button type="button" class="skip-btn" onclick="setSkipMode('original')">Default</button>
          </div>
        </fieldset>

        <!-- Section 4: Batch Modify (Feeder, Footprint, Nozzle) with # Selector & Undo/Redo -->
        <fieldset>
          <legend>4. Batch Modify Components (Feeder / Footprint / Nozzle)</legend>
          <div class="row">
            <label for="batchField" style="width:55px;font-weight:600;">Modify:</label>
            <select id="batchField" style="max-width:130px;">
              <option value="feeder">Feeder</option>
              <option value="footprint">Footprint</option>
              <option value="nozzle">Nozzle</option>
            </select>
            <label for="batchValue" style="font-weight:600;">New Value/#:</label>
            <input type="text" id="batchValue" placeholder="e.g. 6 or 0805" style="width:110px;">
          </div>
          <div class="row">
            <label for="batchSelector" style="width:95px;font-weight:600;">Component #s:</label>
            <input type="text" id="batchSelector" placeholder="Put # of components (e.g. 1-12, 15 or all)" style="flex:1;">
          </div>
          <div class="row" style="justify-content:space-between;margin-top:4px;">
            <span id="batchFeedback" style="font-size:11px;color:#1d4ed8;font-weight:600;">Enter row #s from the table on the right.</span>
            <div style="display:flex;gap:5px;">
              <button type="button" onclick="applyBatchEdit()" style="background:#dbeafe;border-color:#3b82f6;color:#1e3a8a;">Apply</button>
              <button type="button" id="undoBtn" onclick="undoEdit()" disabled>&#8630; Undo</button>
              <button type="button" id="redoBtn" onclick="redoEdit()" disabled>&#8631; Redo</button>
            </div>
          </div>
        </fieldset>
      </div>

      <!-- Minimized by Default Pre-Flight Diagnostics Dropdown -->
      <details class="diag-dropdown">
        <summary>Pre-Flight Diagnostics &amp; Fiducial Math Log (Click to Expand)</summary>
        <div id="diagBox" class="diag-box">Browse and select a NeoDen 4 CSV or KiCad .pos CSV file to begin.</div>
      </details>

      <!-- Action / Export Bar -->
      <div class="footer-bar">
        <div id="statusBar" class="status-pill">No file loaded &mdash; Browse a file in Section 1.</div>
        <button type="button" id="exportBtn" class="primary" onclick="exportCSV()" disabled>START / EXPORT CSV</button>
      </div>
    </div>

    <!-- RIGHT COLUMN: Full Component Preview Table -->
    <div class="right-pane">
      <div class="right-header">
        <h2>Component List Preview</h2>
        <span id="tableSubHeader">Click any row's Skip badge to toggle Yes/No</span>
      </div>
      <div class="table-wrap">
        <table>
          <colgroup>
            <col style="width:36px;">
            <col style="width:58px;">
            <col style="width:52px;">
            <col style="width:42px;">
            <col style="width:110px;">
            <col style="width:74px;">
            <col style="width:64px;">
            <col style="width:64px;">
            <col style="width:68px;">
            <col style="width:68px;">
            <col style="width:68px;">
            <col style="width:56px;">
          </colgroup>
          <thead>
            <tr>
              <th>#</th>
              <th>Name</th>
              <th>Feeder</th>
              <th>Noz</th>
              <th>Value</th>
              <th>Footprint</th>
              <th>CAD X</th>
              <th>CAD Y</th>
              <th>New X</th>
              <th>New Y</th>
              <th>New Rot</th>
              <th>Skip</th>
            </tr>
          </thead>
          <tbody id="compTableBody">
            <tr><td colspan="12" style="padding:28px;color:#64748b;">Browse a NeoDen 4 <code>.csv</code> or KiCad <code>.pos</code> file on the left to view components.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<script>
let currentData = null;

function nudge(inputId, delta) {
  const el = document.getElementById(inputId);
  if (el.disabled) return;
  const cur = parseFloat(el.value) || 0.0;
  el.value = (cur + delta).toFixed(2);
  recalculate();
}

document.getElementById('fileInput').addEventListener('change', async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    alert('File is larger than 5 MB. Please select a valid NeoDen 4 or KiCad .pos CSV file.');
    return;
  }
  const buf = await file.arrayBuffer();
  const res = await fetch('/api/upload?filename=' + encodeURIComponent(file.name), {
    method: 'POST',
    body: buf
  });
  const payload = await res.json();
  applyLoadedState(payload, true);
});

function applyLoadedState(payload, resetInputs) {
  currentData = payload;
  if (payload.error) {
    document.getElementById('statusBar').textContent = 'Error: ' + payload.error;
    return;
  }

  const isKicad = (payload.source_format === 'kicad_pos');
  const settingsBox = document.getElementById('neodenSettingsContainer');
  const stage1Card = document.getElementById('kicadStage1Card');
  const exportBtn = document.getElementById('exportBtn');
  exportBtn.disabled = false;

  if (isKicad) {
    // Remove all settings from view when ingesting a KiCad .pos file to prevent confusion
    settingsBox.style.display = 'none';
    stage1Card.style.display = 'block';
    exportBtn.textContent = 'CONVERT & EXPORT INITIAL NEODEN 4 CSV';
  } else {
    settingsBox.style.display = 'flex';
    stage1Card.style.display = 'none';
    exportBtn.textContent = 'START / EXPORT NEODEN 4 CSV';
  }

  document.getElementById('cadSmd1Banner').textContent =
    `1st Component (${payload.c1_name} / ${payload.c1_footprint}) CAD: (${payload.c1_x.toFixed(2)}, ${payload.c1_y.toFixed(2)}) mm`;
  document.getElementById('sourceFormatTag').textContent =
    isKicad ? 'KiCad .pos (Stage 1)' : 'NeoDen 4 CSV (Stage 2)';

  if (resetInputs) {
    document.getElementById('p1x').value = payload.p1_x.toFixed(2);
    document.getElementById('p1y').value = payload.p1_y.toFixed(2);
    if (payload.has_p2) {
      document.getElementById('p2x').disabled = false;
      document.getElementById('p2y').disabled = false;
      document.getElementById('p2x').value = payload.p2_x.toFixed(2);
      document.getElementById('p2y').value = payload.p2_y.toFixed(2);
    } else {
      document.getElementById('p2x').value = '';
      document.getElementById('p2y').value = '';
      document.getElementById('p2x').disabled = true;
      document.getElementById('p2y').disabled = true;
    }
    document.getElementById('rotMode').value = 'auto';
    document.getElementById('boardAngle').value = payload.recommended_angle.toFixed(4);
    updateBoardAngleLock();
  }

  document.getElementById('undoBtn').disabled = !payload.can_undo;
  document.getElementById('redoBtn').disabled = !payload.can_redo;
  if (payload.batch_message) {
    document.getElementById('batchFeedback').textContent = payload.batch_message;
  }

  renderFiducialList(payload.fiducials || []);
  renderDiagnostics(payload);
  renderTable(payload.components);

  const noCount = payload.components.filter(c => c.skip === 'No').length;
  const yesCount = payload.components.length - noCount;
  document.getElementById('tableSubHeader').textContent =
    `${payload.components.length} components (Place=${noCount}, Skip=${yesCount}) — Click any Skip badge to toggle`;
  document.getElementById('statusBar').textContent =
    isKicad
      ? `Stage 1 Ready: ${payload.filename} (${payload.components.length} SMD parts converted)`
      : `Stage 2 Ready: ${payload.filename} (SMD1: ${parseFloat(document.getElementById('p1x').value).toFixed(2)}, ${parseFloat(document.getElementById('p1y').value).toFixed(2)} | Angle: ${payload.active_angle >= 0 ? '+' : ''}${payload.active_angle.toFixed(4)}°)`;
}

function renderFiducialList(fids) {
  const container = document.getElementById('fidListContainer');
  container.innerHTML = '';
  if (!fids || fids.length === 0) {
    container.innerHTML = '<div style="font-size:11px;color:#64748b;">No fiducials found in mark line.</div>';
    return;
  }
  fids.forEach(f => {
    const row = document.createElement('div');
    row.className = 'fid-row';
    const badgeClass = f.used ? 'fid-badge-used' : 'fid-badge-unused';
    const badgeIcon = f.used ? '&#10003;' : '&#10007;';
    row.innerHTML = `
      <span class="fid-lbl">Fid ${f.index} [${f.corner || '?'}]</span>
      <label>X:</label>
      <input type="text" class="fid-coord" readonly value="${f.x.toFixed(4)}">
      <label>Y:</label>
      <input type="text" class="fid-coord" readonly value="${f.y.toFixed(4)}">
      <span class="${badgeClass}" title="${f.role_note}">${badgeIcon} ${f.role_note}</span>
    `;
    container.appendChild(row);
  });
}

function renderDiagnostics(payload) {
  const lines = [];
  lines.push(`File: ${payload.filename} [${payload.source_format === 'kicad_pos' ? 'Stage 1: KiCad .pos' : 'Stage 2: NeoDen 4 CSV'}] | Fiducials: ${payload.fiducials.length} (${payload.mark_scope}/${payload.mark_mode})`);
  if (payload.fiducials.length > 0) {
    const fstr = payload.fiducials.map(f => `Fid${f.index}[${f.corner}]=(${f.x.toFixed(2)}, ${f.y.toFixed(2)})`).join(', ');
    lines.push(`Fiducial Coords: ${fstr}`);
  }
  const edgeKeys = Object.keys(payload.edge_angles || {});
  if (edgeKeys.length > 0) {
    const estr = edgeKeys.map(k => `${k}: ${payload.edge_angles[k] >= 0 ? '+' : ''}${payload.edge_angles[k].toFixed(4)}°`).join(' | ');
    lines.push(`Edge Angles: ${estr}`);
  }
  lines.push(`Active Rotation: ${payload.active_angle >= 0 ? '+' : ''}${payload.active_angle.toFixed(4)}° (${payload.recommended_method})`);
  (payload.warnings || []).forEach(w => {
    lines.push(`[!] ${w}`);
  });
  document.getElementById('diagBox').textContent = lines.join('\\n');
}

function renderTable(comps) {
  const tbody = document.getElementById('compTableBody');
  tbody.innerHTML = '';
  comps.forEach((c, idx) => {
    const tr = document.createElement('tr');
    const skipClass = c.skip === 'No' ? 'row-skip-no' : 'row-skip-yes';
    tr.innerHTML = `
      <td>${idx + 1}</td>
      <td title="${c.name}"><strong>${c.name}</strong></td>
      <td>${c.feeder_id}</td>
      <td>${c.nozzle}</td>
      <td title="${c.value}">${c.value}</td>
      <td title="${c.footprint}">${c.footprint}</td>
      <td>${c.old_x.toFixed(2)}</td>
      <td>${c.old_y.toFixed(2)}</td>
      <td class="highlight-new">${c.new_x.toFixed(2)}</td>
      <td class="highlight-new">${c.new_y.toFixed(2)}</td>
      <td class="highlight-new">${c.new_rot.toFixed(2)}°</td>
      <td><span class="row-skip-pill ${skipClass}" onclick="toggleRowSkip(${idx})">${c.skip}</span></td>
    `;
    tbody.appendChild(tr);
  });
}

function updateBoardAngleLock() {
  const mode = document.getElementById('rotMode').value;
  const angleInput = document.getElementById('boardAngle');
  if (mode === 'manual') {
    angleInput.readOnly = false;
    angleInput.disabled = false;
  } else {
    angleInput.readOnly = true;
    angleInput.disabled = true;
  }
}

function onModeChange() {
  if (!currentData) return;
  updateBoardAngleLock();
  const mode = document.getElementById('rotMode').value;
  if (mode === 'auto') {
    document.getElementById('boardAngle').value = currentData.recommended_angle.toFixed(4);
  } else if (mode === 'avg') {
    const vals = Object.values(currentData.edge_angles || {});
    if (vals.length > 0) {
      const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
      document.getElementById('boardAngle').value = avg.toFixed(4);
    }
  } else if (mode === 'sq45' && currentData.fiducials.length >= 2) {
    const f1 = currentData.fiducials[0], f2 = currentData.fiducials[1];
    const dx = f2.x - f1.x, dy = f2.y - f1.y;
    const nom = Math.atan2(dy >= 0 ? 1 : -1, dx >= 0 ? 1 : -1) * 180 / Math.PI;
    const raw = Math.atan2(dy, dx) * 180 / Math.PI;
    let diff = ((raw - nom + 180) % 360) - 180;
    document.getElementById('boardAngle').value = diff.toFixed(4);
  } else if (mode === 'rect2' && currentData.fiducials.length >= 2) {
    const cdx = parseFloat(document.getElementById('cadDx').value);
    const cdy = parseFloat(document.getElementById('cadDy').value);
    if (!isNaN(cdx) && !isNaN(cdy) && (Math.abs(cdx) > 0 || Math.abs(cdy) > 0)) {
      const f1 = currentData.fiducials[0], f2 = currentData.fiducials[1];
      const dx = f2.x - f1.x, dy = f2.y - f1.y;
      const nom = Math.atan2(dy >= 0 ? Math.abs(cdy) : -Math.abs(cdy), dx >= 0 ? Math.abs(cdx) : -Math.abs(cdx)) * 180 / Math.PI;
      const raw = Math.atan2(dy, dx) * 180 / Math.PI;
      let diff = ((raw - nom + 180) % 360) - 180;
      document.getElementById('boardAngle').value = diff.toFixed(4);
    }
  }
  recalculate('custom');
}

async function toggleRowSkip(rowIndex) {
  if (!currentData) return;
  const res = await fetch('/api/toggle_row_skip', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      row_index: rowIndex,
      ...collectPreviewParams('custom')
    })
  });
  const payload = await res.json();
  applyLoadedState(payload, false);
}

function setSkipMode(mode) {
  recalculate(mode);
}

async function applyBatchEdit() {
  if (!currentData) return;
  const field = document.getElementById('batchField').value;
  const value = document.getElementById('batchValue').value.trim();
  const selector = document.getElementById('batchSelector').value.trim();
  if (!value || !selector) {
    document.getElementById('batchFeedback').textContent = 'Please enter both a value and component #s (e.g. 1-12).';
    return;
  }
  const res = await fetch('/api/batch_modify', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      field: field,
      value: value,
      selector: selector,
      ...collectPreviewParams('custom')
    })
  });
  const payload = await res.json();
  if (payload.error) {
    document.getElementById('batchFeedback').textContent = 'Error: ' + payload.error;
    return;
  }
  applyLoadedState(payload, false);
}

async function undoEdit() {
  if (!currentData) return;
  const res = await fetch('/api/undo', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(collectPreviewParams('custom'))
  });
  const payload = await res.json();
  applyLoadedState(payload, false);
}

async function redoEdit() {
  if (!currentData) return;
  const res = await fetch('/api/redo', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(collectPreviewParams('custom'))
  });
  const payload = await res.json();
  applyLoadedState(payload, false);
}

function collectPreviewParams(skipMode = 'custom') {
  const isKicad = currentData && currentData.source_format === 'kicad_pos';
  return {
    rot_mode: document.getElementById('rotMode').value,
    board_angle: isKicad ? 0.0 : (parseFloat(document.getElementById('boardAngle').value) || 0.0),
    p1_x: isKicad ? null : (parseFloat(document.getElementById('p1x').value) || 0.0),
    p1_y: isKicad ? null : (parseFloat(document.getElementById('p1y').value) || 0.0),
    p2_x: (isKicad || document.getElementById('p2x').disabled) ? null : (parseFloat(document.getElementById('p2x').value) || 0.0),
    p2_y: (isKicad || document.getElementById('p2y').disabled) ? null : (parseFloat(document.getElementById('p2y').value) || 0.0),
    rot_comp: document.getElementById('rotComp').checked,
    reset_mc_angle: document.getElementById('resetMcAngle').checked,
    skip_mode: skipMode
  };
}

async function recalculate(skipMode = 'custom') {
  if (!currentData) return;
  const res = await fetch('/api/preview', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(collectPreviewParams(skipMode))
  });
  const payload = await res.json();
  applyLoadedState(payload, false);
}

async function exportCSV() {
  if (!currentData) {
    alert('Please browse and select a file first.');
    return;
  }
  const res = await fetch('/api/export', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(collectPreviewParams('custom'))
  });
  const data = await res.json();
  if (data.error) {
    alert('Export error: ' + data.error);
    return;
  }
  const blob = new Blob([data.csv_content], {type: 'text/csv;charset=us-ascii'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = data.download_name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  document.getElementById('statusBar').textContent =
    data.saved_path
      ? `Downloaded & saved to: ${data.saved_path}`
      : `Downloaded ${data.download_name} (CRLF + exact NeoDen 4 commas)`;
}
</script>
</body>
</html>
"""


def serialize_analysis_state(
    analysis: NeoDenCSVAnalysis,
    active_angle: float,
    can_undo: bool = False,
    can_redo: bool = False,
    batch_message: str = "",
) -> Dict[str, Any]:
  c1 = analysis.components[0] if analysis.components else None
  p1 = analysis.panels[0] if analysis.panels else None
  p2 = analysis.panels[1] if len(analysis.panels) >= 2 else None
  return {
      "filename": os.path.basename(analysis.filepath),
      "source_format": analysis.source_format,
      "pcb_mode": analysis.pcb_mode,
      "pcb_feed": analysis.pcb_feed_type,
      "mark_scope": analysis.mark_scope,
      "mark_mode": analysis.mark_mode,
      "c1_name": c1.name if c1 else "—",
      "c1_footprint": c1.footprint if c1 else "—",
      "c1_x": c1.x if c1 else 0.0,
      "c1_y": c1.y if c1 else 0.0,
      "p1_x": p1.x if p1 else 0.0,
      "p1_y": p1.y if p1 else 0.0,
      "has_p2": p2 is not None,
      "p2_x": p2.x if p2 else 0.0,
      "p2_y": p2.y if p2 else 0.0,
      "recommended_angle": analysis.recommended_angle_deg,
      "active_angle": active_angle,
      "recommended_method": analysis.recommended_method,
      "edge_angles": analysis.edge_angles,
      "warnings": analysis.warnings,
      "can_undo": can_undo,
      "can_redo": can_redo,
      "batch_message": batch_message,
      "fiducials": [
          {
              "index": f.index,
              "x": f.x,
              "y": f.y,
              "corner": f.corner_label,
              "used": f.used_for_rotation,
              "role_note": f.role_note,
          }
          for f in analysis.fiducials
      ],
      "components": [
          {
              "name": c.name,
              "feeder_id": c.feeder_id,
              "nozzle": c.nozzle,
              "value": c.value,
              "footprint": c.footprint,
              "old_x": c.x,
              "old_y": c.y,
              "new_x": c.new_x,
              "new_y": c.new_y,
              "new_rot": c.new_rotation,
              "skip": c.skip,
          }
          for c in analysis.components[:500]
      ],
  }


def snapshot_components(analysis: NeoDenCSVAnalysis) -> List[Tuple[str, str, str, str]]:
  """Captures (feeder_id, nozzle, footprint, skip) for every component row."""
  return [
      (c.feeder_id, c.nozzle, c.footprint, c.skip) for c in analysis.components
  ]


def restore_components_snapshot(
    analysis: NeoDenCSVAnalysis, snap: List[Tuple[str, str, str, str]]
) -> None:
  for comp, (fid, noz, fp, sk) in zip(analysis.components, snap):
    comp.feeder_id = fid
    comp.nozzle = noz
    comp.footprint = fp
    comp.skip = sk


def launch_web_gui(
    initial_file: Optional[str] = None,
    port: int = 0,
    open_browser: bool = True,
) -> None:
  """Launches the 2-column Rufus-style GUI in the user's browser."""
  app_state: Dict[str, Any] = {
      "analysis": None,
      "undo_stack": [],
      "redo_stack": [],
      "last_batch_msg": "",
  }

  if initial_file and os.path.isfile(initial_file):
    analysis = parse_neoden_csv(initial_file)
    apply_transformation(
        analysis, board_angle_deg=analysis.recommended_angle_deg
    )
    app_state["analysis"] = analysis

  class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args: Any) -> None:
      pass

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
      raw = json.dumps(data).encode("utf-8")
      self.send_response(status)
      self.send_header("Content-Type", "application/json; charset=utf-8")
      self.send_header("Content-Length", str(len(raw)))
      self.end_headers()
      self.wfile.write(raw)

    def do_GET(self) -> None:
      if self.path == "/" or self.path.startswith("/index.html"):
        raw = RUFUS_WEB_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        return

      if self.path.startswith("/help"):
        raw = HELP_PAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        return

      self.send_error(404)

    def _run_preview_from_req(
        self, analysis: NeoDenCSVAnalysis, req: Dict[str, Any]
    ) -> float:
      rot_mode = req.get("rot_mode", "auto")
      update_fiducial_usage_for_mode(analysis, rot_mode)
      ang = float(req.get("board_angle", 0.0))
      p1x = req.get("p1_x")
      p1y = req.get("p1_y")
      p2x = req.get("p2_x")
      p2y = req.get("p2_y")
      if p1x is not None and analysis.panels:
        analysis.panels[0].x = float(p1x)
      if p1y is not None and analysis.panels:
        analysis.panels[0].y = float(p1y)
      if p2x is not None and len(analysis.panels) >= 2:
        analysis.panels[1].x = float(p2x)
      if p2y is not None and len(analysis.panels) >= 2:
        analysis.panels[1].y = float(p2y)

      skip_mode = req.get("skip_mode", "custom")
      if skip_mode != "custom":
        app_state["undo_stack"].append(snapshot_components(analysis))
        app_state["redo_stack"].clear()

      apply_transformation(
          analysis,
          board_angle_deg=ang,
          panel1_x=p1x,
          panel1_y=p1y,
          panel2_x=p2x,
          panel2_y=p2y,
          rotate_component_angles=bool(req.get("rot_comp", True)),
          reset_mirror_create_angle=bool(req.get("reset_mc_angle", True)),
          skip_mode=skip_mode,
      )
      return ang

    def do_POST(self) -> None:
      length = int(self.headers.get("Content-Length", "0"))
      if length > MAX_CSV_BYTES:
        self._send_json({"error": "File exceeds 5 MB limit."}, 400)
        return
      body = self.rfile.read(length) if length > 0 else b""

      try:
        if self.path.startswith("/api/upload"):
          parsed = urllib.parse.urlparse(self.path)
          qs = urllib.parse.parse_qs(parsed.query)
          fname = qs.get("filename", ["uploaded.csv"])[0]
          save_target = os.path.join(os.getcwd(), os.path.basename(fname))
          analysis = parse_neoden_csv_bytes(body, filepath=save_target)
          apply_transformation(
              analysis, board_angle_deg=analysis.recommended_angle_deg
          )
          app_state["analysis"] = analysis
          app_state["undo_stack"] = []
          app_state["redo_stack"] = []
          app_state["last_batch_msg"] = (
              "Enter row #s from the table on the right."
          )
          self._send_json(
              serialize_analysis_state(
                  analysis,
                  analysis.recommended_angle_deg,
                  can_undo=False,
                  can_redo=False,
                  batch_message=app_state["last_batch_msg"],
              )
          )
          return

        analysis = app_state.get("analysis")
        if analysis is None:
          self._send_json({"error": "No file loaded"}, 400)
          return

        req = json.loads(body.decode("utf-8")) if body else {}

        if self.path == "/api/preview":
          ang = self._run_preview_from_req(analysis, req)
          self._send_json(
              serialize_analysis_state(
                  analysis,
                  ang,
                  can_undo=bool(app_state["undo_stack"]),
                  can_redo=bool(app_state["redo_stack"]),
                  batch_message=app_state["last_batch_msg"],
              )
          )
          return

        if self.path == "/api/toggle_row_skip":
          row_idx = int(req.get("row_index", -1))
          if 0 <= row_idx < len(analysis.components):
            app_state["undo_stack"].append(snapshot_components(analysis))
            app_state["redo_stack"].clear()
            c = analysis.components[row_idx]
            c.skip = "Yes" if c.skip == "No" else "No"
            app_state["last_batch_msg"] = (
                f"Toggled Skip on #{row_idx + 1} ({c.name}) to '{c.skip}'."
            )
          ang = self._run_preview_from_req(analysis, req)
          self._send_json(
              serialize_analysis_state(
                  analysis,
                  ang,
                  can_undo=bool(app_state["undo_stack"]),
                  can_redo=bool(app_state["redo_stack"]),
                  batch_message=app_state["last_batch_msg"],
              )
          )
          return

        if self.path == "/api/batch_modify":
          snap = snapshot_components(analysis)
          _, msg = apply_batch_component_edit(
              analysis,
              field_name=req.get("field", "feeder"),
              new_value=req.get("value", ""),
              selector_str=req.get("selector", ""),
          )
          app_state["undo_stack"].append(snap)
          app_state["redo_stack"].clear()
          app_state["last_batch_msg"] = msg
          ang = self._run_preview_from_req(analysis, req)
          self._send_json(
              serialize_analysis_state(
                  analysis,
                  ang,
                  can_undo=bool(app_state["undo_stack"]),
                  can_redo=bool(app_state["redo_stack"]),
                  batch_message=app_state["last_batch_msg"],
              )
          )
          return

        if self.path == "/api/undo":
          if app_state["undo_stack"]:
            app_state["redo_stack"].append(snapshot_components(analysis))
            prev_snap = app_state["undo_stack"].pop()
            restore_components_snapshot(analysis, prev_snap)
            app_state["last_batch_msg"] = "Undid last component modification."
          ang = self._run_preview_from_req(analysis, req)
          self._send_json(
              serialize_analysis_state(
                  analysis,
                  ang,
                  can_undo=bool(app_state["undo_stack"]),
                  can_redo=bool(app_state["redo_stack"]),
                  batch_message=app_state["last_batch_msg"],
              )
          )
          return

        if self.path == "/api/redo":
          if app_state["redo_stack"]:
            app_state["undo_stack"].append(snapshot_components(analysis))
            next_snap = app_state["redo_stack"].pop()
            restore_components_snapshot(analysis, next_snap)
            app_state["last_batch_msg"] = "Redid component modification."
          ang = self._run_preview_from_req(analysis, req)
          self._send_json(
              serialize_analysis_state(
                  analysis,
                  ang,
                  can_undo=bool(app_state["undo_stack"]),
                  can_redo=bool(app_state["redo_stack"]),
                  batch_message=app_state["last_batch_msg"],
              )
          )
          return

        if self.path == "/api/export":
          ang = float(req.get("board_angle", 0.0))
          out_lines = apply_transformation(
              analysis,
              board_angle_deg=ang,
              panel1_x=req.get("p1_x"),
              panel1_y=req.get("p1_y"),
              panel2_x=req.get("p2_x"),
              panel2_y=req.get("p2_y"),
              rotate_component_angles=bool(req.get("rot_comp", True)),
              reset_mirror_create_angle=bool(req.get("reset_mc_angle", True)),
              skip_mode="custom",
          )
          base, _ext = os.path.splitext(analysis.filepath)
          suffix = (
              "_neoden4_init"
              if analysis.source_format == "kicad_pos"
              else "_aligned"
          )
          if base.endswith(("_aligned", "_neoden4", "_neoden4_init")):
            out_path = f"{base}.csv"
          else:
            out_path = f"{base}{suffix}.csv"
          saved_disk_path = ""
          try:
            save_neoden_csv(out_path, out_lines)
            saved_disk_path = out_path
          except OSError:
            pass
          csv_text = "\r\n".join(out_lines) + "\r\n"
          self._send_json({
              "saved_path": saved_disk_path,
              "download_name": os.path.basename(out_path),
              "csv_content": csv_text,
          })
          return
      except Exception as exc:
        self._send_json({"error": str(exc)}, 400)
        return

      self.send_error(404)

  server = HTTPServer(("127.0.0.1", port), Handler)
  actual_port = server.server_port
  url = f"http://127.0.0.1:{actual_port}/"
  print("=================================================================")
  print(f" NeoDen 4 CSV & KiCad .pos Utility v5.0 running at: {url}")
  print(f" Help & Workflow Guide available at: {url}help")
  print(" (Press Ctrl+C in this terminal window when you are done)")
  print("=================================================================")
  if open_browser:
    threading.Timer(0.2, lambda: webbrowser.open(url)).start()
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    print("\nShutting down GUI server.")
    server.server_close()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
  parser = argparse.ArgumentParser(
      description=(
          "NeoDen 4 Pick-and-Place CSV & KiCad .pos Converter Utility (v5.0). "
          "Run without arguments to launch the 2-column GUI."
      )
  )
  parser.add_argument(
      "input_csv",
      nargs="?",
      help="Input NeoDen 4 .csv OR KiCad .pos .csv file path",
  )
  parser.add_argument(
      "-o",
      "--output",
      help="Output NeoDen 4 .csv file path (if omitted, launches GUI)",
  )
  parser.add_argument(
      "--gui",
      action="store_true",
      help="Launch the 2-column GUI in your default web browser",
  )
  parser.add_argument(
      "--angle",
      type=float,
      default=None,
      help="Override board rotation angle in degrees (CCW positive)",
  )
  parser.add_argument(
      "--all-edges",
      action="store_true",
      help="Use average of all fiducial edges instead of orthogonal outlier rejection",
  )
  parser.add_argument(
      "--cad-dx",
      type=float,
      default=None,
      help="Nominal CAD dX (mm) when using a 2-fiducial rectangular diagonal",
  )
  parser.add_argument(
      "--cad-dy",
      type=float,
      default=None,
      help="Nominal CAD dY (mm) when using a 2-fiducial rectangular diagonal",
  )
  parser.add_argument(
      "--panel1",
      nargs=2,
      type=float,
      metavar=("X", "Y"),
      default=None,
      help="Set Panel 1 (bottom board) SMD1 machine X Y coordinates (mm)",
  )
  parser.add_argument(
      "--panel2",
      nargs=2,
      type=float,
      metavar=("X", "Y"),
      default=None,
      help="Set Panel 2 (top board) SMD1 machine X Y coordinates (mm)",
  )
  parser.add_argument(
      "--batch-modify",
      nargs=3,
      action="append",
      metavar=("FIELD", "VALUE", "COMPONENT_NUMS"),
      help=(
          "Batch modify components: FIELD is feeder|footprint|nozzle, VALUE is"
          " new value, COMPONENT_NUMS is e.g. '1-12,15'"
      ),
  )
  parser.add_argument(
      "--no-rotate-comp-angles",
      action="store_true",
      help="Do not add the board rotation angle to the individual component Rotation column",
  )
  parser.add_argument(
      "--keep-mirror-angle",
      action="store_true",
      help="Keep original mirror_create PCB angle instead of resetting to 0",
  )
  skip_grp = parser.add_mutually_exclusive_group()
  skip_grp.add_argument(
      "--unskip-all",
      "--skips-off",
      action="store_true",
      dest="skips_off",
      help="Toggle all skips OFF (Skip='No' on all components — place all)",
  )
  skip_grp.add_argument(
      "--skip-all",
      "--skips-on",
      action="store_true",
      dest="skips_on",
      help="Toggle all skips ON (Skip='Yes' on all components — skip all)",
  )
  skip_grp.add_argument(
      "--skip-all-except-first",
      action="store_true",
      dest="first_only",
      help="Set Skip='No' on 1st component (SMD1) and Skip='Yes' on all others",
  )

  args = parser.parse_args()

  is_cli_transform = (
      args.input_csv is not None
      and not args.gui
      and (
          args.output is not None
          or args.angle is not None
          or args.all_edges
          or args.panel1 is not None
          or args.panel2 is not None
          or args.batch_modify is not None
          or args.skips_off
          or args.skips_on
          or args.first_only
      )
  )

  if not is_cli_transform:
    launch_web_gui(initial_file=args.input_csv)
    return 0

  analysis = parse_neoden_csv(
      args.input_csv, cad_dx=args.cad_dx, cad_dy=args.cad_dy
  )

  if args.batch_modify:
    for field_name, val_str, sel_str in args.batch_modify:
      apply_batch_component_edit(analysis, field_name, val_str, sel_str)

  if args.angle is not None:
    chosen_angle = args.angle
    chosen_method = "Manual CLI Override"
  elif args.all_edges and analysis.edge_angles:
    chosen_angle = sum(analysis.edge_angles.values()) / len(
        analysis.edge_angles
    )
    chosen_method = "Average of All Fiducial Edges"
  else:
    chosen_angle = analysis.recommended_angle_deg
    chosen_method = analysis.recommended_method

  p1_x = args.panel1[0] if args.panel1 else None
  p1_y = args.panel1[1] if args.panel1 else None
  p2_x = args.panel2[0] if args.panel2 else None
  p2_y = args.panel2[1] if args.panel2 else None

  skip_mode = "custom"
  if args.skips_off:
    skip_mode = "all_no"
  elif args.skips_on:
    skip_mode = "all_yes"
  elif args.first_only:
    skip_mode = "first_no"

  out_lines = apply_transformation(
      analysis,
      board_angle_deg=chosen_angle,
      panel1_x=p1_x,
      panel1_y=p1_y,
      panel2_x=p2_x,
      panel2_y=p2_y,
      rotate_component_angles=not args.no_rotate_comp_angles,
      reset_mirror_create_angle=not args.keep_mirror_angle,
      skip_mode=skip_mode,
  )

  out_path = args.output
  if not out_path:
    base, _ext = os.path.splitext(args.input_csv)
    suffix = (
        "_neoden4_init"
        if analysis.source_format == "kicad_pos"
        else "_aligned"
    )
    out_path = f"{base}{suffix}.csv"

  save_neoden_csv(out_path, out_lines)

  no_cnt = sum(1 for c in analysis.components if c.skip == "No")
  yes_cnt = len(analysis.components) - no_cnt

  print("=== NeoDen 4 CSV & KiCad .pos Utility v5.0 Summary ===")
  print(f"Input File       : {args.input_csv} (format: {analysis.source_format})")
  print(f"Output File      : {out_path}")
  print(
      f"Fiducials Found  : {len(analysis.fiducials)} ({analysis.mark_scope},"
      f" {analysis.mark_mode})"
  )
  for f in analysis.fiducials:
    status_str = "USED" if f.used_for_rotation else "EXCLUDED"
    print(
        f"  - Fiducial {f.index} [{f.corner_label}]: ({f.x:.4f}, {f.y:.4f}) mm"
        f" [{status_str}: {f.role_note}]"
    )
  print(f"Rotation Method  : {chosen_method}")
  print(f"Board Rotation   : {chosen_angle:+.4f} deg")
  c1 = analysis.components[0]
  print(
      f"1st Comp ({c1.name:3s})   : CAD ({c1.x:.2f}, {c1.y:.2f}) -> Output"
      f" ({c1.new_x:.2f}, {c1.new_y:.2f}) mm"
  )
  print(
      f"Total Components : {len(analysis.components)} written (Place={no_cnt},"
      f" Skip={yes_cnt})"
  )

  return 0


if __name__ == "__main__":
  sys.exit(main())
