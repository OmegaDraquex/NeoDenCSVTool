#!/usr/bin/env python3
"""
NeoDen 4 & NeoDen YY1 Pick-and-Place CSV & KiCad .pos Converter Utility (v6.0)
------------------------------------------------------------------------------
What's New in v6.0:
  - Full NeoDen YY1 Support (Import, Edit & Cross-Conversion):
      * Detects and parses NeoDen YY1 Working CSVs ('NEODEN,YY1,P&P FILE'),
        NeoDen YY1 11-column Import CSVs ('Mid X,Mid Y,Ref X,Ref Y,Pad X,Pad Y'),
        NeoDen 4 Job CSVs ('#Feeder' / 'stack,' / '#SMD'), and KiCad '.pos' CSVs.
      * Converts any input format (KiCad .pos, NeoDen 4 CSV, or NeoDen YY1 CSV)
        to either NeoDen 4 CSV or NeoDen YY1 Working CSV ('NEODEN,YY1,P&P FILE')
        or NeoDen YY1 11-column Import Template CSV.
  - Edge-Case Protections Based on NeoDen 4 & YY1 Hardware Rules:
      * YY1 Coordinate Sign Protection: Detects negative X or Y coordinates
        (common when KiCad exports use a top-left page origin where Y is negative)
        and provides automatic Y-axis inversion (-Y -> +Y) or positive bounding-box
        shifting so the YY1 firmware never rejects negative coordinates.
      * Skip Flag Translation: Automatically translates between NeoDen 4's string
        skip format ('No' = place, 'Yes' = skip) and NeoDen YY1's integer skip
        format ('0' = place, '1' = skip) without losing user intent.
      * Head / Nozzle Range Clamping: NeoDen 4 supports Nozzles 1-4 while NeoDen YY1
        has 2 placement heads (1-2); converting Nozzles 3 or 4 to YY1 safely maps
        them to valid YY1 heads (1 or 2) with a diagnostic note.
      * Feeder Range Validation: Checks feeder numbers against NeoDen YY1 banks
        (1-52 Tape Reel, 53-80 Flexible/Stick/Tray, 81-99 Bulk Feeder) and
        auto-groups unmatched parts by (Value, Footprint) into sequential feeders.
      * Comma & Angle Sanitization: Strips internal commas from Comment/Value and
        Footprint strings so CSV columns never shift on the machine's C++ parser,
        wraps all angles strictly into [-180, +180], and formats numbers cleanly.
  - Responsive 2-Column Split Layout & Comprehensive Inline Code Comments:
      * Left pane holds all conversion, alignment, rotation, YY1 panel/fiducial/nozzle,
        and batch-edit settings; right pane displays the live Component Table.
      * Every function and section is thoroughly commented to explain both *what*
        the code does and *why* each engineering choice was made.
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

# Ensure UTF-8 console output on Windows/macOS terminals so degree symbols and
# box-drawing characters in CLI summaries never raise UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
  try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
  except Exception:
    pass

# Cap uploaded/parsed CSV files at 5 MB to prevent accidental memory exhaustion
# if a user selects a binary Gerber archive or huge log file instead of a PnP CSV.
MAX_CSV_BYTES = 5 * 1024 * 1024  # Hard 5 MB cap

# ---------------------------------------------------------------------------
# Built-in NeoDen 4 Machine Template (Used when converting KiCad/YY1 -> NeoDen 4)
# ---------------------------------------------------------------------------
# Why this template exists:
# The NeoDen 4 firmware requires a complete header section ('#Feeder' and 'stack'
# definitions plus a 'pcb' configuration line) before the '#SMD' component table;
# without these lines, the NeoDen 4 GUI refuses to import the CSV.
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

# Default reference-designator profile for known shop boards. Maps a specific
# designator to (feeder_id, nozzle_or_head, value, footprint, rot_offset_deg, skip).
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
  """
  Represents a PCB fiducial mark (from NeoDen 4 'mark', KiCad 'FID*', or YY1 'Fiducial').
  We track corner_label (BL, BR, TL, TR) and used_for_rotation so the user can see
  which fiducials contributed to the board tilt calculation and which were rejected
  as inset/keying marks.
  """
  index: int
  x: float
  y: float
  corner_label: str = ""
  used_for_rotation: bool = True
  role_note: str = "Used for Rotation"


@dataclass
class PanelMirror:
  """
  Represents a panelized board instance ('mirror' line in NeoDen 4).
  In NeoDen 4's manual workflow, the machine stores the physical camera-aligned
  coordinates of the 1st component (SMD1) inside the 'mirror' rows.
  """
  index: int
  line_idx: int
  x: float
  y: float
  rotation: float
  skip: str


@dataclass
class YY1NozzleChange:
  """
  Represents one of the 4 'NozzleChange' rows in a NeoDen YY1 Working CSV:
    NozzleChange,OFF,BeforeComponent,1,Head1,Drop,Station1,PickUp,Station1,,,,
  The YY1 supports up to 4 automatic nozzle swap actions during a job.
  """
  enabled: str = "OFF"  # "OFF" or "ON"
  before_component: int = 1
  head: str = "Head1"  # "Head1" or "Head2"
  drop_station: str = "Station1"  # "Station1", "Station2", etc.
  pickup_station: str = "Station1"


@dataclass
class ComponentRow:
  """
  Unified in-memory representation of a single pick-and-place component across
  KiCad .pos, NeoDen 4 CSV, and NeoDen YY1 CSV formats.
  Why unify them:
  By storing NeoDen 4 attributes (feeder_id, nozzle, skip='Yes'/'No') alongside
  NeoDen YY1 attributes (mount_speed, pick_height, place_height, yy1_mode), any
  loaded file can be previewed, rotated, nudged, batch-edited, and exported to
  EITHER NeoDen 4 or NeoDen YY1 without data loss.
  """
  line_idx: int
  feeder_id: str
  nozzle: str  # Called "Nozzle" (1-4) on NeoDen 4 and "Head" (1-2) on NeoDen YY1
  name: str  # Called "Ref" in KiCad, "Name" in NeoDen 4, "Designator" in YY1
  value: str  # Called "Val" in KiCad, "Value" in NeoDen 4, "Comment" in YY1
  footprint: str  # Package/Footprint string
  x: float
  y: float
  rotation: float
  skip: str  # Internally normalized to "Yes" (skip) or "No" (place)
  orig_skip: str = "Yes"
  new_x: float = 0.0
  new_y: float = 0.0
  new_rotation: float = 0.0
  # NeoDen YY1 specific component parameters (preserved when reading/writing YY1):
  mount_speed: int = 50       # Mount Speed (%) — YY1 default is 50-70%
  pick_height: float = 0.0    # Pick Height (mm) relative to Nozzle Height Datum
  place_height: float = 0.0   # Place Height (mm) relative to PCB surface
  yy1_mode: int = 1           # Recognition mode: 0=Null, 1=Camera, 2=Vacuum, 3=Cam+Vac, 4=Big IC
  layer: str = "T"            # 'T' (Top) or 'B' (Bottom) for YY1 11-column import template


@dataclass
class NeoDenCSVAnalysis:
  """
  Complete parsed state of an uploaded or CLI-loaded CSV file, including source
  format metadata, NeoDen 4 header structures, NeoDen YY1 header structures,
  fiducial geometry analysis, and component rows.
  """
  filepath: str
  # source_format can be: "neoden" (NeoDen 4), "neoden_yy1" (YY1 Working CSV),
  # "neoden_yy1_import" (YY1 11-col Import CSV), or "kicad_pos" (KiCad .pos CSV)
  source_format: str = "neoden"
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

  # NeoDen YY1 Header Metadata (PanelizedPCB, Fiducial, NozzleChange rows):
  yy1_unit_length: float = 53.24
  yy1_unit_width: float = 78.74
  yy1_rows: int = 1
  yy1_columns: int = 1
  yy1_fid_x: float = 0.0
  yy1_fid_y: float = 0.0
  yy1_offset_x: float = 0.0
  yy1_offset_y: float = 0.0
  yy1_nozzle_changes: List[YY1NozzleChange] = field(
      default_factory=lambda: [YY1NozzleChange() for _ in range(4)]
  )
  has_negative_coords: bool = False

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
  Inspects the first ~1 KB of a CSV file to determine its exact format:
    - 'neoden_yy1'        : NeoDen YY1 Working File ('NEODEN,YY1,P&P FILE' or
                            'Designator,Comment,Footprint,Mid X,Mid Y,Rotation,Head,FeederNo...')
    - 'neoden_yy1_import' : NeoDen YY1 11-Column Import File ('Designator,Footprint,Mid X,Mid Y,Ref X,Ref Y,Pad X,Pad Y,Layer,Rotation,Comment')
    - 'neoden'            : NeoDen 4 Job CSV ('#Feeder' or 'stack,' or '#SMD')
    - 'kicad_pos'         : KiCad Component Placement (.pos) CSV ('Ref,Val,Package,PosX,PosY,Rot,Side')
  Why inspect content instead of file extension:
  All four formats use the '.csv' (or '.pos') extension, so header inspection
  is the only reliable way to distinguish them automatically.
  """
  clean = head_text.lstrip("\ufeff").strip()
  clean_up = clean.upper()
  if "NEODEN,YY1,P&P FILE" in clean_up or (
      "DESIGNATOR,COMMENT,FOOTPRINT,MID X,MID Y,ROTATION,HEAD,FEEDERNO" in clean_up
  ) or "PANELIZEDPCB,UNITLENGTH" in clean_up:
    return "neoden_yy1"
  if (
      "DESIGNATOR,FOOTPRINT,MID X,MID Y,REF X,REF Y,PAD X,PAD Y" in clean_up
      or ("REF X" in clean_up and "PAD X" in clean_up and "DESIGNATOR" in clean_up)
  ):
    return "neoden_yy1_import"
  if "#Feeder" in clean or clean.startswith("stack,") or "#SMD," in clean:
    return "neoden"
  if "Ref,Val,Package,PosX,PosY,Rot,Side" in clean or (
      "PosX" in clean and "PosY" in clean and "Package" in clean
  ):
    return "kicad_pos"
  return None



def sanitize_csv_token(text: str) -> str:
  """
  Removes commas, carriage returns, and newlines from a text field.
  Why this is necessary:
  Neither the NeoDen 4 nor the NeoDen YY1 C++ firmware supports RFC-4180 quoted
  CSV fields; they split every line strictly on literal ',' characters. Any comma
  inside a Value/Comment or Footprint string shifts all subsequent columns and
  causes a machine import error.
  """
  return re.sub(r"[,\r\n]+", "_", (text or "").strip())


def format_yy1_float(val: float, decimals: int = 6) -> str:
  """
  Formats a float for NeoDen YY1 CSV output (up to 6 decimal places with
  trailing zeros and trailing decimal point stripped, e.g. 82.890000 -> '82.89',
  0.0 -> '0', -94.816666 -> '-94.816666').
  Why this choice was made:
  Native NeoDen YY1 working files (like DACdestroyers.csv) store integer-like
  floats as '0' or '90' and fractional coordinates with up to 6 decimals without
  trailing zero padding.
  """
  if math.isclose(val, 0.0, abs_tol=1e-9):
    return "0"
  s = f"{val:.{decimals}f}".rstrip("0").rstrip(".")
  return "0" if s in ("", "-0") else s


def clamp_yy1_head(nozzle_str: str) -> Tuple[str, bool]:
  """
  Ensures a head/nozzle identifier is valid for the NeoDen YY1 (which physically
  has only 2 heads: '1' and '2').
  Why this choice was made:
  NeoDen 4 machines have 4 nozzles ('1'..'4'), whereas the NeoDen YY1 has 2 heads
  ('1'..'2'). When converting a NeoDen 4 file that uses Nozzle 3 or 4 to YY1,
  mapping odd nozzles (1, 3 -> 1) and even nozzles (2, 4 -> 2) prevents an
  invalid head index on the YY1 while preserving dual-head alternation.
  Returns (yy1_head_str, was_clamped).
  """
  s = (nozzle_str or "1").strip()
  try:
    n = int(float(s))
    if n in (1, 2):
      return str(n), False
    mapped = "1" if (n % 2 == 1) else "2"
    return mapped, True
  except ValueError:
    return "1", True


def default_yy1_vision_mode(footprint: str, ref: str = "") -> int:
  """
  Chooses a sensible default NeoDen YY1 recognition mode (0..4) based on package:
    - Mode 1 (Camera) for standard passives, diodes, and small SOT packages
    - Mode 3 (Camera + Vacuum) for medium ICs (SOP, SOIC, TSSOP, SSOP, MSOP)
    - Mode 4 (Big IC Camera — enlarged FOV) for fine-pitch / large multi-pin ICs
      (LQFP, TQFP, QFN, BGA)
  Why this choice was made:
  Per the NeoDen YY1 User Manual (Section 3.2.1), Mode 1 is fastest and standard
  for passives (matching DACdestroyers.csv where passives/LEDs/SOT use Mode 1),
  while larger ICs benefit from Vacuum check or Enlarged FOV (Mode 3/4).
  """
  fp_up = (footprint or "").upper()
  if any(k in fp_up for k in ("LQFP", "TQFP", "QFP", "QFN", "BGA")):
    return 4
  if any(k in fp_up for k in ("SOP", "SOIC", "TSSOP", "SSOP", "MSOP")):
    return 3
  return 1


def normalize_kicad_footprint(pkg: str, val: str = "") -> Tuple[str, float]:
  """
  Converts verbose KiCad package strings (e.g. 'R_0805_2012Metric',
  'SOIC-24W_7.5x15.4mm_Pitch1.27mm') into short NeoDen footprint codes
  ('0805', 'SOP24') and returns (neoden_footprint, default_rot_offset_deg).
  Why this choice was made:
  KiCad footprint library names often exceed 20 characters and include metric
  dimensions that overflow NeoDen UI fields and prevent feeder matching by
  package size. Standardizing to short JEDEC/EIA codes ('0402', '0603', '0805',
  '1206', 'SOT23', 'SOP24', etc.) makes component grouping and vision setup clean.
  """
  p = sanitize_csv_token(pkg)
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

  # Extract standard Imperial passive size code (0201..2512) from KiCad strings
  # such as 'C_0603_1608Metric' or 'R_0805_HandSolder'.
  m_passive = re.search(
      r"(?:^|_|-)(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?:_|-|$)", p
  )
  if m_passive:
    return m_passive.group(1), 0.0

  # Condense standard IC package prefixes + pin counts (e.g. 'SOIC-16' -> 'SOP16')
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

  # Truncate unknown custom footprints to 16 chars so NeoDen's parser doesn't choke
  return p[:16], 0.0


def should_exclude_kicad_part(ref: str, val: str, pkg: str) -> Tuple[bool, str]:
  """
  Identifies non-SMD, hand-soldered, or mechanical PCB features in a KiCad .pos
  export that should not be picked and placed by an automated SMT machine.
  Why this choice was made:
  KiCad frequently exports fiducial footprints ('FID1'), through-hole DIP sockets,
  test points ('TP1'), pin headers ('J1'), switches, and battery contacts into the
  placement file. Filtering them automatically prevents the PnP machine from
  attempting to assign feeders to unmountable parts.
  """
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
      or "MOUNTINGHOLE" in p_up
      or r_up.startswith("H") and "HOLE" in p_up
  ):
    return True, f"Connector/header/mounting hole ({ref})"
  return False, ""


def assign_feeder_for_kicad_part(
    ref: str, val: str, pkg: str
) -> Tuple[str, str, str, str, float, str]:
  """
  Maps a KiCad component (Ref, Val, Package) to:
    (feeder_id, nozzle, neoden_val, neoden_fp, rot_offset_deg, default_skip).
  Why this choice was made:
  1. Checks DEFAULT_REF_PROFILE first for known project designators.
  2. Normalizes verbose KiCad footprints into clean codes ('0805', '0603', etc.).
  3. Cleans up unannotated KiCad Values that accidentally repeat the long package
     name so the Comment/Value column stays readable on the machine screen.
  """
  if ref in DEFAULT_REF_PROFILE:
    return DEFAULT_REF_PROFILE[ref]

  fp, rot_off = normalize_kicad_footprint(pkg, val)
  v_clean = sanitize_csv_token(val)
  # If KiCad Value is identical to the verbose package name (common on unannotated footprints),
  # shorten it to the normalized footprint so it doesn't clutter the NeoDen UI.
  if v_clean == sanitize_csv_token(pkg) and len(v_clean) > 12:
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
  Job CSV preserving the raw KiCad CAD coordinates (so the user can either load
  it onto the NeoDen 4 machine for Stage 2 alignment, or convert it directly to
  NeoDen YY1 format).
  Why this choice was made:
  By converting KiCad .pos into our canonical NeoDen 4 structure first, all
  downstream tools (component table preview, batch editing, skip toggling, and
  export to either NeoDen 4 or NeoDen YY1) work identically regardless of input.
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
    ref = sanitize_csv_token(row.get("Ref") or "")
    val = sanitize_csv_token(row.get("Val") or "")
    pkg = sanitize_csv_token(row.get("Package") or "")
    side = (row.get("Side") or "top").strip().lower()
    if not ref:
      continue

    try:
      # Strip any trailing 'mm' unit suffix if a custom exporter included units
      raw_px = re.sub(r"[a-zA-Z]+$", "", (row.get("PosX") or "0").strip())
      raw_py = re.sub(r"[a-zA-Z]+$", "", (row.get("PosY") or "0").strip())
      raw_rot = re.sub(r"[a-zA-Z]+$", "", (row.get("Rot") or "0").strip())
      posx = float(raw_px or 0.0)
      posy = float(raw_py or 0.0)
      rot = float(raw_rot or 0.0)
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

    cad_x = round(posx, 4)
    cad_y = round(posy, 4)
    cad_rot = wrap_neoden_angle(rot + rot_off)

    if len(comp_lines) == 0:
      def_skip = "No"
      first_comp_xy = (cad_x, cad_y)

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
# Core Geometry & NeoDen 4 / NeoDen YY1 CSV Parser & Formatter
# ---------------------------------------------------------------------------


def wrap_neoden_angle(angle_deg: float) -> float:
  """
  Wraps any rotation angle in degrees into the strict [-180.0, +180.0] interval
  required by both NeoDen 4 and NeoDen YY1 firmware.
  Why this choice was made:
  KiCad exports rotations in [0, 360) (e.g. 270 deg), and adding a board rotation
  offset can push angles outside [-180, 180]. Both NeoDen machines reject or
  misinterpret angles outside [-180, +180]. We normalize -180.0 to +180.0 and
  -0.0 to 0.0 for clean output.
  """
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
  Repairs a single NeoDen 4 CSV line if Microsoft Excel padded shorter rows with
  extra trailing commas (to match the 33-column 'stack' rows) or stripped the
  single trailing comma required by the NeoDen 4 C++ parser.
  Why this choice was made:
  Opening and saving a NeoDen 4 CSV in Excel pads every row to the widest column
  count in the sheet. The NeoDen 4 parser expects an exact number of commas per
  section type ('#SMD' has no trailing comma; 'comp', 'pcb', 'mirror' end with
  exactly one trailing comma).
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
  Classifies 2, 3, or 4 fiducials, calculates the physical PCB rotation angle
  (in degrees counter-clockwise), and marks which exact fiducials are used for
  rotation vs. excluded as anti-reversal/keying marks.
  Why this choice was made:
  1. With 2 fiducials: they may lie along a horizontal edge, a vertical edge, or
     a board diagonal (45 deg on a square board, or atan2(dY, dX) on a rectangular
     board). Detecting edge vs. diagonal geometry avoids a 45-degree error.
  2. With 3 or 4 fiducials: PCB designers commonly inset one corner fiducial by
     1-5 mm as an anti-reversal keying mark so an operator cannot load the board
     rotated 180 deg. Naive averaging of all 4 edges corrupts the rotation angle
     when 1 fiducial is intentionally offset. By evaluating all 4 corner pairs
     (Left+Top, Left+Bottom, Right+Top, Right+Bottom) and selecting the pair whose
     two edges are closest to a true 90.000 deg angle, we automatically isolate the
     3 symmetric corners and reject the 4th offset/keying fiducial.
  Returns (edge_angles, recommended_angle, method_name, used_indices, outlier_note).
  """
  edge_angles: Dict[str, float] = {}
  outlier_note = ""
  used_indices: List[int] = []

  n = len(fiducials)
  if n < 2:
    # A single fiducial (such as on the NeoDen YY1) only defines a translational
    # offset (X, Y) and cannot determine board rotation on its own.
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

    # Case A: Both fiducials share approximately the same Y -> Horizontal board edge
    if abs(dy) < 0.15 * abs(dx):
      rot = wrap_neoden_angle(raw_angle if dx > 0 else raw_angle - 180.0)
      edge_angles["Horizontal Edge (Fid1->Fid2)"] = rot
      return edge_angles, rot, "2-Fiducial Horizontal Edge", used_indices, outlier_note
    # Case B: Both fiducials share approximately the same X -> Vertical board edge
    if abs(dx) < 0.15 * abs(dy):
      rot = wrap_neoden_angle(
          raw_angle - 90.0 if dy > 0 else raw_angle + 90.0
      )
      edge_angles["Vertical Edge (Fid1->Fid2)"] = rot
      return edge_angles, rot, "2-Fiducial Vertical Edge", used_indices, outlier_note

    # Case C: Diagonal fiducial pair (either rectangular CAD dX/dY or 45-deg square)
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

  # For 3 or 4 fiducials, classify each fiducial into a quadrant (BL, BR, TL, TR)
  # relative to the centroid (cx, cy) of all fiducials.
  cx = sum(f.x for f in fiducials) / n
  cy = sum(f.y for f in fiducials) / n
  corners: Dict[str, Fiducial] = {}
  for f in fiducials:
    horiz = "R" if f.x >= cx else "L"
    vert = "T" if f.y >= cy else "B"
    label = f"{vert}{horiz}"
    f.corner_label = label
    corners[label] = f

  # Compute the tilt angle of each available perimeter edge relative to 0 deg
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
  """
  Updates each fiducial's used_for_rotation flag and role_note for the active UI
  rotation mode so the user can visually confirm which marks are active.
  """
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


def _populate_yy1_Into_neoden_analysis(
    text: str, analysis: NeoDenCSVAnalysis
) -> None:
  """
  Parses either a NeoDen YY1 Working CSV ('NEODEN,YY1,P&P FILE') or a NeoDen YY1
  11-column Import CSV into the unified NeoDenCSVAnalysis structure, and also
  populates synthetic NeoDen 4 raw_lines so the user can convert YY1 -> NeoDen 4
  as well as YY1 -> YY1.
  Why this choice was made:
  A NeoDen YY1 Working CSV has 4 distinct metadata sections before the component
  table ('PanelizedPCB', 'Fiducial', 4x 'NozzleChange', and the 13-column header
  'Designator,Comment,Footprint,Mid X,Mid Y,Rotation,Head,FeederNo,Mount Speed (%),
  Pick Height(mm),Place Height(mm),Mode,Skip'). Parsing these fields into
  NeoDenCSVAnalysis preserves every YY1 setting while exposing all components to
  the same alignment, rotation, and batch-edit tools.
  """
  lines = text.splitlines()
  nozzle_changes: List[YY1NozzleChange] = []
  in_comp_table = False
  is_11col_import = (analysis.source_format == "neoden_yy1_import")
  comp_lines_n4: List[str] = []
  first_comp_xy: Optional[Tuple[float, float]] = None

  for raw_line in lines:
    stripped = raw_line.strip()
    if not stripped:
      continue
    tokens = [t.strip() for t in stripped.split(",")]
    if not any(tokens):
      continue

    first_tok = tokens[0]
    first_up = first_tok.upper()

    if not in_comp_table:
      if first_up == "NEODEN":
        continue
      if first_up == "PANELIZEDPCB" and len(tokens) >= 9:
        # Format: PanelizedPCB,UnitLength,53.24,UnitWidth,78.74,Rows,1,Columns,1,,,,
        try:
          analysis.yy1_unit_length = float(tokens[2] or 53.24)
          analysis.yy1_unit_width = float(tokens[4] or 78.74)
          analysis.yy1_rows = max(1, int(float(tokens[6] or 1)))
          analysis.yy1_columns = max(1, int(float(tokens[8] or 1)))
        except ValueError:
          pass
        continue
      if first_up == "FIDUCIAL" and len(tokens) >= 9:
        # Format: Fiducial,1-X,0,1-Y,0,OverallOffsetX,0,OverallOffsetY,0,,,,
        try:
          analysis.yy1_fid_x = float(tokens[2] or 0.0)
          analysis.yy1_fid_y = float(tokens[4] or 0.0)
          analysis.yy1_offset_x = float(tokens[6] or 0.0)
          analysis.yy1_offset_y = float(tokens[8] or 0.0)
          if abs(analysis.yy1_fid_x) > 1e-6 or abs(analysis.yy1_fid_y) > 1e-6:
            analysis.fiducials.append(
                Fiducial(
                    index=1,
                    x=analysis.yy1_fid_x,
                    y=analysis.yy1_fid_y,
                    corner_label="YY1-Fid1",
                    used_for_rotation=False,
                    role_note="YY1 Single Fiducial Reference",
                )
            )
        except ValueError:
          pass
        continue
      if first_up == "NOZZLECHANGE" and len(tokens) >= 9:
        # Format: NozzleChange,OFF,BeforeComponent,1,Head1,Drop,Station1,PickUp,Station1,,,,
        try:
          nc = YY1NozzleChange(
              enabled=tokens[1].upper() if tokens[1] else "OFF",
              before_component=max(1, int(float(tokens[3] or 1))),
              head=tokens[4] or "Head1",
              drop_station=tokens[6] or "Station1",
              pickup_station=tokens[8] or "Station1",
          )
          nozzle_changes.append(nc)
        except ValueError:
          pass
        continue
      if first_up == "DESIGNATOR":
        in_comp_table = True
        if len(tokens) >= 11 and "REF X" in stripped.upper():
          is_11col_import = True
        continue

    # Parse component row (either 13-col YY1 Working File or 11-col YY1 Import File)
    if in_comp_table:
      if is_11col_import and len(tokens) >= 11:
        # 11-col Import format:
        # Designator,Footprint,Mid X,Mid Y,Ref X,Ref Y,Pad X,Pad Y,Layer,Rotation,Comment
        des = sanitize_csv_token(tokens[0])
        fp = sanitize_csv_token(tokens[1])
        val = sanitize_csv_token(tokens[10])
        layer = (tokens[8] or "T").strip().upper()
        try:
          cx = float(re.sub(r"[a-zA-Z]+$", "", tokens[2]) or 0.0)
          cy = float(re.sub(r"[a-zA-Z]+$", "", tokens[3]) or 0.0)
          crot = wrap_neoden_angle(
              float(re.sub(r"[a-zA-Z]+$", "", tokens[9]) or 0.0)
          )
        except ValueError:
          continue
        feeder_id, nozzle, _v, norm_fp, _roff, _sk = assign_feeder_for_kicad_part(
            des, val, fp
        )
        head_str, _ = clamp_yy1_head(nozzle)
        skip_n4 = "No"
        speed = 50
        pick_h = 0.0
        place_h = 0.0
        mode_val = default_yy1_vision_mode(fp or norm_fp, des)
      elif len(tokens) >= 13:
        # 13-col YY1 Working File format (like DACdestroyers.csv):
        # Designator,Comment,Footprint,Mid X,Mid Y,Rotation,Head,FeederNo,Mount Speed (%),Pick Height(mm),Place Height(mm),Mode,Skip
        des = sanitize_csv_token(tokens[0])
        val = sanitize_csv_token(tokens[1])
        fp = sanitize_csv_token(tokens[2])
        if not des:
          continue
        try:
          cx = float(re.sub(r"[a-zA-Z]+$", "", tokens[3]) or 0.0)
          cy = float(re.sub(r"[a-zA-Z]+$", "", tokens[4]) or 0.0)
          crot = wrap_neoden_angle(
              float(re.sub(r"[a-zA-Z]+$", "", tokens[5]) or 0.0)
          )
        except ValueError:
          continue
        head_str, _ = clamp_yy1_head(tokens[6] or "1")
        feeder_id = tokens[7] or "1"
        try:
          speed = max(1, min(100, int(float(tokens[8] or 50))))
        except ValueError:
          speed = 50
        try:
          pick_h = float(tokens[9] or 0.0)
        except ValueError:
          pick_h = 0.0
        try:
          place_h = float(tokens[10] or 0.0)
        except ValueError:
          place_h = 0.0
        try:
          mode_val = max(0, min(4, int(float(tokens[11] or 1))))
        except ValueError:
          mode_val = 1
        # YY1 Skip column: '0' means Place (Skip='No' in NeoDen 4), '1' means Skip (Skip='Yes' in NeoDen 4)
        raw_skip = (tokens[12] or "0").strip().upper()
        skip_n4 = "Yes" if raw_skip in ("1", "YES", "TRUE") else "No"
        layer = "T"
      else:
        continue

      if cx < 0.0 or cy < 0.0:
        analysis.has_negative_coords = True

      if first_comp_xy is None:
        first_comp_xy = (cx, cy)

      line_idx_n4 = len(DEFAULT_NEODEN_STACK_LINES) + 7 + len(comp_lines_n4)
      comp_row = ComponentRow(
          line_idx=line_idx_n4,
          feeder_id=feeder_id,
          nozzle=head_str,
          name=des,
          value=val,
          footprint=fp,
          x=cx,
          y=cy,
          rotation=crot,
          skip=skip_n4,
          orig_skip=skip_n4,
          new_x=cx,
          new_y=cy,
          new_rotation=crot,
          mount_speed=speed,
          pick_height=pick_h,
          place_height=place_h,
          yy1_mode=mode_val,
          layer=layer,
      )
      analysis.components.append(comp_row)
      comp_lines_n4.append(
          f"comp,{feeder_id},{head_str},{des},{val},{fp},"
          f"{cx:.2f},{cy:.2f},{crot:.2f},{skip_n4},"
      )

  while len(nozzle_changes) < 4:
    nozzle_changes.append(YY1NozzleChange())
  analysis.yy1_nozzle_changes = nozzle_changes[:4]

  # Populate synthetic NeoDen 4 header lines so this YY1 file can also be
  # exported to NeoDen 4 or aligned using the Panel 1 / SMD1 controls.
  header_lines = list(DEFAULT_NEODEN_STACK_LINES)
  fx, fy = analysis.yy1_fid_x, analysis.yy1_fid_y
  header_lines.append(f"mark,Whole,Auto,{fx:.4f},{fy:.4f},{fx:.4f},{fy:.4f},")
  header_lines.append("markext,0,0.8,3,1,0,")
  header_lines.append("markext,1,0.8,3,1,0,")
  header_lines.append("test,No,")
  c1x, c1y = first_comp_xy if first_comp_xy else (0.0, 0.0)
  header_lines.append(
      f"mirror_create,{analysis.yy1_columns},{analysis.yy1_rows},"
      f"{c1x:.2f},{c1y:.2f},{c1x:.2f},{c1y:.2f},0,0,0,"
  )
  p1_line_idx = len(header_lines)
  header_lines.append(f"mirror,{c1x:.2f},{c1y:.2f},0,No,")
  p2_line_idx = len(header_lines)
  header_lines.append(f"mirror,{c1x:.2f},{c1y:.2f},0,No,")
  header_lines.append(
      "#SMD,Feeder ID,Nozzle,Name,Value,Footprint,X,Y,Rotation,Skip"
  )

  analysis.raw_lines = header_lines + comp_lines_n4
  analysis.mirror_create_line_idx = p1_line_idx - 1
  analysis.mirror_cols = analysis.yy1_columns
  analysis.mirror_rows = analysis.yy1_rows
  analysis.panels = [
      PanelMirror(index=1, line_idx=p1_line_idx, x=c1x, y=c1y, rotation=0.0, skip="No"),
      PanelMirror(index=2, line_idx=p2_line_idx, x=c1x, y=c1y, rotation=0.0, skip="No"),
  ]
  analysis.feeder_ids = sorted(
      {c.feeder_id for c in analysis.components},
      key=lambda x: int(x) if x.isdigit() else 999,
  )


def parse_neoden_csv_bytes(
    raw_bytes: bytes,
    filepath: str = "uploaded.csv",
    cad_dx: Optional[float] = None,
    cad_dy: Optional[float] = None,
    exclude_non_smd: bool = True,
) -> NeoDenCSVAnalysis:
  """
  Parses raw bytes of a NeoDen 4 Job CSV, a NeoDen YY1 Working/Import CSV, or a
  KiCad .pos CSV (strictly capped at 5 MB).
  Why this choice was made:
  Centralizing byte-level decoding, UTF-8 BOM stripping, line-ending detection,
  and format auto-detection in one parser ensures identical behavior between the
  command-line interface and the browser-based drag/drop uploader.
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

  csv_type = detect_csv_type_from_head(text[:2048]) or "neoden"
  cad_fids: List[Fiducial] = []
  excluded_parts: List[str] = []

  analysis = NeoDenCSVAnalysis(
      filepath=filepath,
      source_format=csv_type,
      raw_lines=[],
      had_bom=had_bom,
      line_ending=line_ending,
      cad_fiducials=cad_fids,
      excluded_parts=excluded_parts,
  )

  # Route NeoDen YY1 files through the dedicated YY1 parser
  if csv_type in ("neoden_yy1", "neoden_yy1_import"):
    _populate_yy1_Into_neoden_analysis(text, analysis)
    analysis.recommended_angle_deg = 0.0
    analysis.recommended_method = "NeoDen YY1 File (Preserving Coordinates)"
    analysis.warnings.append(
        f"Loaded NeoDen YY1 CSV ({len(analysis.components)} components,"
        f" Panel={analysis.yy1_unit_length}x{analysis.yy1_unit_width} mm,"
        f" {analysis.yy1_rows}x{analysis.yy1_columns})."
    )
    if analysis.has_negative_coords:
      analysis.warnings.append(
          "EDGE CASE WARNING (Negative Coordinates): File contains negative X or Y"
          " coordinates (common when exported from KiCad with a top-left origin)."
          " The NeoDen YY1 manual states that coordinates must be positive. Enable"
          " 'Invert Negative Y (-Y -> +Y)' or 'Auto-Shift to Positive' when"
          " exporting for the YY1."
      )
    empty_fp_count = sum(1 for c in analysis.components if not c.footprint)
    if empty_fp_count > 0:
      analysis.warnings.append(
          f"Footprint Notice: {empty_fp_count} component(s) have an empty"
          " Footprint column. The YY1 groups identical parts by (Comment +"
          " Footprint); you can batch-assign footprints in Section 4 if needed."
      )
    return analysis

  if csv_type == "kicad_pos":
    raw_lines, cad_fids, excluded_parts = convert_kicad_pos_text_to_neoden_lines(
        text, exclude_non_smd=exclude_non_smd
    )
    analysis.cad_fiducials = cad_fids
    analysis.excluded_parts = excluded_parts
  else:
    raw_lines = text.splitlines()

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
        analysis.yy1_columns = max(1, analysis.mirror_cols)
        analysis.yy1_rows = max(1, analysis.mirror_rows)
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
        if cx < 0.0 or cy < 0.0:
          analysis.has_negative_coords = True
        fp_str = sanitize_csv_token(tokens[5])
        des_str = sanitize_csv_token(tokens[3])
        analysis.components.append(
            ComponentRow(
                line_idx=idx,
                feeder_id=tokens[1].strip(),
                nozzle=tokens[2].strip(),
                name=des_str,
                value=sanitize_csv_token(tokens[4]),
                footprint=fp_str,
                x=cx,
                y=cy,
                rotation=crot,
                skip=sk,
                orig_skip=sk,
                new_x=cx,
                new_y=cy,
                new_rotation=crot,
                mount_speed=50,
                pick_height=0.0,
                place_height=0.0,
                yy1_mode=default_yy1_vision_mode(fp_str, des_str),
            )
        )
      except ValueError:
        pass

  # Estimate board bounding box dimensions for YY1 PanelizedPCB header if converting
  # from KiCad or NeoDen 4, and seed YY1 fiducial 1 from the first available fiducial.
  if analysis.components:
    xs = [abs(c.x) for c in analysis.components]
    ys = [abs(c.y) for c in analysis.components]
    span_x = round(max(xs) - min(xs) + 10.0, 2)
    span_y = round(max(ys) - min(ys) + 10.0, 2)
    if span_x > 5.0:
      analysis.yy1_unit_length = span_x
    if span_y > 5.0:
      analysis.yy1_unit_width = span_y
  if analysis.fiducials:
    analysis.yy1_fid_x = analysis.fiducials[0].x
    analysis.yy1_fid_y = analysis.fiducials[0].y

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
    if analysis.has_negative_coords:
      analysis.warnings.append(
          "Coordinate Sign Notice: KiCad .pos contains negative coordinates"
          " (top-left page origin). If exporting to NeoDen YY1, 'Invert Negative"
          " Y (-Y -> +Y)' will ensure positive coordinates as required by YY1."
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
  """Reads a CSV file from disk (verifying the 5 MB limit) and parses it."""
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
  Why this choice was made:
  Operators frequently need to assign the same feeder, nozzle/head, footprint,
  or mount speed to a contiguous block of resistors/capacitors in the table without
  clicking 30 individual dropdowns.
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
  Applies a batch modification to 'feeder', 'footprint', 'nozzle' (Head),
  'speed' (YY1 Mount Speed %), 'pick_height' (mm), 'place_height' (mm), or
  'mode' (YY1 Vision Mode 0-4) across the selected 1-based component #s.
  Returns (count_modified, human_readable_message).
  """
  val_clean = sanitize_csv_token(new_value)
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
    elif f_key in ("nozzle", "head"):
      comp.nozzle = val_clean
    elif f_key == "speed":
      try:
        comp.mount_speed = max(1, min(100, int(float(val_clean))))
      except ValueError as exc:
        raise ValueError("Mount Speed (%) must be an integer between 1 and 100.") from exc
    elif f_key == "pick_height":
      try:
        comp.pick_height = float(val_clean)
      except ValueError as exc:
        raise ValueError("Pick Height (mm) must be a valid number.") from exc
    elif f_key == "place_height":
      try:
        comp.place_height = float(val_clean)
      except ValueError as exc:
        raise ValueError("Place Height (mm) must be a valid number.") from exc
    elif f_key == "mode":
      try:
        m = int(float(val_clean))
        if m not in (0, 1, 2, 3, 4):
          raise ValueError()
        comp.yy1_mode = m
      except ValueError as exc:
        raise ValueError(
            "YY1 Mode must be 0 (Null), 1 (Camera), 2 (Vacuum), 3 (Cam+Vac), or 4 (Big IC)."
        ) from exc
    else:
      raise ValueError(f"Unknown field '{field_name}'.")

  label_map = {
      "feeder": "Feeder ID",
      "footprint": "Footprint",
      "nozzle": "Nozzle / Head",
      "head": "Head",
      "speed": "Mount Speed (%)",
      "pick_height": "Pick Height (mm)",
      "place_height": "Place Height (mm)",
      "mode": "YY1 Vision Mode",
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
  """
  Updates each component's internal .skip attribute ('Yes' or 'No') according to
  skip_mode ('all_no' = place all, 'all_yes' = skip all, 'first_no' = place 1st only,
  'original' = restore file default).
  """
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


def auto_assign_feeders_by_part(
    analysis: NeoDenCSVAnalysis,
    start_feeder: int = 1,
    alternate_heads: bool = True,
) -> str:
  """
  Groups components by unique (Value, Footprint) and assigns sequential feeder
  numbers starting at start_feeder (e.g., 1 for Tape Feeders 1-52, 53 for
  Stick/Tray 53-80, or 81 for Bulk Feeders 81-99, matching DACdestroyers.csv).
  Optionally alternates Head 1 and Head 2 across consecutive placements so the
  machine can pick two parts per gantry pass.
  """
  start_num = max(1, min(99, int(start_feeder)))
  part_to_feeder: Dict[Tuple[str, str], str] = {}
  next_feeder = start_num

  for idx, comp in enumerate(analysis.components):
    key = (comp.value.strip().upper(), comp.footprint.strip().upper())
    if key not in part_to_feeder:
      part_to_feeder[key] = str(min(99, next_feeder))
      next_feeder += 1
    comp.feeder_id = part_to_feeder[key]
    if alternate_heads:
      comp.nozzle = "1" if (idx % 2 == 0) else "2"

  return (
      f"Auto-assigned {len(part_to_feeder)} unique part type(s) to Feeders"
      f" {start_num}..{min(99, next_feeder - 1)}"
      + (" with Head 1/2 alternation." if alternate_heads else ".")
  )


def _natural_designator_key(name: str) -> Tuple[str, int, str]:
  """Splits a designator like 'C12' into ('C', 12, '') for natural sorting."""
  m = re.match(r"^([A-Za-z_]+)(\d+)(.*)$", (name or "").strip())
  if m:
    return (m.group(1).upper(), int(m.group(2)), m.group(3))
  return ((name or "").upper(), 0, "")


def sort_components_order(
    analysis: NeoDenCSVAnalysis, sort_by: str = "feeder"
) -> str:
  """
  Reorders analysis.components by:
    - 'feeder'     : Feeder number ascending, then natural designator (minimizes feeder travel)
    - 'footprint'  : Footprint & Head ascending, then Feeder (groups nozzle sizes before nozzle change)
    - 'designator' : Natural designator order (C1, C2, ..., R1, R2, ..., U1)
  Preserves original line_idx slot positions so NeoDen 4 raw_lines stay aligned.
  """
  line_indices = [c.line_idx for c in analysis.components]
  mode = (sort_by or "feeder").strip().lower()
  if mode == "feeder":
    analysis.components.sort(
        key=lambda c: (
            int(c.feeder_id) if c.feeder_id.isdigit() else 999,
            _natural_designator_key(c.name),
        )
    )
    label = "Feeder # (then Designator)"
  elif mode == "footprint":
    analysis.components.sort(
        key=lambda c: (
            c.footprint.upper(),
            c.nozzle,
            int(c.feeder_id) if c.feeder_id.isdigit() else 999,
            _natural_designator_key(c.name),
        )
    )
    label = "Footprint & Head (then Feeder #)"
  else:
    analysis.components.sort(key=lambda c: _natural_designator_key(c.name))
    label = "Natural Designator (C1, C2, R1...)"

  for comp, l_idx in zip(analysis.components, line_indices):
    comp.line_idx = l_idx
  return f"Sorted {len(analysis.components)} components by {label}."


def mirror_bottom_side_x(
    analysis: NeoDenCSVAnalysis, board_width_x: float
) -> str:
  """
  Mirrors all component X coordinates across board_width_x (X_new = board_width_x - X)
  and flips component horizontal orientation for mounting bottom-side PCBs when the
  zero origin is at the lower-left corner of the board.
  """
  if board_width_x <= 0.0:
    raise ValueError("Board Width (X) must be greater than 0 mm to mirror X coordinates.")
  for comp in analysis.components:
    comp.x = round(board_width_x - comp.x, 6)
    comp.rotation = wrap_neoden_angle(180.0 - comp.rotation)
  return (
      f"Mirrored X coordinates across {board_width_x:.2f} mm board width for"
      f" bottom-side mounting ({len(analysis.components)} components)."
  )


def format_neoden_yy1_lines(
    analysis: NeoDenCSVAnalysis,
    unit_length: Optional[float] = None,
    unit_width: Optional[float] = None,
    rows: Optional[int] = None,
    columns: Optional[int] = None,
    fid_x: Optional[float] = None,
    fid_y: Optional[float] = None,
    offset_x: Optional[float] = None,
    offset_y: Optional[float] = None,
) -> List[str]:
  """
  Generates a complete NeoDen YY1 Working CSV ('NEODEN,YY1,P&P FILE') matching
  the exact 13-column structure produced and expected by the NeoDen YY1 machine
  (as seen in DACdestroyers.csv):
    - Every header and blank separator row has 13 columns (12 commas).
    - Skip is translated from internal 'No'/'Yes' to YY1 '0' (place) / '1' (skip).
    - Head is clamped to '1' or '2' because the YY1 has 2 physical placement heads.
    - Commas inside Comment or Footprint strings are stripped so columns never shift.
  """
  u_len = unit_length if unit_length is not None else analysis.yy1_unit_length
  u_wid = unit_width if unit_width is not None else analysis.yy1_unit_width
  u_rows = max(1, int(rows if rows is not None else analysis.yy1_rows))
  u_cols = max(1, int(columns if columns is not None else analysis.yy1_columns))
  fx = fid_x if fid_x is not None else analysis.yy1_fid_x
  fy = fid_y if fid_y is not None else analysis.yy1_fid_y
  ox = offset_x if offset_x is not None else analysis.yy1_offset_x
  oy = offset_y if offset_y is not None else analysis.yy1_offset_y

  out: List[str] = [
      "NEODEN,YY1,P&P FILE,,,,,,,,,,",
      ",,,,,,,,,,,,",
      (
          f"PanelizedPCB,UnitLength,{format_yy1_float(u_len, 4)},"
          f"UnitWidth,{format_yy1_float(u_wid, 4)},"
          f"Rows,{u_rows},Columns,{u_cols},,,,"
      ),
      ",,,,,,,,,,,,",
      (
          f"Fiducial,1-X,{format_yy1_float(fx, 4)},1-Y,{format_yy1_float(fy, 4)},"
          f"OverallOffsetX,{format_yy1_float(ox, 4)},"
          f"OverallOffsetY,{format_yy1_float(oy, 4)},,,,"
      ),
      ",,,,,,,,,,,,",
  ]

  # Write the 4 NozzleChange configuration lines
  n_changes = list(analysis.yy1_nozzle_changes[:4])
  while len(n_changes) < 4:
    n_changes.append(YY1NozzleChange())
  for nc in n_changes:
    out.append(
        f"NozzleChange,{nc.enabled},BeforeComponent,{nc.before_component},"
        f"{nc.head},Drop,{nc.drop_station},PickUp,{nc.pickup_station},,,,"
    )

  out.append(",,,,,,,,,,,,")
  out.append(
      "Designator,Comment,Footprint,Mid X,Mid Y,Rotation,Head,FeederNo,"
      "Mount Speed (%),Pick Height(mm),Place Height(mm),Mode,Skip"
  )

  for comp in analysis.components:
    des = sanitize_csv_token(comp.name)
    comm = sanitize_csv_token(comp.value)
    fp = sanitize_csv_token(comp.footprint)
    mid_x = format_yy1_float(comp.new_x, 6)
    mid_y = format_yy1_float(comp.new_y, 6)
    rot_s = format_yy1_float(wrap_neoden_angle(comp.new_rotation), 4)
    head_s, _ = clamp_yy1_head(comp.nozzle)
    feeder_s = sanitize_csv_token(comp.feeder_id) or "1"
    speed_s = str(max(1, min(100, int(comp.mount_speed))))
    pick_s = format_yy1_float(comp.pick_height, 4)
    place_s = format_yy1_float(comp.place_height, 4)
    mode_s = str(max(0, min(4, int(comp.yy1_mode))))
    # Critical NeoDen YY1 difference: Skip=0 means Place, Skip=1 means Skip!
    yy1_skip = "1" if comp.skip == "Yes" else "0"

    out.append(
        f"{des},{comm},{fp},{mid_x},{mid_y},{rot_s},{head_s},{feeder_s},"
        f"{speed_s},{pick_s},{place_s},{mode_s},{yy1_skip}"
    )

  return out


def format_neoden_yy1_import_lines(analysis: NeoDenCSVAnalysis) -> List[str]:
  """
  Generates the 11-column NeoDen YY1 SD-Card Import Template CSV documented by
  Neoden USA Support ('Designator,Footprint,Mid X,Mid Y,Ref X,Ref Y,Pad X,Pad Y,
  Layer,Rotation,Comment'), where Mid X/Y are repeated 3 times (Mid, Ref, Pad)
  and skipped rows are omitted or Layer-tagged.
  """
  out: List[str] = [
      "Designator,Footprint,Mid X,Mid Y,Ref X,Ref Y,Pad X,Pad Y,Layer,Rotation,Comment"
  ]
  for comp in analysis.components:
    des = sanitize_csv_token(comp.name)
    fp = sanitize_csv_token(comp.footprint) or sanitize_csv_token(comp.value)
    comm = sanitize_csv_token(comp.value)
    mx = format_yy1_float(comp.new_x, 4)
    my = format_yy1_float(comp.new_y, 4)
    rot_s = format_yy1_float(wrap_neoden_angle(comp.new_rotation), 4)
    layer = (comp.layer or "T").strip().upper()
    if layer not in ("T", "B"):
      layer = "T"
    out.append(
        f"{des},{fp},{mx},{my},{mx},{my},{mx},{my},{layer},{rot_s},{comm}"
    )
  return out


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
    target_format: str = "neoden4",
    yy1_coord_mode: str = "preserve",
    yy1_unit_length: Optional[float] = None,
    yy1_unit_width: Optional[float] = None,
    yy1_rows: Optional[int] = None,
    yy1_columns: Optional[int] = None,
    yy1_fid_x: Optional[float] = None,
    yy1_fid_y: Optional[float] = None,
    yy1_offset_x: Optional[float] = None,
    yy1_offset_y: Optional[float] = None,
) -> List[str]:
  """
  Applies coordinate transformations, board rotation, YY1 coordinate-sign
  protections, and skip settings to all components, and returns the formatted
  CSV lines for the requested target_format:
    - 'neoden4'           : NeoDen 4 Job CSV ('#Feeder' / 'stack' / 'comp')
    - 'neoden_yy1'        : NeoDen YY1 Working CSV ('NEODEN,YY1,P&P FILE')
    - 'neoden_yy1_import' : NeoDen YY1 11-column Import Template CSV
  Why yy1_coord_mode exists:
  KiCad exports often have negative Y coordinates (when the origin is at the top-left
  of the schematic/layout sheet), whereas the NeoDen YY1 manual states that
  coordinates must be positive. Supporting:
    - 'preserve'      : Keep coordinates as calculated (matches DACdestroyers.csv)
    - 'invert_neg_y'  : Flip negative Y coordinates to positive (-Y -> +Y)
    - 'shift_positive': Shift the board's minimum X/Y to +5.0 mm margin if negative
  prevents edge-case coordinate errors on the YY1 while letting users preserve raw
  coordinates when desired.
  """
  if not analysis.components:
    raise ValueError("No component rows found in the file.")

  if skip_mode != "custom" or custom_skips:
    apply_skip_mode(analysis, skip_mode, custom_skips)

  c1_orig_x = analysis.components[0].x
  c1_orig_y = analysis.components[0].y

  # Determine whether we are keeping raw/original coordinates (e.g. Stage 1 KiCad
  # conversion or unmodified YY1 coordinates) vs. shifting/rotating around SMD1.
  has_p1_override = (
      panel1_x is not None
      and panel1_y is not None
      and (
          not math.isclose(panel1_x, c1_orig_x, abs_tol=1e-4)
          or not math.isclose(panel1_y, c1_orig_y, abs_tol=1e-4)
      )
  )
  is_raw_passthrough = (
      analysis.source_format in ("kicad_pos", "neoden_yy1", "neoden_yy1_import")
      and not has_p1_override
      and math.isclose(board_angle_deg, 0.0, abs_tol=1e-7)
  )

  if is_raw_passthrough:
    for comp in analysis.components:
      comp.new_x = comp.x
      comp.new_y = comp.y
      comp.new_rotation = wrap_neoden_angle(comp.rotation)
  else:
    if not analysis.panels and (panel1_x is None or panel1_y is None):
      target_p1_x, target_p1_y = c1_orig_x, c1_orig_y
    else:
      target_p1_x = panel1_x if panel1_x is not None else analysis.panels[0].x
      target_p1_y = panel1_y if panel1_y is not None else analysis.panels[0].y

    theta_rad = math.radians(board_angle_deg)
    cos_t = math.cos(theta_rad)
    sin_t = math.sin(theta_rad)

    # Rotate every component's CAD offset (dx, dy) from the 1st component (SMD1)
    # by board_angle_deg and translate to the physical Panel 1 SMD1 origin.
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

  # Apply optional YY1 coordinate-sign normalization (edge-case protection for
  # negative KiCad Y coordinates or negative quadrant offsets):
  if yy1_coord_mode == "invert_neg_y":
    for comp in analysis.components:
      if comp.new_y < 0.0:
        comp.new_y = -comp.new_y
      if comp.new_x < 0.0:
        comp.new_x = -comp.new_x
  elif yy1_coord_mode == "shift_positive":
    min_x = min(c.new_x for c in analysis.components)
    min_y = min(c.new_y for c in analysis.components)
    shift_x = (5.0 - min_x) if min_x < 0.0 else 0.0
    shift_y = (5.0 - min_y) if min_y < 0.0 else 0.0
    if shift_x != 0.0 or shift_y != 0.0:
      for comp in analysis.components:
        comp.new_x += shift_x
        comp.new_y += shift_y

  # If the user selected NeoDen YY1 output, format and return the YY1 lines
  if target_format == "neoden_yy1":
    return format_neoden_yy1_lines(
        analysis,
        unit_length=yy1_unit_length,
        unit_width=yy1_unit_width,
        rows=yy1_rows,
        columns=yy1_columns,
        fid_x=yy1_fid_x,
        fid_y=yy1_fid_y,
        offset_x=yy1_offset_x,
        offset_y=yy1_offset_y,
    )
  if target_format == "neoden_yy1_import":
    return format_neoden_yy1_import_lines(analysis)

  # Otherwise format for NeoDen 4 ('neoden4'):
  out_lines = list(analysis.raw_lines)
  target_p1_x = analysis.components[0].new_x
  target_p1_y = analysis.components[0].new_y
  p2_x_val = analysis.panels[1].x if len(analysis.panels) >= 2 else target_p1_x
  p2_y_val = analysis.panels[1].y if len(analysis.panels) >= 2 else target_p1_y

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

  for comp in analysis.components:
    out_lines[comp.line_idx] = (
        f"comp,{comp.feeder_id},{comp.nozzle},{comp.name},{comp.value},"
        f"{comp.footprint},{comp.new_x:.2f},{comp.new_y:.2f},"
        f"{comp.new_rotation:.2f},{comp.skip},"
    )

  return out_lines


def save_neoden_csv(filepath: str, lines: List[str]) -> None:
  """
  Writes CSV lines in binary mode ('wb') with explicit Windows CRLF (\\r\\n) and
  no UTF-8 BOM.
  Why binary mode is used:
  Both NeoDen 4 and NeoDen YY1 embedded controllers expect Windows CRLF line
  endings and pure ASCII text without a 3-byte UTF-8 Byte Order Mark (BOM).
  Writing in binary mode guarantees identical CRLF output on macOS, Linux, and
  Windows without Python's text-mode newline translation doubling '\\r\\r\\n'.
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
    <h1>NeoDen 4 &amp; NeoDen YY1 CSV &amp; KiCad .pos Utility (v6.0) — Help &amp; User Guide</h1>
    <p>Complete reference for KiCad / NeoDen 4 / NeoDen YY1 conversion, fiducial rotation math, manual SMD1 tweaking, YY1 edge-case protections, and batch component editing.</p>
  </div>
  <div class="body">
    <h2>1. Supported Workflows (NeoDen 4 &amp; NeoDen YY1)</h2>
    <div class="step-card">
      <h3>Workflow A: Convert KiCad <code>.pos</code> or NeoDen 4 CSV &rarr; NeoDen YY1 CSV</h3>
      <ul>
        <li>Click <strong>Choose File / Browse</strong> in Section 1 and select a KiCad <code>.pos</code> CSV, a NeoDen 4 Job CSV, or an existing NeoDen YY1 CSV.</li>
        <li>In Section 1, set <strong>Output Machine / Format</strong> to <strong>NeoDen YY1 Working File (<code>NEODEN,YY1,P&P FILE</code>)</strong> (or the 11-column YY1 SD-card Import Template).</li>
        <li>Configure <strong>NeoDen YY1 Settings</strong> (UnitLength, UnitWidth, Rows, Columns, Fiducial 1-X/1-Y, OverallOffsetX/Y, and Coordinate Sign Mode).</li>
        <li>If your KiCad file has negative Y coordinates (top-left page origin), choose <strong>Invert Negative Y (<code>-Y &rarr; +Y</code>)</strong> or <strong>Shift Bounding Box Positive</strong> so the YY1 machine never rejects negative coordinates.</li>
        <li>Use <strong>Section 4 (Batch Modify)</strong> to adjust Feeder numbers (1&ndash;52 Tape, 53&ndash;80 Flexible/Stick/Tray, 81&ndash;99 Bulk), Head (1&ndash;2), Mount Speed (%), Pick/Place Height (mm), or Vision Mode (0&ndash;4), then click <strong>EXPORT NEODEN YY1 CSV</strong>.</li>
      </ul>
    </div>
    <div class="step-card">
      <h3>Workflow B: Two-Stage KiCad <code>.pos</code> &rarr; NeoDen 4 Machine &rarr; Final Aligned CSV</h3>
      <ul>
        <li><strong>Stage 1 (Ingest KiCad <code>.pos</code> CSV)</strong>: Converts verbose KiCad footprints into short NeoDen codes (<code>0805</code>, <code>0603</code>, <code>SOT23</code>, <code>SOP24</code>), filters through-hole/mechanical parts, moves <code>FID*</code> rows into <code>mark</code>, and exports an initial NeoDen 4 CSV (or converts directly to NeoDen YY1).</li>
        <li><strong>On the NeoDen 4 Machine</strong>: Load the Stage 1 CSV, align physical <code>Panel 1 / SMD1</code> (and <code>Panel 2</code> if mounting 2 boards) and physical fiducials using the camera, and export the updated CSV to USB.</li>
        <li><strong>Stage 2 (Ingest NeoDen 4 CSV)</strong>: Load the machine CSV, verify the automatic orthogonal fiducial angle and SMD1 coordinates, and export the final aligned NeoDen 4 CSV (or convert the aligned coordinates to NeoDen YY1!).</li>
      </ul>
    </div>

    <h2>2. Format Comparison &amp; Edge Cases Handled Automatically</h2>
    <table>
      <thead>
        <tr>
          <th style="width:22%;">Feature / Rule</th>
          <th style="width:26%;">KiCad <code>.pos</code> CSV</th>
          <th style="width:26%;">NeoDen 4 Job CSV</th>
          <th style="width:26%;">NeoDen YY1 Working CSV</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Header Sections</strong></td>
          <td>Single header row (<code>Ref,Val,Package,PosX,PosY,Rot,Side</code>)</td>
          <td><code>#Feeder</code>, <code>stack</code>, <code>pcb</code>, <code>mark</code>, <code>mirror_create</code>, <code>mirror</code>, <code>#SMD</code></td>
          <td><code>NEODEN,YY1,P&amp;P FILE</code>, <code>PanelizedPCB</code>, <code>Fiducial</code>, 4&times;<code>NozzleChange</code> (13 cols / 12 commas per line)</td>
        </tr>
        <tr>
          <td><strong>Skip Column Polarity</strong></td>
          <td>N/A (all exported rows included)</td>
          <td><code>No</code> = Place, <code>Yes</code> = Skip</td>
          <td><code>0</code> = Place, <code>1</code> = Skip (auto-translated!)</td>
        </tr>
        <tr>
          <td><strong>Heads / Nozzles</strong></td>
          <td>N/A</td>
          <td>Nozzles <code>1</code>&ndash;<code>4</code></td>
          <td>Heads <code>1</code>&ndash;<code>2</code> (Nozzles 3&ndash;4 auto-clamped to 1&ndash;2)</td>
        </tr>
        <tr>
          <td><strong>Coordinate Sign &amp; Angles</strong></td>
          <td><code>PosY</code> often negative; <code>Rot</code> can be <code>0..360&deg;</code></td>
          <td>Positive machine mm; angles wrapped to <code>[-180&deg;, +180&deg;]</code></td>
          <td>Manual requires positive mm (optional <code>-Y &rarr; +Y</code> or positive shift); angles <code>[-180&deg;, +180&deg;]</code></td>
        </tr>
        <tr>
          <td><strong>Feeders &amp; Vision Modes</strong></td>
          <td>Unassigned</td>
          <td>Feeders defined in <code>stack</code> rows</td>
          <td>Feeders <code>1&ndash;52</code> Tape, <code>53&ndash;80</code> Flex/Tray, <code>81&ndash;99</code> Bulk; Mode <code>0</code>=Null, <code>1</code>=Camera, <code>2</code>=Vac, <code>3</code>=Cam+Vac, <code>4</code>=Big IC</td>
        </tr>
      </tbody>
    </table>

    <h2>3. Guide to Each Control Section</h2>
    <table>
      <thead>
        <tr>
          <th style="width:26%;">Section / Control</th>
          <th>What It Does &amp; How to Use It</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>1. Input File &amp; Output Format</strong></td>
          <td>Browse a KiCad <code>.pos</code>, NeoDen 4 <code>.csv</code>, or NeoDen YY1 <code>.csv</code>, and select whether to export for <strong>NeoDen 4</strong>, <strong>NeoDen YY1 Working File</strong>, or <strong>NeoDen YY1 11-Col Import File</strong>.</td>
        </tr>
        <tr>
          <td><strong>NeoDen YY1 Settings</strong></td>
          <td>Configure <code>PanelizedPCB</code> (UnitLength, UnitWidth, Rows, Columns), <code>Fiducial</code> (1-X, 1-Y, OverallOffsetX, OverallOffsetY), and Coordinate Sign Handling (Preserve, Invert Negative Y, or Shift Positive).</td>
        </tr>
        <tr>
          <td><strong>2. Manual Panel 1 / SMD1 &amp; Panel 2 Tweaking</strong></td>
          <td>Sets physical machine coordinates of the 1st component (<code>SMD1</code>) with <code>-0.1</code> / <code>+0.1</code> mm nudge buttons and resets bogus <code>mirror_create</code> angles.</td>
        </tr>
        <tr>
          <td><strong>3. Fiducial Rotation &amp; Global Skip Toggles</strong></td>
          <td>Displays active vs. excluded fiducials, computes board tilt using orthogonal corner / outlier rejection, and provides one-click global Skip toggles.</td>
        </tr>
        <tr>
          <td><strong>4. Batch Modify Components (Undo/Redo)</strong></td>
          <td>Batch-update <code>Feeder</code>, <code>Footprint</code>, <code>Nozzle / Head</code>, <code>Mount Speed (%)</code>, <code>Pick Height</code>, <code>Place Height</code>, or <code>YY1 Vision Mode (0-4)</code> using row selectors like <code>1-12, 15</code> or <code>all</code>.</td>
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
  .tab-bar {
    display: flex;
    gap: 6px;
    border-bottom: 2px solid #cbd5e1;
    padding-bottom: 6px;
    margin-bottom: 2px;
  }
  .tab-btn {
    flex: 1;
    padding: 6px 10px;
    font-size: 12px;
    font-weight: 700;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #e2e8f0;
    color: #334155;
    cursor: pointer;
    text-align: center;
  }
  .tab-btn.active {
    background: #1d4ed8;
    color: #ffffff;
    border-color: #1e3a8a;
    box-shadow: 0 1px 3px rgba(29, 78, 216, 0.25);
  }
  .adv-note {
    font-size: 11px;
    color: #475569;
    line-height: 1.4;
    margin-bottom: 6px;
  }
</style>
</head>
<body>
<div class="app-shell">
  <div class="titlebar">
    <h1>NeoDen 4 &amp; NeoDen YY1 CSV &amp; KiCad .pos Utility</h1>
    <div class="titlebar-right">
      <a href="/help" target="_blank" rel="noopener" class="help-box" title="Opens a detailed guide in a new tab">
        &#128214; Help &amp; Workflow Guide (Opens in New Tab)
      </a>
      <span class="badge">v6.0</span>
    </div>
  </div>

  <div class="workspace">
    <!-- LEFT COLUMN: All Settings & Controls (Tabbed: Main Controls vs. Advanced Settings) -->
    <div class="left-pane">
      <!-- Top Tab Switcher -->
      <div class="tab-bar">
        <button type="button" id="tabBtnMain" class="tab-btn active" onclick="switchTab('main')">
          &#128295; Main Controls
        </button>
        <button type="button" id="tabBtnAdv" class="tab-btn" onclick="switchTab('adv')">
          &#9881;&#65039; Advanced Settings (Seldom Used)
        </button>
      </div>

      <!-- ==================== TAB 1: MAIN CONTROLS ==================== -->
      <div id="mainTabPane" style="display:flex;flex-direction:column;gap:10px;">
        <!-- Section 1: Browse File & Select Target Output Format (2 everyday targets by default) -->
        <fieldset>
          <legend>1. Input File &amp; Target Output Machine Format</legend>
          <div class="row">
            <label for="fileInput" style="font-weight:600;width:110px;">Browse File:</label>
            <input type="file" id="fileInput" accept=".csv,.pos,text/csv" style="font-size:12px;flex:1;">
          </div>
          <div class="row" style="margin-top:6px;">
            <label for="targetFormat" style="font-weight:600;width:110px;">Convert / Export To:</label>
            <select id="targetFormat" onchange="onTargetFormatChange()" style="flex:1;font-weight:600;">
              <option value="neoden4">1. NeoDen 4 Job CSV (Full machine file: #Feeder / stack / comp)</option>
              <option value="neoden_yy1">2. NeoDen YY1 Working CSV (Full machine file: NEODEN,YY1,P&amp;P FILE)</option>
              <option value="neoden_yy1_import" id="optTarget3" style="display:none;" disabled>3. NeoDen YY1 11-Col Import Template (SD-Card Bare Import)</option>
            </select>
          </div>
          <div id="targetFormatHelp" style="margin-top:6px;font-size:11px;line-height:1.4;background:#f1f5f9;border-left:3px solid #2563eb;padding:5px 8px;border-radius:2px;color:#334155;">
            <strong>NeoDen 4 Job CSV:</strong> Full production file for the NeoDen 4 machine (4 nozzles, stack feeder table, and SMD1 mirror coordinates).
          </div>
        </fieldset>

        <!-- KiCad Stage 1 Banner (Shown when a KiCad .pos file is loaded & NeoDen 4 is selected) -->
        <div id="kicadStage1Card" class="kicad-stage1-card">
          <h3>KiCad .pos File Ingested &mdash; Stage 1: Convert for NeoDen 4 or NeoDen YY1</h3>
          <div>You can export an initial <strong>NeoDen 4 CSV</strong> (to align Fiducials &amp; SMD1 on the NeoDen 4), or switch <strong>Convert / Export To</strong> above to <strong>NeoDen YY1 Working CSV</strong> to convert directly for the YY1!</div>
          <ol>
            <li>For <strong>NeoDen 4</strong>: Click <strong>CONVERT &amp; EXPORT INITIAL NEODEN 4 CSV</strong>, align physical Fiducials &amp; SMD1 on the machine, and load the exported CSV back here for Stage 2.</li>
            <li>For <strong>NeoDen YY1</strong>: Select <strong>2. NeoDen YY1 Working CSV</strong> in Section 1 above to reveal YY1 Panel, Fiducial, Coordinate Sign, and Batch Edit settings.</li>
          </ol>
        </div>

        <!-- NeoDen YY1 Core Settings Card (Shown whenever Target Format is YY1 or Input is YY1) -->
        <fieldset id="yy1SettingsBox" style="display:none;border-color:#3b82f6;background:#f8fafc;">
          <legend style="color:#1d4ed8;">NeoDen YY1 Machine &amp; Coordinate Sign Settings</legend>
          <div class="row">
            <label style="width:92px;font-weight:600;">UnitLength (X):</label>
            <input type="number" step="0.01" id="yy1UnitLen" style="width:74px;" onchange="recalculate()">
            <label style="margin-left:4px;font-weight:600;">UnitWidth (Y):</label>
            <input type="number" step="0.01" id="yy1UnitWid" style="width:74px;" onchange="recalculate()">
          </div>
          <div class="row">
            <label style="width:92px;font-weight:600;">Panel Rows:</label>
            <input type="number" step="1" min="1" id="yy1Rows" style="width:74px;" onchange="recalculate()">
            <label style="margin-left:4px;font-weight:600;">Panel Cols:</label>
            <input type="number" step="1" min="1" id="yy1Cols" style="width:74px;" onchange="recalculate()">
          </div>
          <div class="row">
            <label style="width:92px;font-weight:600;">Fiducial 1-X:</label>
            <input type="number" step="0.01" id="yy1FidX" style="width:74px;" onchange="recalculate()">
            <label style="margin-left:4px;font-weight:600;">Fiducial 1-Y:</label>
            <input type="number" step="0.01" id="yy1FidY" style="width:74px;" onchange="recalculate()">
          </div>
          <div class="row" style="margin-top:4px;">
            <label for="yy1CoordMode" style="width:110px;font-weight:600;" title="The YY1 manual requires positive coordinates; KiCad often outputs negative Y.">Coord Sign Rule:</label>
            <select id="yy1CoordMode" onchange="recalculate()" style="flex:1;">
              <option value="preserve">Preserve Raw / Aligned Coordinates (As-Is)</option>
              <option value="invert_neg_y">Invert Negative Coords (-Y &rarr; +Y, -X &rarr; +X)</option>
              <option value="shift_positive">Shift Bounding Box to Positive (+5 mm margin)</option>
            </select>
          </div>
        </fieldset>

        <!-- NeoDen Settings Wrapper (Shown for NeoDen 4 Stage 2, YY1 files, or when YY1 output is selected) -->
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
          </fieldset>

          <!-- Section 3: Fiducial Rotation & Global Skip Toggles -->
          <fieldset>
            <legend>3. Fiducial Rotation &amp; Global Skip Toggles (mark)</legend>
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
                <option value="manual">Manual Angle Override (&deg;)</option>
                <option value="sq45">2-Fiducial Square Diagonal (45&deg; &mdash; Advanced)</option>
                <option value="rect2">2-Fiducial Rectangular Diagonal (CAD &Delta;X, &Delta;Y &mdash; Advanced)</option>
              </select>
            </div>
            <div class="row">
              <label for="boardAngle" style="width:110px;">Board Angle (&deg;CCW):</label>
              <input type="number" step="0.0001" id="boardAngle" readonly disabled onchange="recalculate()" title="Locked in Auto mode. Select 'Manual Angle Override' above to edit manually.">
            </div>
            <div class="row" style="margin-top:6px;padding-top:6px;border-top:1px dashed #cbd5e1;">
              <label style="width:95px;font-weight:700;color:#0f172a;">Toggle Skips:</label>
              <button type="button" class="skip-btn active-no" onclick="setSkipMode('all_no')">All Skips OFF (Place)</button>
              <button type="button" class="skip-btn active-yes" onclick="setSkipMode('all_yes')">All Skips ON (Skip)</button>
              <button type="button" class="skip-btn" onclick="setSkipMode('first_no')">1st Only</button>
              <button type="button" class="skip-btn" onclick="setSkipMode('original')">Default</button>
            </div>
          </fieldset>

          <!-- Section 4: Batch Modify (Feeder, Footprint, Nozzle/Head, YY1 Speed/Height/Mode) -->
          <fieldset>
            <legend>4. Batch Modify Components (Feeder / Footprint / Head / YY1 Params)</legend>
            <div class="row">
              <label for="batchField" style="width:55px;font-weight:600;">Modify:</label>
              <select id="batchField" style="max-width:155px;">
                <option value="feeder">Feeder / FeederNo</option>
                <option value="footprint">Footprint</option>
                <option value="nozzle">Nozzle / Head (1-4 / 1-2)</option>
                <option value="speed">YY1 Mount Speed (%)</option>
                <option value="pick_height">YY1 Pick Height (mm)</option>
                <option value="place_height">YY1 Place Height (mm)</option>
                <option value="mode">YY1 Vision Mode (0-4)</option>
              </select>
              <label for="batchValue" style="font-weight:600;">New Value/#:</label>
              <input type="text" id="batchValue" placeholder="e.g. 81 or 0805" style="width:95px;">
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
      </div>

      <!-- ==================== TAB 2: ADVANCED & SELDOM-USED SETTINGS ==================== -->
      <div id="advTabPane" style="display:none;flex-direction:column;gap:10px;">
        <!-- A. Hidden Target 3: YY1 11-Col Bare SD-Card Import Template -->
        <fieldset style="border-color:#94a3b8;">
          <legend>A. Hidden Target 3 &mdash; YY1 11-Column Bare Import Template</legend>
          <div class="adv-note">
            Only needed if you want the YY1 touchscreen to force manual from-scratch feeder &amp; height teaching on the machine instead of using a complete Working File.
          </div>
          <div class="check-row" style="margin-bottom:6px;">
            <input type="checkbox" id="unlockTarget3" onchange="toggleTarget3Unlock()">
            <label for="unlockTarget3">Show <strong>Target 3 (YY1 11-Col Import Template)</strong> in Main Dropdown</label>
          </div>
          <button type="button" onclick="exportTarget3Directly()" style="width:100%;background:#f1f5f9;border-color:#64748b;">
            Export YY1 11-Col Bare Import Template (.csv) Now
          </button>
        </fieldset>

        <!-- B. Smart Feeder Grouping, Placement Sorting & Bottom-Side Mirroring -->
        <fieldset style="border-color:#94a3b8;">
          <legend>B. Smart Feeder Auto-Assignment, Sorting &amp; Bottom-Layer Mirror</legend>
          <div class="adv-note">
            One-click power utilities (all actions support <strong>Undo / Redo</strong>):
          </div>
          <div class="row">
            <label style="width:115px;font-weight:600;">Start Feeder #:</label>
            <select id="autoFeederStart" style="width:165px;">
              <option value="1">1 (YY1 Tape Feeders 1&ndash;52)</option>
              <option value="53">53 (YY1 Stick/Tray 53&ndash;80)</option>
              <option value="81" selected>81 (YY1 Bulk Feeders 81&ndash;99)</option>
            </select>
            <input type="checkbox" id="autoAltHeads" checked title="Alternate Head 1 & Head 2">
            <label for="autoAltHeads" style="font-size:11px;">Alt Heads 1/2</label>
            <button type="button" onclick="runAdvancedAction('auto_feeders')" style="background:#dbeafe;border-color:#3b82f6;color:#1e3a8a;">Auto-Assign</button>
          </div>
          <div class="row" style="margin-top:6px;padding-top:6px;border-top:1px dashed #cbd5e1;">
            <label style="width:115px;font-weight:600;">Sort Components:</label>
            <button type="button" onclick="runAdvancedAction('sort', 'feeder')">By Feeder #</button>
            <button type="button" onclick="runAdvancedAction('sort', 'footprint')">By Footprint/Head</button>
            <button type="button" onclick="runAdvancedAction('sort', 'designator')">By Designator</button>
          </div>
          <div class="row" style="margin-top:6px;padding-top:6px;border-top:1px dashed #cbd5e1;">
            <label style="width:115px;font-weight:600;" title="X_new = BoardWidth - X">Bottom-Side PCB:</label>
            <input type="number" step="0.01" id="mirrorWidthX" placeholder="Board Width X (mm)" style="width:125px;">
            <button type="button" onclick="runAdvancedAction('mirror_x')">Mirror X Coords (X = W &minus; X)</button>
          </div>
          <div id="advActionFeedback" style="font-size:11px;color:#1d4ed8;font-weight:600;margin-top:4px;"></div>
        </fieldset>

        <!-- C. YY1 Overall Offsets & Auto-Nozzle Change Programming -->
        <fieldset style="border-color:#94a3b8;">
          <legend>C. YY1 Global Offsets &amp; Auto-Nozzle Change Station (Slots 1&ndash;4)</legend>
          <div class="row">
            <label style="width:125px;font-weight:600;">OverallOffset X / Y:</label>
            <input type="number" step="0.01" id="yy1OffX" style="width:74px;" value="0.00" onchange="recalculate()">
            <input type="number" step="0.01" id="yy1OffY" style="width:74px;" value="0.00" onchange="recalculate()">
          </div>
          <div class="adv-note" style="margin-top:4px;">
            Configure automatic nozzle swaps (<code>NozzleChange</code> rows in YY1 Working CSV):
          </div>
          <div id="yy1NozzleChangeContainer" style="display:flex;flex-direction:column;gap:4px;font-size:11px;">
            <div class="row">
              <span style="width:42px;font-weight:700;">Slot 1:</span>
              <select id="ncEn0" style="width:58px;" onchange="recalculate()"><option value="OFF">OFF</option><option value="ON">ON</option></select>
              <label>Before#:</label><input type="number" id="ncBef0" value="1" min="1" style="width:48px;" onchange="recalculate()">
              <select id="ncHd0" style="width:68px;" onchange="recalculate()"><option value="Head1">Head1</option><option value="Head2">Head2</option></select>
              <label>Drop:</label><input type="text" id="ncDrp0" value="Station1" style="width:68px;" onchange="recalculate()">
              <label>Pick:</label><input type="text" id="ncPck0" value="Station1" style="width:68px;" onchange="recalculate()">
            </div>
            <div class="row">
              <span style="width:42px;font-weight:700;">Slot 2:</span>
              <select id="ncEn1" style="width:58px;" onchange="recalculate()"><option value="OFF">OFF</option><option value="ON">ON</option></select>
              <label>Before#:</label><input type="number" id="ncBef1" value="1" min="1" style="width:48px;" onchange="recalculate()">
              <select id="ncHd1" style="width:68px;" onchange="recalculate()"><option value="Head1">Head1</option><option value="Head2">Head2</option></select>
              <label>Drop:</label><input type="text" id="ncDrp1" value="Station1" style="width:68px;" onchange="recalculate()">
              <label>Pick:</label><input type="text" id="ncPck1" value="Station1" style="width:68px;" onchange="recalculate()">
            </div>
          </div>
        </fieldset>

        <!-- D. Seldom-Changed Alignment & Rotation Overrides -->
        <fieldset style="border-color:#94a3b8;">
          <legend>D. Seldom-Changed Alignment &amp; 2-Fiducial Diagonal Overrides</legend>
          <div class="row">
            <label style="width:165px;">2-Fiducial Nominal CAD &Delta;X, &Delta;Y:</label>
            <input type="number" step="0.01" id="cadDx" placeholder="&Delta;X (mm)" style="width:75px;" onchange="onModeChange()">
            <input type="number" step="0.01" id="cadDy" placeholder="&Delta;Y (mm)" style="width:75px;" onchange="onModeChange()">
          </div>
          <div class="check-row">
            <input type="checkbox" id="rotComp" checked onchange="recalculate()">
            <label for="rotComp">
              Add board rotation to component <code>Rotation</code> column
              <span class="default-tag">(Default: ON)</span>
            </label>
          </div>
          <div class="check-row">
            <input type="checkbox" id="resetMcAngle" checked onchange="recalculate()">
            <label for="resetMcAngle">
              Reset <code>mirror_create</code> Bogus PCB Angle to 0&deg;
              <span class="default-tag">(Default: ON)</span>
            </label>
          </div>
        </fieldset>

        <!-- E. Pre-Flight Diagnostics & Fiducial Math Log -->
        <details class="diag-dropdown" open>
          <summary>E. Pre-Flight Diagnostics &amp; Fiducial Math Log</summary>
          <div id="diagBox" class="diag-box">Browse and select a NeoDen 4 CSV, NeoDen YY1 CSV, or KiCad .pos CSV file to begin.</div>
        </details>
      </div>

      <!-- Action / Export Bar (Always Visible at Bottom of Left Pane) -->
      <div class="footer-bar">
        <div id="statusBar" class="status-pill">No file loaded &mdash; Browse a file in Section 1.</div>
        <button type="button" id="exportBtn" class="primary" onclick="exportCSV()" disabled>START / EXPORT CSV</button>
      </div>
    </div>

    <!-- RIGHT COLUMN: Full Component Preview Table -->
    <div class="right-pane">
      <div class="right-header">
        <h2>Component List Preview</h2>
        <span id="tableSubHeader">Click any row's Skip badge to toggle Place/Skip</span>
      </div>
      <div class="table-wrap">
        <table>
          <colgroup>
            <col style="width:34px;">
            <col style="width:54px;">
            <col style="width:48px;">
            <col style="width:40px;">
            <col style="width:96px;">
            <col style="width:68px;">
            <col style="width:62px;">
            <col style="width:62px;">
            <col style="width:66px;">
            <col style="width:66px;">
            <col style="width:62px;">
            <col style="width:58px;">
            <col style="width:62px;">
          </colgroup>
          <thead>
            <tr>
              <th>#</th>
              <th>Name</th>
              <th>Feeder</th>
              <th>Head</th>
              <th>Value</th>
              <th>Footprint</th>
              <th>Orig X</th>
              <th>Orig Y</th>
              <th>New X</th>
              <th>New Y</th>
              <th>New Rot</th>
              <th>Spd/Mod</th>
              <th>Skip</th>
            </tr>
          </thead>
          <tbody id="compTableBody">
            <tr><td colspan="13" style="padding:28px;color:#64748b;">Browse a NeoDen 4 <code>.csv</code>, NeoDen YY1 <code>.csv</code>, or KiCad <code>.pos</code> file on the left to view components.</td></tr>
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
    alert('File is larger than 5 MB. Please select a valid NeoDen 4, NeoDen YY1, or KiCad .pos CSV file.');
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

function updateTargetFormatHelp() {
  const targetFmt = document.getElementById('targetFormat').value;
  const helpBox = document.getElementById('targetFormatHelp');
  if (!helpBox) return;

  if (targetFmt === 'neoden4') {
    helpBox.innerHTML = '<strong>Target 1 &mdash; NeoDen 4 Job CSV:</strong> Full production file for the <em>NeoDen 4 machine only</em>. Generates 4-nozzle configuration with <code>#Feeder</code>/<code>stack</code> tables, pcb clamp lines, and <code>mirror</code>/<code>mark</code> SMD1 coordinates.';
  } else if (targetFmt === 'neoden_yy1') {
    helpBox.innerHTML = '<strong>Target 2 &mdash; NeoDen YY1 Working CSV:</strong> Full production file for the <em>NeoDen YY1 machine</em>. Produces the complete 13-column project file (<code>NEODEN,YY1,P&amp;P FILE</code>, identical to <code>DACdestroyers.csv</code>) with panel sizes, dual heads (1&ndash;2), feeders (1&ndash;99), pick/place heights, and vision modes (0&ndash;4).';
  } else if (targetFmt === 'neoden_yy1_import') {
    helpBox.innerHTML = '<strong>Target 3 &mdash; NeoDen YY1 11-Col Import Template:</strong> SD-card import template for the <em>NeoDen YY1</em>. Simplified 11-column file (<code>Designator,Footprint,Mid/Ref/Pad X/Y,Layer,Rotation,Comment</code>) for loading raw CAD positions before manually teaching feeders on the YY1 touchscreen.';
  }
}

function onTargetFormatChange() {
  updateTargetFormatHelp();
  updateSectionVisibility();
  if (currentData) {
    recalculate('custom');
  }
}

function updateSectionVisibility() {
  updateTargetFormatHelp();
  const targetFmt = document.getElementById('targetFormat').value;
  const isYY1Target = (targetFmt === 'neoden_yy1' || targetFmt === 'neoden_yy1_import');
  const isKicad = currentData ? (currentData.source_format === 'kicad_pos') : false;
  const isYY1Src = currentData ? (currentData.source_format === 'neoden_yy1' || currentData.source_format === 'neoden_yy1_import') : false;

  const settingsBox = document.getElementById('neodenSettingsContainer');
  const stage1Card = document.getElementById('kicadStage1Card');
  const yy1Box = document.getElementById('yy1SettingsBox');
  const exportBtn = document.getElementById('exportBtn');

  if (yy1Box) {
    yy1Box.style.display = (isYY1Target || isYY1Src) ? 'block' : 'none';
  }

  if (isKicad && !isYY1Target) {
    // Stage 1 KiCad -> NeoDen 4: hide alignment settings until Stage 2, but show Stage 1 instructions
    if (settingsBox) settingsBox.style.display = 'none';
    if (stage1Card) stage1Card.style.display = 'block';
    if (exportBtn) exportBtn.textContent = 'CONVERT & EXPORT INITIAL NEODEN 4 CSV';
  } else {
    if (settingsBox && currentData) settingsBox.style.display = 'flex';
    if (stage1Card) stage1Card.style.display = isKicad ? 'block' : 'none';
    if (exportBtn) {
      if (targetFmt === 'neoden_yy1') {
        exportBtn.textContent = 'CONVERT & EXPORT NEODEN YY1 CSV';
      } else if (targetFmt === 'neoden_yy1_import') {
        exportBtn.textContent = 'EXPORT NEODEN YY1 11-COL IMPORT CSV';
      } else {
        exportBtn.textContent = 'START / EXPORT NEODEN 4 CSV';
      }
    }
  }
}

function applyLoadedState(payload, resetInputs) {
  currentData = payload;
  if (payload.error) {
    document.getElementById('statusBar').textContent = 'Error: ' + payload.error;
    return;
  }

  const exportBtn = document.getElementById('exportBtn');
  exportBtn.disabled = false;

  if (resetInputs) {
    // Default the target format selector based on the uploaded file type
    if (payload.source_format === 'neoden_yy1' || payload.source_format === 'neoden_yy1_import') {
      document.getElementById('targetFormat').value = 'neoden_yy1';
    } else {
      document.getElementById('targetFormat').value = 'neoden4';
    }

    document.getElementById('yy1UnitLen').value = (payload.yy1_unit_length || 53.24).toFixed(2);
    document.getElementById('yy1UnitWid').value = (payload.yy1_unit_width || 78.74).toFixed(2);
    document.getElementById('yy1Rows').value = payload.yy1_rows || 1;
    document.getElementById('yy1Cols').value = payload.yy1_columns || 1;
    document.getElementById('yy1FidX').value = (payload.yy1_fid_x || 0.0).toFixed(2);
    document.getElementById('yy1FidY').value = (payload.yy1_fid_y || 0.0).toFixed(2);
    document.getElementById('yy1OffX').value = (payload.yy1_offset_x || 0.0).toFixed(2);
    document.getElementById('yy1OffY').value = (payload.yy1_offset_y || 0.0).toFixed(2);
    document.getElementById('yy1CoordMode').value = 'preserve';

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

  updateSectionVisibility();

  const fmtLabels = {
    'kicad_pos': 'KiCad .pos CSV',
    'neoden': 'NeoDen 4 CSV',
    'neoden_yy1': 'NeoDen YY1 Working CSV',
    'neoden_yy1_import': 'NeoDen YY1 11-Col Import CSV'
  };
  document.getElementById('cadSmd1Banner').textContent =
    `1st Component (${payload.c1_name} / ${payload.c1_footprint}) Orig: (${payload.c1_x.toFixed(2)}, ${payload.c1_y.toFixed(2)}) mm`;
  document.getElementById('sourceFormatTag').textContent =
    fmtLabels[payload.source_format] || payload.source_format;

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
  const targetFmt = document.getElementById('targetFormat').value;
  const skipHint = (targetFmt === 'neoden_yy1')
    ? 'YY1 Skip: 0=Place (No), 1=Skip (Yes)'
    : 'NeoDen 4 Skip: No=Place, Yes=Skip';
  document.getElementById('tableSubHeader').textContent =
    `${payload.components.length} components (Place=${noCount}, Skip=${yesCount}) — ${skipHint}`;
  document.getElementById('statusBar').textContent =
    `Ready: ${payload.filename} [${fmtLabels[payload.source_format] || payload.source_format}] -> Target: ${targetFmt.toUpperCase()} (${payload.components.length} parts)`;
}

function renderFiducialList(fids) {
  const container = document.getElementById('fidListContainer');
  container.innerHTML = '';
  if (!fids || fids.length === 0) {
    container.innerHTML = '<div style="font-size:11px;color:#64748b;">No fiducials found in mark / Fiducial line.</div>';
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
  lines.push(`File: ${payload.filename} [Source: ${payload.source_format}] | Fiducials: ${payload.fiducials.length}`);
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
  const targetFmt = document.getElementById('targetFormat').value;
  const isYY1Target = (targetFmt === 'neoden_yy1' || targetFmt === 'neoden_yy1_import');
  comps.forEach((c, idx) => {
    const tr = document.createElement('tr');
    const skipClass = c.skip === 'No' ? 'row-skip-no' : 'row-skip-yes';
    const skipDisplay = isYY1Target
      ? (c.skip === 'No' ? '0 (Place)' : '1 (Skip)')
      : c.skip;
    const headDisplay = isYY1Target ? c.yy1_head : c.nozzle;
    const spdMod = `${c.mount_speed || 50}% / M${c.yy1_mode !== undefined ? c.yy1_mode : 1}`;
    tr.innerHTML = `
      <td>${idx + 1}</td>
      <td title="${c.name}"><strong>${c.name}</strong></td>
      <td>${c.feeder_id}</td>
      <td>${headDisplay}</td>
      <td title="${c.value}">${c.value}</td>
      <td title="${c.footprint}">${c.footprint || '&mdash;'}</td>
      <td>${c.old_x.toFixed(2)}</td>
      <td>${c.old_y.toFixed(2)}</td>
      <td class="highlight-new">${c.new_x.toFixed(2)}</td>
      <td class="highlight-new">${c.new_y.toFixed(2)}</td>
      <td class="highlight-new">${c.new_rot.toFixed(2)}°</td>
      <td title="Mount Speed: ${c.mount_speed || 50}%, PickH: ${c.pick_height || 0}mm, PlaceH: ${c.place_height || 0}mm, Mode: ${c.yy1_mode}">${spdMod}</td>
      <td><span class="row-skip-pill ${skipClass}" onclick="toggleRowSkip(${idx})">${skipDisplay}</span></td>
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

function switchTab(tabName) {
  const isAdv = (tabName === 'adv');
  document.getElementById('mainTabPane').style.display = isAdv ? 'none' : 'flex';
  document.getElementById('advTabPane').style.display = isAdv ? 'flex' : 'none';
  document.getElementById('tabBtnMain').classList.toggle('active', !isAdv);
  document.getElementById('tabBtnAdv').classList.toggle('active', isAdv);
}

function toggleTarget3Unlock() {
  const unlocked = document.getElementById('unlockTarget3').checked;
  const opt3 = document.getElementById('optTarget3');
  if (!opt3) return;
  opt3.style.display = unlocked ? '' : 'none';
  opt3.disabled = !unlocked;
  const sel = document.getElementById('targetFormat');
  if (!unlocked && sel.value === 'neoden_yy1_import') {
    sel.value = 'neoden_yy1';
    onTargetFormatChange();
  }
}

async function exportTarget3Directly() {
  if (!currentData) {
    alert('Please browse and select a file in Main Controls first.');
    return;
  }
  const params = collectPreviewParams('custom');
  params.target_format = 'neoden_yy1_import';
  const res = await fetch('/api/export', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(params)
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
  document.getElementById('advActionFeedback').textContent =
    `Exported Target 3 (YY1 11-Col Import): ${data.download_name}`;
}

async function runAdvancedAction(actionType, param = '') {
  if (!currentData) {
    alert('Please browse and select a file in Main Controls first.');
    return;
  }
  const bodyObj = {
    action: actionType,
    param: param,
    start_feeder: parseInt(document.getElementById('autoFeederStart').value, 10) || 81,
    alt_heads: document.getElementById('autoAltHeads').checked,
    mirror_width_x: parseFloat(document.getElementById('mirrorWidthX').value) || parseFloat(document.getElementById('yy1UnitLen').value) || 0.0,
    ...collectPreviewParams('custom')
  };
  const res = await fetch('/api/advanced_action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(bodyObj)
  });
  const payload = await res.json();
  if (payload.error) {
    document.getElementById('advActionFeedback').textContent = 'Error: ' + payload.error;
    return;
  }
  document.getElementById('advActionFeedback').textContent = payload.batch_message || 'Applied advanced action.';
  applyLoadedState(payload, false);
}

function collectPreviewParams(skipMode = 'custom') {
  const targetFmt = document.getElementById('targetFormat').value;
  const isKicadStage1N4 = currentData && currentData.source_format === 'kicad_pos' && targetFmt === 'neoden4';
  const nozzleChanges = [0, 1].map(i => ({
    enabled: document.getElementById('ncEn' + i) ? document.getElementById('ncEn' + i).value : 'OFF',
    before_component: document.getElementById('ncBef' + i) ? (parseInt(document.getElementById('ncBef' + i).value, 10) || 1) : 1,
    head: document.getElementById('ncHd' + i) ? document.getElementById('ncHd' + i).value : 'Head1',
    drop_station: document.getElementById('ncDrp' + i) ? document.getElementById('ncDrp' + i).value : 'Station1',
    pickup_station: document.getElementById('ncPck' + i) ? document.getElementById('ncPck' + i).value : 'Station1'
  }));
  return {
    target_format: targetFmt,
    yy1_coord_mode: document.getElementById('yy1CoordMode').value,
    yy1_unit_length: parseFloat(document.getElementById('yy1UnitLen').value) || 53.24,
    yy1_unit_width: parseFloat(document.getElementById('yy1UnitWid').value) || 78.74,
    yy1_rows: parseInt(document.getElementById('yy1Rows').value, 10) || 1,
    yy1_columns: parseInt(document.getElementById('yy1Cols').value, 10) || 1,
    yy1_fid_x: parseFloat(document.getElementById('yy1FidX').value) || 0.0,
    yy1_fid_y: parseFloat(document.getElementById('yy1FidY').value) || 0.0,
    yy1_offset_x: parseFloat(document.getElementById('yy1OffX').value) || 0.0,
    yy1_offset_y: parseFloat(document.getElementById('yy1OffY').value) || 0.0,
    yy1_nozzle_changes: nozzleChanges,
    rot_mode: document.getElementById('rotMode').value,
    board_angle: isKicadStage1N4 ? 0.0 : (parseFloat(document.getElementById('boardAngle').value) || 0.0),
    p1_x: isKicadStage1N4 ? null : (parseFloat(document.getElementById('p1x').value) || 0.0),
    p1_y: isKicadStage1N4 ? null : (parseFloat(document.getElementById('p1y').value) || 0.0),
    p2_x: (isKicadStage1N4 || document.getElementById('p2x').disabled) ? null : (parseFloat(document.getElementById('p2x').value) || 0.0),
    p2_y: (isKicadStage1N4 || document.getElementById('p2y').disabled) ? null : (parseFloat(document.getElementById('p2y').value) || 0.0),
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
      : `Downloaded ${data.download_name} (CRLF + exact NeoDen commas)`;
}

// Initialize target format explanatory card on page load
updateTargetFormatHelp();
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
  """
  Serializes the current NeoDenCSVAnalysis state into a JSON-friendly dictionary
  for the web UI. Includes both NeoDen 4 and NeoDen YY1 metadata and per-row
  clamped YY1 head numbers so the right-hand preview table updates in real time.
  """
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
      "yy1_unit_length": analysis.yy1_unit_length,
      "yy1_unit_width": analysis.yy1_unit_width,
      "yy1_rows": analysis.yy1_rows,
      "yy1_columns": analysis.yy1_columns,
      "yy1_fid_x": analysis.yy1_fid_x,
      "yy1_fid_y": analysis.yy1_fid_y,
      "yy1_offset_x": analysis.yy1_offset_x,
      "yy1_offset_y": analysis.yy1_offset_y,
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
              "yy1_head": clamp_yy1_head(c.nozzle)[0],
              "value": c.value,
              "footprint": c.footprint,
              "old_x": c.x,
              "old_y": c.y,
              "new_x": c.new_x,
              "new_y": c.new_y,
              "new_rot": c.new_rotation,
              "skip": c.skip,
              "mount_speed": c.mount_speed,
              "pick_height": c.pick_height,
              "place_height": c.place_height,
              "yy1_mode": c.yy1_mode,
          }
          for c in analysis.components[:500]
      ],
  }


def snapshot_components(
    analysis: NeoDenCSVAnalysis,
) -> List[ComponentRow]:
  """
  Captures a deep copy of every ComponentRow so multi-step Undo and Redo work
  across batch edits, feeder auto-assignment, component sorting, and bottom-side
  X-mirroring.
  """
  return copy.deepcopy(analysis.components)


def restore_components_snapshot(
    analysis: NeoDenCSVAnalysis,
    snap: List[ComponentRow],
) -> None:
  """Restores the full component list from an Undo/Redo snapshot."""
  analysis.components = copy.deepcopy(snap)


def launch_web_gui(
    initial_file: Optional[str] = None,
    port: int = 0,
    open_browser: bool = True,
) -> None:
  """
  Launches the local 2-column Rufus-style HTTP GUI in the user's default browser.
  Why a local HTTP server + browser UI is used instead of Tkinter:
  macOS and Linux Python installations often lack Tcl/Tk or render outdated
  X11 widgets. Using Python's built-in http.server requires zero third-party
  pip dependencies while delivering a responsive, high-DPI 2-column interface.
  """
  app_state: Dict[str, Any] = {
      "analysis": None,
      "undo_stack": [],
      "redo_stack": [],
      "last_batch_msg": "",
  }

  if initial_file and os.path.isfile(initial_file):
    analysis = parse_neoden_csv(initial_file)
    default_target = (
        "neoden_yy1"
        if analysis.source_format in ("neoden_yy1", "neoden_yy1_import")
        else "neoden4"
    )
    apply_transformation(
        analysis,
        board_angle_deg=analysis.recommended_angle_deg,
        target_format=default_target,
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

      # Update YY1 header parameters in-place when edited in the UI
      if req.get("yy1_unit_length") is not None:
        analysis.yy1_unit_length = float(req["yy1_unit_length"])
      if req.get("yy1_unit_width") is not None:
        analysis.yy1_unit_width = float(req["yy1_unit_width"])
      if req.get("yy1_rows") is not None:
        analysis.yy1_rows = max(1, int(req["yy1_rows"]))
      if req.get("yy1_columns") is not None:
        analysis.yy1_columns = max(1, int(req["yy1_columns"]))
      if req.get("yy1_fid_x") is not None:
        analysis.yy1_fid_x = float(req["yy1_fid_x"])
      if req.get("yy1_fid_y") is not None:
        analysis.yy1_fid_y = float(req["yy1_fid_y"])
      if req.get("yy1_offset_x") is not None:
        analysis.yy1_offset_x = float(req["yy1_offset_x"])
      if req.get("yy1_offset_y") is not None:
        analysis.yy1_offset_y = float(req["yy1_offset_y"])

      if isinstance(req.get("yy1_nozzle_changes"), list):
        for idx_nc, nc_dict in enumerate(req["yy1_nozzle_changes"][:4]):
          if idx_nc < len(analysis.yy1_nozzle_changes) and isinstance(nc_dict, dict):
            analysis.yy1_nozzle_changes[idx_nc] = YY1NozzleChange(
                enabled=str(nc_dict.get("enabled", "OFF")).upper(),
                before_component=max(1, int(nc_dict.get("before_component", 1))),
                head=sanitize_csv_token(str(nc_dict.get("head", "Head1"))),
                drop_station=sanitize_csv_token(str(nc_dict.get("drop_station", "Station1"))),
                pickup_station=sanitize_csv_token(str(nc_dict.get("pickup_station", "Station1"))),
            )

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
          target_format=req.get("target_format", "neoden4"),
          yy1_coord_mode=req.get("yy1_coord_mode", "preserve"),
          yy1_unit_length=analysis.yy1_unit_length,
          yy1_unit_width=analysis.yy1_unit_width,
          yy1_rows=analysis.yy1_rows,
          yy1_columns=analysis.yy1_columns,
          yy1_fid_x=analysis.yy1_fid_x,
          yy1_fid_y=analysis.yy1_fid_y,
          yy1_offset_x=analysis.yy1_offset_x,
          yy1_offset_y=analysis.yy1_offset_y,
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
          default_target = (
              "neoden_yy1"
              if analysis.source_format in ("neoden_yy1", "neoden_yy1_import")
              else "neoden4"
          )
          apply_transformation(
              analysis,
              board_angle_deg=analysis.recommended_angle_deg,
              target_format=default_target,
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

        if self.path == "/api/advanced_action":
          snap = snapshot_components(analysis)
          act = (req.get("action") or "").strip().lower()
          if act == "auto_feeders":
            msg = auto_assign_feeders_by_part(
                analysis,
                start_feeder=int(req.get("start_feeder", 81)),
                alternate_heads=bool(req.get("alt_heads", True)),
            )
          elif act == "sort":
            msg = sort_components_order(
                analysis, sort_by=str(req.get("param", "feeder"))
            )
          elif act == "mirror_x":
            w_x = float(req.get("mirror_width_x") or analysis.yy1_unit_length)
            msg = mirror_bottom_side_x(analysis, w_x)
          else:
            raise ValueError(f"Unknown advanced action '{act}'.")
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
          ang = self._run_preview_from_req(analysis, req)
          target_fmt = req.get("target_format", "neoden4")
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
              target_format=target_fmt,
              yy1_coord_mode=req.get("yy1_coord_mode", "preserve"),
              yy1_unit_length=analysis.yy1_unit_length,
              yy1_unit_width=analysis.yy1_unit_width,
              yy1_rows=analysis.yy1_rows,
              yy1_columns=analysis.yy1_columns,
              yy1_fid_x=analysis.yy1_fid_x,
              yy1_fid_y=analysis.yy1_fid_y,
              yy1_offset_x=analysis.yy1_offset_x,
              yy1_offset_y=analysis.yy1_offset_y,
          )
          base, _ext = os.path.splitext(analysis.filepath)
          for known_sfx in (
              "_aligned",
              "_neoden4",
              "_neoden4_init",
              "_neoden_yy1",
              "_neoden_yy1_import",
          ):
            if base.endswith(known_sfx):
              base = base[: -len(known_sfx)]
              break
          if target_fmt == "neoden_yy1":
            suffix = "_neoden_yy1"
          elif target_fmt == "neoden_yy1_import":
            suffix = "_neoden_yy1_import"
          else:
            suffix = (
                "_neoden4_init"
                if analysis.source_format == "kicad_pos"
                else "_aligned"
            )
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
  print(f" NeoDen 4 & YY1 CSV / KiCad .pos Utility v6.0 running at: {url}")
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
# Entrypoint & Command-Line Interface
# ---------------------------------------------------------------------------


def main() -> int:
  parser = argparse.ArgumentParser(
      description=(
          "NeoDen 4 & NeoDen YY1 Pick-and-Place CSV & KiCad .pos Converter"
          " Utility (v6.0). Run without arguments to launch the 2-column GUI."
      )
  )
  parser.add_argument(
      "input_csv",
      nargs="?",
      help="Input KiCad .pos, NeoDen 4 .csv, or NeoDen YY1 .csv file path",
  )
  parser.add_argument(
      "-o",
      "--output",
      help="Output .csv file path (if omitted with no CLI flags, launches GUI)",
  )
  parser.add_argument(
      "--gui",
      action="store_true",
      help="Launch the 2-column GUI in your default web browser",
  )
  parser.add_argument(
      "-t",
      "--target-format",
      choices=["neoden4", "neoden_yy1", "neoden_yy1_import"],
      default=None,
      help=(
          "Target machine CSV format: 'neoden4' (NeoDen 4 Job CSV),"
          " 'neoden_yy1' (NeoDen YY1 Working CSV: NEODEN,YY1,P&P FILE), or"
          " 'neoden_yy1_import' (NeoDen YY1 11-column SD-card Import Template)"
      ),
  )
  parser.add_argument(
      "--yy1",
      action="store_true",
      help="Shortcut for --target-format neoden_yy1 (export NeoDen YY1 Working CSV)",
  )
  parser.add_argument(
      "--yy1-coord-mode",
      choices=["preserve", "invert_neg_y", "shift_positive"],
      default="preserve",
      help=(
          "Coordinate sign handling when exporting for NeoDen YY1: 'preserve'"
          " (keep as-is), 'invert_neg_y' (flip negative X/Y to positive), or"
          " 'shift_positive' (shift bounding box so min X/Y >= +5 mm)"
      ),
  )
  parser.add_argument(
      "--yy1-panel",
      nargs=4,
      type=float,
      metavar=("UNIT_LEN", "UNIT_WID", "ROWS", "COLS"),
      default=None,
      help="Set NeoDen YY1 PanelizedPCB UnitLength, UnitWidth, Rows, Columns",
  )
  parser.add_argument(
      "--yy1-fiducial",
      nargs=2,
      type=float,
      metavar=("FID_X", "FID_Y"),
      default=None,
      help="Set NeoDen YY1 Fiducial 1-X and 1-Y coordinates (mm)",
  )
  parser.add_argument(
      "--yy1-offset",
      nargs=2,
      type=float,
      metavar=("OFFSET_X", "OFFSET_Y"),
      default=None,
      help="Set NeoDen YY1 OverallOffsetX and OverallOffsetY (mm)",
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
          "Batch modify components: FIELD is"
          " feeder|footprint|nozzle|speed|pick_height|place_height|mode, VALUE"
          " is new value, COMPONENT_NUMS is e.g. '1-12,15' or 'all'"
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
      help="Toggle all skips OFF (Place all components: NeoDen 4 Skip='No', YY1 Skip='0')",
  )
  skip_grp.add_argument(
      "--skip-all",
      "--skips-on",
      action="store_true",
      dest="skips_on",
      help="Toggle all skips ON (Skip all components: NeoDen 4 Skip='Yes', YY1 Skip='1')",
  )
  skip_grp.add_argument(
      "--skip-all-except-first",
      action="store_true",
      dest="first_only",
      help="Place only the 1st component (SMD1) and skip all others",
  )

  args = parser.parse_args()

  target_fmt = args.target_format
  if args.yy1 and not target_fmt:
    target_fmt = "neoden_yy1"

  is_cli_transform = (
      args.input_csv is not None
      and not args.gui
      and (
          args.output is not None
          or target_fmt is not None
          or args.yy1_coord_mode != "preserve"
          or args.yy1_panel is not None
          or args.yy1_fiducial is not None
          or args.yy1_offset is not None
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

  if not target_fmt:
    target_fmt = (
        "neoden_yy1"
        if analysis.source_format in ("neoden_yy1", "neoden_yy1_import")
        else "neoden4"
    )

  if args.yy1_panel:
    analysis.yy1_unit_length = float(args.yy1_panel[0])
    analysis.yy1_unit_width = float(args.yy1_panel[1])
    analysis.yy1_rows = max(1, int(args.yy1_panel[2]))
    analysis.yy1_columns = max(1, int(args.yy1_panel[3]))
  if args.yy1_fiducial:
    analysis.yy1_fid_x = float(args.yy1_fiducial[0])
    analysis.yy1_fid_y = float(args.yy1_fiducial[1])
  if args.yy1_offset:
    analysis.yy1_offset_x = float(args.yy1_offset[0])
    analysis.yy1_offset_y = float(args.yy1_offset[1])

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
      target_format=target_fmt,
      yy1_coord_mode=args.yy1_coord_mode,
  )

  out_path = args.output
  if not out_path:
    base, _ext = os.path.splitext(args.input_csv)
    if target_fmt == "neoden_yy1":
      suffix = "_neoden_yy1"
    elif target_fmt == "neoden_yy1_import":
      suffix = "_neoden_yy1_import"
    else:
      suffix = (
          "_neoden4_init"
          if analysis.source_format == "kicad_pos"
          else "_aligned"
      )
    out_path = f"{base}{suffix}.csv"

  save_neoden_csv(out_path, out_lines)

  no_cnt = sum(1 for c in analysis.components if c.skip == "No")
  yes_cnt = len(analysis.components) - no_cnt

  print("=== NeoDen 4 & YY1 CSV / KiCad .pos Utility v6.0 Summary ===")
  print(f"Input File       : {args.input_csv} (format: {analysis.source_format})")
  print(f"Target Format    : {target_fmt}")
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
      f"1st Comp ({c1.name:3s})   : Orig ({c1.x:.2f}, {c1.y:.2f}) -> Output"
      f" ({c1.new_x:.2f}, {c1.new_y:.2f}) mm"
  )
  print(
      f"Total Components : {len(analysis.components)} written (Place={no_cnt},"
      f" Skip={yes_cnt})"
  )
  for w in analysis.warnings:
    print(f"Notice           : {w}")

  return 0


if __name__ == "__main__":
  sys.exit(main())

