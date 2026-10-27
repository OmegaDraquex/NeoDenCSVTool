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
  yy1_rot_offset: float = 0.0
  invert_rot_sign: bool = False
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


def normalize_kicad_footprint(
    pkg: str, val: str = "", use_legacy_profile: bool = False
) -> Tuple[str, float]:
  """
  Converts verbose KiCad package strings (e.g. 'R_0805_2012Metric',
  'SOIC-8_3.9x4.9mm_P1.27mm', 'LED_1206_3216Metric') into short NeoDen
  footprint codes ('0805', 'SOP8', '1206') and returns
  (neoden_footprint, default_rot_offset_deg).
  Why this choice was made:
  KiCad footprint library names often exceed 20 characters and include metric
  dimensions that overflow NeoDen UI fields and prevent feeder matching by
  package size. Standardizing to short JEDEC/EIA codes ('0402', '0603', '0805',
  '1206', 'SOT23', 'SOP8', 'SOP24', etc.) makes component grouping and vision
  setup clean without silently mutating rotations unless legacy profile is active.
  """
  p = sanitize_csv_token(pkg)
  if "SOIC127P600X175-16N" in p or "SOP16S" in p.upper():
    return "SOP16S", (-90.0 if use_legacy_profile else 0.0)
  if p.startswith("SOIC-24") or p.startswith("SOP-24") or "SOP24" in p.upper():
    return "SOP24", 0.0
  if p.startswith("TSSOP-24") or "TSSOP24" in p.upper():
    return "TSSOP24", 0.0
  if p.startswith("LQFP-32") or "LQFP32" in p.upper():
    return "LQFP32", 0.0
  if p.startswith("TQFP-44") or "TQFP44" in p.upper():
    return "TQFP44", 0.0
  if p.startswith("TQFP-64") or "TQFP64" in p.upper():
    return "TQFP64", 0.0
  if p.startswith("QFN-32") or "QFN32" in p.upper():
    return "QFN32", 0.0
  if p.upper() in ("SOT-23", "SOT23") or p.upper().startswith("SOT-23"):
    return "SOT23", (180.0 if use_legacy_profile else 0.0)

  # Extract standard Imperial passive size code (0201..2512) from KiCad strings
  # such as 'C_0603_1608Metric', 'R_0805_2012Metric', or 'LED_1206_3216Metric'.
  m_passive = re.search(
      r"(?:^|_|-)(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?:_|-|$)", p
  )
  if m_passive:
    return m_passive.group(1), 0.0

  # Condense standard IC package prefixes + pin counts (e.g. 'SOIC-8_3.9x4.9mm' -> 'SOP8')
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
    ref: str, val: str, pkg: str, use_legacy_profile: bool = False
) -> Tuple[str, str, str, str, float, str]:
  """
  Maps a KiCad component (Ref, Val, Package) to:
    (feeder_id, nozzle, neoden_val, neoden_fp, rot_offset_deg, default_skip).
  Why this choice was made:
  1. Preserves the actual component Value and normalized Footprint from the KiCad
     .pos file by default so user boards never have their footprints or values
     overwritten by DEFAULT_REF_PROFILE designator collisions.
  2. Normalizes verbose KiCad footprints into clean codes ('0805', '1206', 'SOP8').
  3. Defaults skip to 'No' (Place / YY1 Skip=0) so all valid SMD parts are active.
  """
  if use_legacy_profile and ref in DEFAULT_REF_PROFILE:
    return DEFAULT_REF_PROFILE[ref]

  fp, rot_off = normalize_kicad_footprint(pkg, val, use_legacy_profile=use_legacy_profile)
  v_clean = sanitize_csv_token(val)
  # If KiCad Value is identical to the verbose package name (common on unannotated footprints),
  # shorten it to the normalized footprint so it doesn't clutter the NeoDen UI.
  if v_clean == sanitize_csv_token(pkg) and len(v_clean) > 12:
    v_clean = fp
  if not v_clean:
    v_clean = fp or "PART"

  return ("1", "1", v_clean, fp, rot_off, "No")


def convert_kicad_pos_text_to_neoden_lines(
    pos_text: str,
    exclude_non_smd: bool = True,
    rename_r50_to_d16: bool = False,
    use_legacy_profile: bool = False,
) -> Tuple[List[str], List[Fiducial], List[str]]:
  """
  Stage 1 Converter: Converts a KiCad .pos CSV string into an initial NeoDen
  representation preserving the raw KiCad CAD coordinates (up to 6 decimal places),
  actual component Values & normalized Footprints, 1-panel default (1x1), and
  automatic sequential feeder grouping by (Value, Footprint).
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

  # Track unique (Value, Footprint) groups to auto-assign distinct sequential feeders
  part_group_feeders: Dict[Tuple[str, str], str] = {}
  next_auto_feeder = 1

  for row in reader:
    ref = sanitize_csv_token(row.get("Ref") or "")
    val = sanitize_csv_token(row.get("Val") or "")
    pkg = sanitize_csv_token(row.get("Package") or "")
    side = (row.get("Side") or "top").strip().lower()

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

    # Detect explicit fiducials ('FID*', 'Fiducial*', 'REF**' with Fiducial package)
    # as well as unnamed coordinate marks (e.g. ',,,17,25.5,0,top' in KiCad .pos exports)
    if (
        ref.upper().startswith("FID")
        or "FIDUCIAL" in val.upper()
        or "FIDUCIAL" in pkg.upper()
        or (not ref and not val and not pkg and (abs(posx) > 1e-6 or abs(posy) > 1e-6))
    ):
      fid_label = ref if ref and ref != "REF**" else f"FID{len(cad_fids) + 1}"
      cad_fids.append(
          Fiducial(index=len(cad_fids) + 1, x=posx, y=posy, corner_label=fid_label)
      )
      continue

    if not ref:
      continue

    is_excl, reason = should_exclude_kicad_part(ref, val, pkg)
    if exclude_non_smd and is_excl:
      excluded_info.append(f"{ref} ({val}, {pkg}) -> {reason}")
      continue

    if side not in ("top", "t", "front", "f", ""):
      excluded_info.append(f"{ref} ({val}, {pkg}) -> Bottom side ({side})")
      continue

    feeder_id, nozzle, n4_val, n4_fp, rot_off, def_skip = (
        assign_feeder_for_kicad_part(
            ref, val, pkg, use_legacy_profile=use_legacy_profile
        )
    )

    if not use_legacy_profile:
      grp_key = (n4_val.upper(), n4_fp.upper())
      if grp_key not in part_group_feeders:
        part_group_feeders[grp_key] = str(min(99, next_auto_feeder))
        next_auto_feeder += 1
      feeder_id = part_group_feeders[grp_key]

    out_ref = ref
    if rename_r50_to_d16 and ref == "R50" and n4_val == "Zen":
      out_ref = "D16"

    cad_x = round(posx, 6)
    cad_y = round(posy, 6)
    cad_rot = wrap_neoden_angle(rot + rot_off)

    if len(comp_lines) == 0:
      def_skip = "No"
      first_comp_xy = (cad_x, cad_y)

    comp_lines.append(
        f"comp,{feeder_id},{nozzle},{out_ref},{n4_val},{n4_fp},"
        f"{format_yy1_float(cad_x, 6)},{format_yy1_float(cad_y, 6)},"
        f"{format_yy1_float(cad_rot, 4)},{def_skip},"
    )

  # Build the initial NeoDen 4 header using CAD fiducials (if present in .pos)
  # and setting initial mirror SMD1 to the 1st component's CAD coordinate.
  # Default to 1 panel (mirror_create,1,1) so single boards never default to 2 panels.
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
      f"mirror_create,1,1,{c1x:.2f},{c1y:.2f},{c1x:.2f},{c1y:.2f},0,0,0,"
  )
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


def clean_excel_corrupted_line(line: str, preserve_precision: bool = False) -> Tuple[str, bool]:
  """
  Repairs a single NeoDen 4 CSV line if Microsoft Excel padded shorter rows with
  extra trailing commas (to match the 33-column 'stack' rows) or stripped the
  single trailing comma required by the NeoDen 4 C++ parser.
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

    if prefix == "comp" and len(tokens) >= 10 and not preserve_precision:
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
    cleaned_line, was_modified = clean_excel_corrupted_line(
        raw_line, preserve_precision=(csv_type == "kicad_pos")
    )
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
        # Always default YY1 output to 1x1 panel unless explicitly changed by user
        analysis.yy1_columns = 1
        analysis.yy1_rows = 1
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

  (
      analysis.edge_angles,
      analysis.recommended_angle_deg,
      analysis.recommended_method,
      analysis.used_fiducial_indices,
      analysis.outlier_fiducial_note,
  ) = analyze_fiducials(analysis.fiducials, cad_dx=cad_dx, cad_dy=cad_dy)

  # Estimate board bounding box dimensions for YY1 PanelizedPCB header using both
  # components and fiducials, and seed YY1 Fiducial 1 from the bottom-left fiducial.
  if analysis.components or analysis.fiducials:
    all_pts_x = [abs(c.x) for c in analysis.components] + [
        abs(f.x) for f in analysis.fiducials
    ]
    all_pts_y = [abs(c.y) for c in analysis.components] + [
        abs(f.y) for f in analysis.fiducials
    ]
    min_x, max_x = min(all_pts_x), max(all_pts_x)
    min_y, max_y = min(all_pts_y), max(all_pts_y)
    # If coordinates lie near a (0,0) board origin (min margin <= 15 mm), assume
    # symmetric inset margin (max + min); otherwise use span + 6 mm border.
    if 0.0 < min_x <= 15.0 and 0.0 < min_y <= 15.0:
      span_x = round(max_x + min_x, 2)
      span_y = round(max_y + min_y, 2)
    else:
      span_x = round(max_x - min_x + 6.0, 2)
      span_y = round(max_y - min_y + 6.0, 2)
    if span_x > 5.0:
      analysis.yy1_unit_length = span_x
    if span_y > 5.0:
      analysis.yy1_unit_width = span_y

  if analysis.fiducials:
    # Prefer the Bottom-Left (BL) fiducial or minimum (x + y) so YY1 Fiducial 1
    # defaults to the lower-left reference mark (e.g. (3, 3) instead of (17, 3)).
    bl_fids = [f for f in analysis.fiducials if f.corner_label == "BL"]
    chosen_fid = (
        bl_fids[0]
        if bl_fids
        else min(analysis.fiducials, key=lambda f: (f.x + f.y, f.y, f.x))
    )
    analysis.yy1_fid_x = chosen_fid.x
    analysis.yy1_fid_y = chosen_fid.y

  if csv_type == "kicad_pos":
    # In Stage 1 (KiCad .pos conversion), keep raw CAD coordinates (0 deg rotation)
    analysis.recommended_angle_deg = 0.0
    analysis.recommended_method = "KiCad .pos Import (Raw CAD Coordinates)"
    analysis.warnings.append(
        f"KiCad .pos Imported: {len(analysis.components)} SMD components"
        f" across {len({(c.value.upper(), c.footprint.upper()) for c in analysis.components})}"
        f" unique part group(s), {len(cad_fids)} fiducial(s) detected, and"
        f" {len(excluded_parts)} non-SMD/THT row(s) excluded."
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
# Batch, Part-Group & Inline Component Modification (+ Undo/Redo)
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


def get_part_groups(analysis: NeoDenCSVAnalysis) -> List[Dict[str, Any]]:
  """
  Groups components by unique (Value, Footprint) pair so the operator can
  configure Feeders, Heads, Rotation offsets, Speeds, and Vision Modes by part
  type in one place (ideal for NeoDen YY1 feeder setup).
  """
  groups_map: Dict[Tuple[str, str], List[Tuple[int, ComponentRow]]] = {}
  for idx, comp in enumerate(analysis.components):
    key = (comp.value.strip().upper(), comp.footprint.strip().upper())
    groups_map.setdefault(key, []).append((idx, comp))

  result: List[Dict[str, Any]] = []
  for (val_up, fp_up), items in groups_map.items():
    first_comp = items[0][1]
    indices = [i for i, _ in items]
    des_list = [c.name for _, c in items]
    feeders = {c.feeder_id for _, c in items}
    heads = {clamp_yy1_head(c.nozzle)[0] for _, c in items}
    nozzles = {c.nozzle for _, c in items}
    speeds = {c.mount_speed for _, c in items}
    modes = {c.yy1_mode for _, c in items}
    pick_hs = {round(c.pick_height, 4) for _, c in items}
    place_hs = {round(c.place_height, 4) for _, c in items}
    skips = {c.skip for _, c in items}

    result.append({
        "group_key": f"{val_up}||{fp_up}",
        "value": first_comp.value,
        "footprint": first_comp.footprint,
        "count": len(items),
        "designators": des_list,
        "row_indices": indices,
        "feeder_id": first_comp.feeder_id if len(feeders) == 1 else "Mixed",
        "nozzle": first_comp.nozzle if len(nozzles) == 1 else "Alt",
        "yy1_head": clamp_yy1_head(first_comp.nozzle)[0] if len(heads) == 1 else "Alt",
        "mount_speed": first_comp.mount_speed if len(speeds) == 1 else 50,
        "pick_height": first_comp.pick_height if len(pick_hs) == 1 else 0.0,
        "place_height": first_comp.place_height if len(place_hs) == 1 else 0.0,
        "yy1_mode": first_comp.yy1_mode if len(modes) == 1 else 1,
        "skip": first_comp.skip if len(skips) == 1 else "Mixed",
    })
  return result


def apply_part_group_edit(
    analysis: NeoDenCSVAnalysis,
    group_key: str,
    edits: Dict[str, Any],
) -> str:
  """
  Applies an edit from the Part-Group Feeder Configuration table to all components
  belonging to that (Value, Footprint) group.
  """
  row_indices = edits.get("row_indices")
  if isinstance(row_indices, list) and row_indices:
    target_indices = [
        int(i) for i in row_indices if 0 <= int(i) < len(analysis.components)
    ]
  else:
    parts = (group_key or "").split("||", 1)
    v_match = parts[0].strip().upper() if len(parts) > 0 else ""
    fp_match = parts[1].strip().upper() if len(parts) > 1 else ""
    target_indices = [
        idx
        for idx, c in enumerate(analysis.components)
        if c.value.strip().upper() == v_match
        and c.footprint.strip().upper() == fp_match
    ]

  if not target_indices:
    raise ValueError("No matching components found for the selected part group.")

  changes_desc: List[str] = []
  if "feeder_id" in edits and str(edits["feeder_id"]).strip() != "":
    f_val = sanitize_csv_token(str(edits["feeder_id"]))
    for idx in target_indices:
      analysis.components[idx].feeder_id = f_val
    changes_desc.append(f"Feeder={f_val}")

  if "nozzle" in edits and str(edits["nozzle"]).strip() != "":
    noz_raw = str(edits["nozzle"]).strip().lower()
    if noz_raw == "alt":
      for k, idx in enumerate(target_indices):
        analysis.components[idx].nozzle = "1" if (k % 2 == 0) else "2"
      changes_desc.append("Head=Alt(1/2)")
    else:
      n_val = sanitize_csv_token(str(edits["nozzle"]))
      for idx in target_indices:
        analysis.components[idx].nozzle = n_val
      changes_desc.append(f"Head={n_val}")

  if "head" in edits and str(edits["head"]).strip() != "":
    n_val = sanitize_csv_token(str(edits["head"]))
    for idx in target_indices:
      analysis.components[idx].nozzle = n_val
    changes_desc.append(f"Head={n_val}")

  if "footprint" in edits and str(edits["footprint"]).strip() != "":
    fp_val = sanitize_csv_token(str(edits["footprint"]))
    for idx in target_indices:
      analysis.components[idx].footprint = fp_val
    changes_desc.append(f"Footprint={fp_val}")

  if "value" in edits and str(edits["value"]).strip() != "":
    v_val = sanitize_csv_token(str(edits["value"]))
    for idx in target_indices:
      analysis.components[idx].value = v_val
    changes_desc.append(f"Value={v_val}")

  if "rot_delta" in edits and edits["rot_delta"] is not None:
    delta = float(edits["rot_delta"])
    for idx in target_indices:
      c = analysis.components[idx]
      c.rotation = wrap_neoden_angle(c.rotation + delta)
    changes_desc.append(f"Rot {delta:+.0f}°")

  if "mount_speed" in edits and edits["mount_speed"] is not None:
    spd = max(1, min(100, int(float(edits["mount_speed"]))))
    for idx in target_indices:
      analysis.components[idx].mount_speed = spd
    changes_desc.append(f"Speed={spd}%")

  if "pick_height" in edits and edits["pick_height"] is not None:
    ph = float(edits["pick_height"])
    for idx in target_indices:
      analysis.components[idx].pick_height = ph
    changes_desc.append(f"PickH={ph:g}mm")

  if "place_height" in edits and edits["place_height"] is not None:
    plh = float(edits["place_height"])
    for idx in target_indices:
      analysis.components[idx].place_height = plh
    changes_desc.append(f"PlaceH={plh:g}mm")

  if "yy1_mode" in edits and edits["yy1_mode"] is not None:
    m_val = max(0, min(4, int(float(edits["yy1_mode"]))))
    for idx in target_indices:
      analysis.components[idx].yy1_mode = m_val
    changes_desc.append(f"Mode=M{m_val}")

  if "skip" in edits and str(edits["skip"]).strip() in ("Yes", "No"):
    sk_val = str(edits["skip"]).strip()
    for idx in target_indices:
      analysis.components[idx].skip = sk_val
    changes_desc.append(f"Skip={sk_val}")

  sample = analysis.components[target_indices[0]]
  return (
      f"Updated group {sample.value} [{sample.footprint}] ({len(target_indices)}"
      f" parts): {', '.join(changes_desc) or 'no changes'}."
  )


def apply_single_row_edit(
    analysis: NeoDenCSVAnalysis,
    row_idx: int,
    edits: Dict[str, Any],
) -> str:
  """
  Applies an inline cell edit from the right-hand Component List Preview table
  to a single component row.
  """
  if not (0 <= row_idx < len(analysis.components)):
    raise ValueError(f"Invalid component row index {row_idx + 1}.")

  comp = analysis.components[row_idx]
  changes: List[str] = []

  if "feeder_id" in edits and str(edits["feeder_id"]).strip() != "":
    comp.feeder_id = sanitize_csv_token(str(edits["feeder_id"]))
    changes.append(f"Feeder={comp.feeder_id}")

  if "nozzle" in edits and str(edits["nozzle"]).strip() != "":
    comp.nozzle = sanitize_csv_token(str(edits["nozzle"]))
    changes.append(f"Head={comp.nozzle}")

  if "head" in edits and str(edits["head"]).strip() != "":
    comp.nozzle = sanitize_csv_token(str(edits["head"]))
    changes.append(f"Head={comp.nozzle}")

  if "name" in edits and str(edits["name"]).strip() != "":
    comp.name = sanitize_csv_token(str(edits["name"]))
    changes.append(f"Name={comp.name}")

  if "value" in edits and str(edits["value"]).strip() != "":
    comp.value = sanitize_csv_token(str(edits["value"]))
    changes.append(f"Value={comp.value}")

  if "footprint" in edits and str(edits["footprint"]).strip() != "":
    comp.footprint = sanitize_csv_token(str(edits["footprint"]))
    changes.append(f"Footprint={comp.footprint}")

  x_val = edits.get("new_x", edits.get("x"))
  if x_val is not None:
    target_nx = float(x_val)
    dx = target_nx - comp.new_x
    comp.x = round(comp.x + dx, 6)
    comp.new_x = round(target_nx, 6)
    changes.append(f"X={comp.new_x:g}")

  y_val = edits.get("new_y", edits.get("y"))
  if y_val is not None:
    target_ny = float(y_val)
    dy = target_ny - comp.new_y
    comp.y = round(comp.y + dy, 6)
    comp.new_y = round(target_ny, 6)
    changes.append(f"Y={comp.new_y:g}")

  rot_val = edits.get("new_rot", edits.get("rot"))
  if rot_val is not None:
    target_nrot = wrap_neoden_angle(float(rot_val))
    drot = target_nrot - comp.new_rotation
    comp.rotation = wrap_neoden_angle(comp.rotation + drot)
    comp.new_rotation = target_nrot
    changes.append(f"Rot={comp.new_rotation:g}°")

  if "rot_delta" in edits and edits["rot_delta"] is not None:
    drot = float(edits["rot_delta"])
    comp.rotation = wrap_neoden_angle(comp.rotation + drot)
    comp.new_rotation = wrap_neoden_angle(comp.new_rotation + drot)
    changes.append(f"Rot {drot:+.0f}°")

  if "mount_speed" in edits and edits["mount_speed"] is not None:
    comp.mount_speed = max(1, min(100, int(float(edits["mount_speed"]))))
    changes.append(f"Speed={comp.mount_speed}%")

  if "pick_height" in edits and edits["pick_height"] is not None:
    comp.pick_height = float(edits["pick_height"])
    changes.append(f"PickH={comp.pick_height:g}mm")

  if "place_height" in edits and edits["place_height"] is not None:
    comp.place_height = float(edits["place_height"])
    changes.append(f"PlaceH={comp.place_height:g}mm")

  if "yy1_mode" in edits and edits["yy1_mode"] is not None:
    comp.yy1_mode = max(0, min(4, int(float(edits["yy1_mode"]))))
    changes.append(f"Mode=M{comp.yy1_mode}")

  if "skip" in edits and str(edits["skip"]).strip() in ("Yes", "No"):
    comp.skip = str(edits["skip"]).strip()
    changes.append(f"Skip={comp.skip}")

  return f"Edited #{row_idx + 1} ({comp.name}): {', '.join(changes) or 'updated'}."


def shift_board_origin(
    analysis: NeoDenCSVAnalysis,
    mode: str,
    target_x: float = 0.0,
    target_y: float = 0.0,
) -> str:
  """
  Re-zeros or shifts all component & fiducial coordinates relative to a physical
  reference point on a cut-out FR1 board:
    - 'fid1'        : Sets active YY1 Fiducial 1 (or BL fiducial) to (target_x, target_y)
    - 'comp1'       : Sets 1st Component (SMD1) to (target_x, target_y)
    - 'bbox_margin' : Shifts minimum board X/Y to (target_x, target_y)
    - 'delta'       : Adds (target_x, target_y) directly to all coordinates
  """
  if not analysis.components:
    raise ValueError("No components loaded.")

  m = (mode or "fid1").strip().lower()
  if m == "fid1":
    ref_x, ref_y = analysis.yy1_fid_x, analysis.yy1_fid_y
    if abs(ref_x) < 1e-6 and abs(ref_y) < 1e-6 and analysis.fiducials:
      ref_x, ref_y = analysis.fiducials[0].x, analysis.fiducials[0].y
    dx = target_x - ref_x
    dy = target_y - ref_y
    desc = f"Fiducial 1 ({ref_x:g}, {ref_y:g}) -> ({target_x:g}, {target_y:g})"
  elif m == "comp1":
    ref_x, ref_y = analysis.components[0].x, analysis.components[0].y
    dx = target_x - ref_x
    dy = target_y - ref_y
    desc = f"1st Component {analysis.components[0].name} ({ref_x:g}, {ref_y:g}) -> ({target_x:g}, {target_y:g})"
  elif m == "bbox_margin":
    all_x = [c.x for c in analysis.components] + [f.x for f in analysis.fiducials]
    all_y = [c.y for c in analysis.components] + [f.y for f in analysis.fiducials]
    dx = target_x - min(all_x)
    dy = target_y - min(all_y)
    desc = f"Minimum bounding box corner -> ({target_x:g}, {target_y:g}) mm"
  elif m == "delta":
    dx = target_x
    dy = target_y
    desc = f"Shifted all coordinates by ({dx:+g}, {dy:+g}) mm"
  else:
    raise ValueError(f"Unknown origin shift mode '{mode}'.")

  for c in analysis.components:
    c.x = round(c.x + dx, 6)
    c.y = round(c.y + dy, 6)
  for f in analysis.fiducials:
    f.x = round(f.x + dx, 6)
    f.y = round(f.y + dy, 6)
  for p in analysis.panels:
    p.x = round(p.x + dx, 6)
    p.y = round(p.y + dy, 6)
  analysis.yy1_fid_x = round(analysis.yy1_fid_x + dx, 6)
  analysis.yy1_fid_y = round(analysis.yy1_fid_y + dy, 6)
  return f"Origin shift applied: {desc} (dX={dx:+.4f}, dY={dy:+.4f} mm)."


def apply_batch_component_edit(
    analysis: NeoDenCSVAnalysis,
    field_name: str,
    new_value: str,
    selector_str: str,
) -> Tuple[int, str]:
  """
  Applies a batch modification to 'feeder', 'footprint', 'nozzle' (Head),
  'speed' (YY1 Mount Speed %), 'pick_height' (mm), 'place_height' (mm),
  'mode' (YY1 Vision Mode 0-4), or 'rot_delta' across the selected 1-based component #s.
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
    elif f_key == "value":
      comp.value = val_clean
    elif f_key in ("nozzle", "head"):
      comp.nozzle = val_clean
    elif f_key == "rot_delta":
      try:
        comp.rotation = wrap_neoden_angle(comp.rotation + float(val_clean))
      except ValueError as exc:
        raise ValueError("Rotation offset must be a number in degrees (e.g. -90, 90, 180).") from exc
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
      "value": "Value / Comment",
      "nozzle": "Nozzle / Head",
      "head": "Head",
      "rot_delta": "Rotation Offset (deg)",
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
  the exact 13-column structure produced and expected by the NeoDen YY1 machine.
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
  Generates the 11-column NeoDen YY1 SD-Card Import Template CSV.
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
    comp_rot_offset_deg: Optional[float] = None,
    invert_rot_sign: Optional[bool] = None,
) -> List[str]:
  """
  Applies coordinate transformations, board rotation, tape rotation offsets,
  YY1 coordinate-sign protections, and skip settings to all components, and
  returns the formatted CSV lines for the requested target_format.
  """
  if not analysis.components:
    raise ValueError("No component rows found in the file.")

  if skip_mode != "custom" or custom_skips:
    apply_skip_mode(analysis, skip_mode, custom_skips)

  rot_off = (
      float(comp_rot_offset_deg)
      if comp_rot_offset_deg is not None
      else analysis.yy1_rot_offset
  )
  inv_rot = (
      bool(invert_rot_sign)
      if invert_rot_sign is not None
      else analysis.invert_rot_sign
  )

  c1_orig_x = analysis.components[0].x
  c1_orig_y = analysis.components[0].y

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
      base_r = -comp.rotation if inv_rot else comp.rotation
      comp.new_rotation = wrap_neoden_angle(base_r + rot_off)
  else:
    if not analysis.panels and (panel1_x is None or panel1_y is None):
      target_p1_x, target_p1_y = c1_orig_x, c1_orig_y
    else:
      target_p1_x = panel1_x if panel1_x is not None else analysis.panels[0].x
      target_p1_y = panel1_y if panel1_y is not None else analysis.panels[0].y

    theta_rad = math.radians(board_angle_deg)
    cos_t = math.cos(theta_rad)
    sin_t = math.sin(theta_rad)

    for idx, comp in enumerate(analysis.components):
      if idx == 0:
        comp.new_x = target_p1_x
        comp.new_y = target_p1_y
      else:
        dx = comp.x - c1_orig_x
        dy = comp.y - c1_orig_y
        comp.new_x = target_p1_x + (dx * cos_t - dy * sin_t)
        comp.new_y = target_p1_y + (dx * sin_t + dy * cos_t)

      base_r = -comp.rotation if inv_rot else comp.rotation
      if rotate_component_angles:
        comp.new_rotation = wrap_neoden_angle(base_r + board_angle_deg + rot_off)
      else:
        comp.new_rotation = wrap_neoden_angle(base_r + rot_off)

  # Apply optional YY1 coordinate-sign normalization:
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
<title>NeoDen YY1 &amp; NeoDen 4 CSV &amp; KiCad .pos Utility v7.0</title>
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
    padding: 10px;
    background: #dde3ea;
    color: #0f172a;
  }
  .app-shell {
    width: 100%;
    max-width: 1640px;
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

  .workspace {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
  }
  @media (min-width: 980px) {
    .workspace {
      display: grid;
      grid-template-columns: 520px minmax(0, 1fr);
      gap: 14px;
      align-items: start;
    }
  }

  .left-pane {
    display: flex;
    flex-direction: column;
    gap: 9px;
    min-width: 0;
  }
  .right-pane {
    display: flex;
    flex-direction: column;
    gap: 10px;
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
  details.sec-dropdown {
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #ffffff;
    overflow: hidden;
  }
  details.sec-dropdown > summary {
    padding: 8px 11px;
    font-size: 12px;
    font-weight: 700;
    color: #1e293b;
    background: #f1f5f9;
    cursor: pointer;
    user-select: none;
    list-style: none;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  details.sec-dropdown > summary::-webkit-details-marker {
    display: none;
  }
  details.sec-dropdown > summary::before {
    content: "▸ ";
    color: #2563eb;
    font-weight: 900;
    margin-right: 4px;
  }
  details.sec-dropdown[open] > summary::before {
    content: "▾ ";
  }
  details.sec-dropdown[open] > summary {
    border-bottom: 1px solid #cbd5e1;
    background: #eff6ff;
    color: #1e3a8a;
  }
  .sec-body {
    padding: 9px 11px 11px;
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
    font-size: 11.5px;
    color: #334155;
  }
  input[type="text"], input[type="number"], select {
    background: #ffffff;
    color: #0f172a;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    padding: 3px 6px;
    font-size: 11.5px;
  }
  input[type="number"] {
    width: 74px;
    font-variant-numeric: tabular-nums;
  }
  input[readonly], input:disabled {
    background: #e2e8f0 !important;
    color: #475569 !important;
    border-color: #cbd5e1 !important;
    cursor: not-allowed;
  }
  button {
    background: #e2e8f0;
    color: #0f172a;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 11px;
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
    padding: 2px 5px;
    font-size: 10.5px;
    background: #f1f5f9;
  }
  button.rot-pill {
    padding: 3px 7px;
    font-size: 11px;
    background: #f8fafc;
  }
  button.rot-pill.active {
    background: #1d4ed8;
    color: #ffffff;
    border-color: #1e40af;
  }
  button.skip-btn {
    padding: 3px 7px;
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
    font-size: 11px;
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
    font-size: 10px;
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

  /* Part Group Feeder Table on Left */
  .group-table-wrap {
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    max-height: 290px;
    overflow-y: auto;
    background: #ffffff;
    margin-top: 6px;
  }
  table.group-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
  }
  table.group-table th {
    position: sticky;
    top: 0;
    background: #f1f5f9;
    color: #1e293b;
    border-bottom: 1px solid #cbd5e1;
    padding: 5px 4px;
    font-weight: 700;
    text-align: center;
    z-index: 1;
  }
  table.group-table td {
    border-bottom: 1px solid #f1f5f9;
    padding: 4px 4px;
    text-align: center;
    vertical-align: middle;
  }
  table.group-table tr:hover td {
    background: #eff6ff;
  }

  /* Fiducial List */
  .fid-list-box {
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    padding: 6px 8px;
    margin-bottom: 8px;
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
    width: 68px !important;
    text-align: right;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 11px !important;
    padding: 2px 4px !important;
  }
  .fid-badge-used {
    background: #dcfce7;
    color: #166534;
    border: 1px solid #86efac;
    border-radius: 3px;
    padding: 1px 6px;
    font-size: 10px;
    font-weight: 700;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .fid-badge-unused {
    background: #fef3c7;
    color: #92400e;
    border: 1px solid #fde68a;
    border-radius: 3px;
    padding: 1px 6px;
    font-size: 10px;
    font-weight: 600;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
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
  }

  /* 2D Interactive PCB / FR1 Board Map */
  .board-map-card {
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #f8fafc;
    overflow: hidden;
  }
  .board-map-card > summary {
    padding: 7px 10px;
    font-size: 12.5px;
    font-weight: 700;
    color: #1e293b;
    background: #eff6ff;
    border-bottom: 1px solid #cbd5e1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    user-select: none;
  }
  .board-map-body {
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 7px;
  }
  .board-svg-wrap {
    width: 100%;
    height: 270px;
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    overflow: hidden;
  }
  .locator-hud {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-left: 4px solid #2563eb;
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 11.5px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }

  /* Right Pane Component Table with Inline Editing */
  .right-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
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
    max-height: calc(100vh - 440px);
    min-height: 290px;
    overflow-y: auto;
    overflow-x: auto;
    background: #ffffff;
  }
  table.comp-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
  }
  table.comp-table th {
    position: sticky;
    top: 0;
    background: #f1f5f9;
    color: #1e293b;
    border-bottom: 1px solid #cbd5e1;
    padding: 5px 3px;
    text-align: center;
    font-weight: 700;
    z-index: 1;
    white-space: nowrap;
  }
  table.comp-table td {
    border-bottom: 1px solid #f1f5f9;
    padding: 3px 3px;
    text-align: center;
    color: #1e293b;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  table.comp-table tr:nth-child(even) td {
    background: #f8fafc;
  }
  table.comp-table tr.selected-row td {
    background: #fef3c7 !important;
  }
  .cell-inp {
    width: 100%;
    min-width: 42px;
    padding: 2px 4px !important;
    font-size: 11px !important;
    text-align: center;
    border: 1px solid transparent !important;
    background: transparent !important;
    border-radius: 3px;
  }
  .cell-inp:hover, .cell-inp:focus {
    border-color: #3b82f6 !important;
    background: #ffffff !important;
    outline: none;
  }
  .cell-inp-num {
    width: 58px;
    padding: 2px 3px !important;
    font-size: 11px !important;
    text-align: right;
    border: 1px solid #cbd5e1 !important;
    background: #ffffff !important;
    color: #1d4ed8 !important;
    font-weight: 600;
  }
  .row-skip-pill {
    display: inline-block;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 10.5px;
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
  .sub-badge {
    font-size: 10.5px;
    font-weight: 600;
    color: #475569;
    background: #e2e8f0;
    padding: 1px 6px;
    border-radius: 999px;
  }
</style>
</head>
<body>
<div class="app-shell">
  <div class="titlebar">
    <h1>NeoDen YY1 &amp; NeoDen 4 Pick-and-Place CSV &amp; KiCad .pos Utility</h1>
    <div class="titlebar-right">
      <button type="button" id="undoBtn" onclick="undoEdit()" disabled style="background:#ffffff;color:#1e3a8a;border-color:#bfdbfe;">&#8630; Undo</button>
      <button type="button" id="redoBtn" onclick="redoEdit()" disabled style="background:#ffffff;color:#1e3a8a;border-color:#bfdbfe;">&#8631; Redo</button>
      <a href="/help" target="_blank" rel="noopener" class="help-box" title="Opens a detailed guide in a new tab">
        &#128214; Help Guide
      </a>
      <span class="badge">v7.0</span>
    </div>
  </div>

  <div class="workspace">
    <!-- ==================== LEFT COLUMN: STREAMLINED CONTROLS ==================== -->
    <div class="left-pane">
      <!-- Section 1 (Always Open): File, Target Format, Quick Rotation & Global Skips -->
      <fieldset>
        <legend>1. Input File, Output Machine &amp; Quick Rotation / Skips</legend>
        <div class="row">
          <label for="fileInput" style="font-weight:600;width:95px;">Browse File:</label>
          <input type="file" id="fileInput" accept=".csv,.pos,text/csv" style="font-size:11.5px;flex:1;">
          <span id="sourceFormatTag" class="format-tag">No File</span>
        </div>
        <div class="row">
          <label for="targetFormat" style="font-weight:600;width:95px;">Export Target:</label>
          <select id="targetFormat" onchange="onTargetFormatChange()" style="flex:1;font-weight:600;">
            <option value="neoden_yy1" selected>NeoDen YY1 Working CSV (NEODEN,YY1,P&amp;P FILE &mdash; Default)</option>
            <option value="neoden4">NeoDen 4 Job CSV (#Feeder / stack / comp)</option>
            <option value="neoden_yy1_import" id="optTarget3" style="display:none;" disabled>NeoDen YY1 11-Col Bare Import Template</option>
          </select>
        </div>

        <!-- Global KiCad -> YY1 Tape Rotation Offset Bar -->
        <div class="row" style="margin-top:6px;padding-top:6px;border-top:1px dashed #cbd5e1;">
          <label style="width:95px;font-weight:700;color:#0f172a;" title="Shifts all component rotations to match NeoDen YY1 tape feeder orientation">Tape Rot Offset:</label>
          <button type="button" id="rotOff0" class="rot-pill active" onclick="setGlobalRotOffset(0)">0&deg; (CAD)</button>
          <button type="button" id="rotOffM90" class="rot-pill" onclick="setGlobalRotOffset(-90)">-90&deg; (YY1 Tape)</button>
          <button type="button" id="rotOffP90" class="rot-pill" onclick="setGlobalRotOffset(90)">+90&deg;</button>
          <button type="button" id="rotOff180" class="rot-pill" onclick="setGlobalRotOffset(180)">180&deg;</button>
          <input type="number" step="1" id="yy1RotOffset" value="0" style="width:54px;" onchange="recalculate()" title="Custom global rotation offset (°)">
          <label style="font-size:11px;display:inline-flex;align-items:center;gap:3px;" title="Flip CW/CCW sign (-Rot) before adding offset">
            <input type="checkbox" id="invertRotSign" onchange="recalculate()"> -Rot
          </label>
        </div>

        <!-- Global Skip Toggles -->
        <div class="row" style="margin-top:4px;">
          <label style="width:95px;font-weight:700;color:#0f172a;">Global Skips:</label>
          <button type="button" class="skip-btn active-no" onclick="setSkipMode('all_no')">Place All (Skip=0)</button>
          <button type="button" class="skip-btn active-yes" onclick="setSkipMode('all_yes')">Skip All (Skip=1)</button>
          <button type="button" class="skip-btn" onclick="setSkipMode('first_no')">1st Part Only</button>
        </div>
      </fieldset>

      <!-- Section 2 (Always Open): Feeder & Part-Group Setup (Primary YY1 Workflow) -->
      <fieldset style="border-color:#3b82f6;">
        <legend style="color:#1d4ed8;">2. Feeder Configuration by Part Group (Value + Footprint)</legend>
        <div class="row" style="justify-content:space-between;background:#eff6ff;padding:5px 7px;border-radius:4px;border:1px solid #bfdbfe;">
          <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap;">
            <label style="font-weight:700;color:#1e3a8a;">Auto-Feeders:</label>
            <select id="autoFeederStart" style="width:148px;">
              <option value="1" selected>Start #1 (Tape 1&ndash;52)</option>
              <option value="53">Start #53 (Tray 53&ndash;80)</option>
              <option value="81">Start #81 (Bulk 81&ndash;99)</option>
            </select>
            <label style="font-size:11px;display:inline-flex;align-items:center;gap:3px;" title="Alternate Head 1 & Head 2">
              <input type="checkbox" id="autoAltHeads" checked> Alt H1/H2
            </label>
          </div>
          <button type="button" onclick="runAdvancedAction('auto_feeders')" style="background:#1d4ed8;color:#ffffff;border-color:#1e40af;">Auto-Assign</button>
        </div>

        <div class="group-table-wrap">
          <table class="group-table">
            <thead>
              <tr>
                <th style="text-align:left;padding-left:6px;">Part (Val / Pkg)</th>
                <th style="width:52px;">Feeder</th>
                <th style="width:58px;">Head</th>
                <th style="width:82px;">Rot Nudge</th>
                <th style="width:48px;">Spd%</th>
                <th style="width:46px;">Mode</th>
                <th style="width:54px;">Place?</th>
              </tr>
            </thead>
            <tbody id="partGroupTableBody">
              <tr><td colspan="7" style="padding:16px;color:#64748b;">Load a file above to configure feeders by part group.</td></tr>
            </tbody>
          </table>
        </div>
        <div id="batchFeedback" style="font-size:11px;color:#1d4ed8;font-weight:600;margin-top:5px;">
          Tip: Edit a Part Group above to update all matching parts, or edit any single row in the Right Table.
        </div>
      </fieldset>

      <!-- ==================== CLOSED-BY-DEFAULT DROPDOWNS FOR YY1 ==================== -->

      <!-- Dropdown A: YY1 Board Size, Panelization (Default 1x1) & Fiducial 1-X/Y -->
      <details id="ddYy1Panel" class="sec-dropdown">
        <summary>
          <span>YY1 Board Size, Panelization &amp; Fiducial 1-X/Y</span>
          <span id="panelSummaryBadge" class="sub-badge">1&times;1 Panel (Single Board)</span>
        </summary>
        <div class="sec-body">
          <div class="row">
            <label style="width:95px;font-weight:600;">UnitLength (X):</label>
            <input type="number" step="0.01" id="yy1UnitLen" value="20.00" onchange="recalculate()">
            <label style="margin-left:4px;font-weight:600;">UnitWidth (Y):</label>
            <input type="number" step="0.01" id="yy1UnitWid" value="28.50" onchange="recalculate()">
          </div>
          <div class="row">
            <label style="width:95px;font-weight:600;">Panel Rows:</label>
            <input type="number" step="1" min="1" id="yy1Rows" value="1" onchange="recalculate()">
            <label style="margin-left:4px;font-weight:600;">Panel Cols:</label>
            <input type="number" step="1" min="1" id="yy1Cols" value="1" onchange="recalculate()">
            <button type="button" class="nudge" onclick="resetSinglePanel()" title="Force 1x1 single board output">1&times;1 Single</button>
          </div>
          <div class="row">
            <label style="width:95px;font-weight:600;">Fiducial 1-X:</label>
            <input type="number" step="0.01" id="yy1FidX" value="0.00" onchange="recalculate()">
            <label style="margin-left:4px;font-weight:600;">1-Y:</label>
            <input type="number" step="0.01" id="yy1FidY" value="0.00" onchange="recalculate()">
            <select id="fidQuickPick" onchange="pickFiducialFromDropdown()" style="flex:1;min-width:110px;" title="Pick a detected fiducial from your board">
              <option value="">Pick Fiducial...</option>
            </select>
          </div>
          <div class="row" style="margin-top:4px;">
            <label for="yy1CoordMode" style="width:95px;font-weight:600;">Coord Sign:</label>
            <select id="yy1CoordMode" onchange="recalculate()" style="flex:1;">
              <option value="preserve">Preserve Coordinates (As-Is)</option>
              <option value="invert_neg_y">Invert Negative Coords (-Y &rarr; +Y, -X &rarr; +X)</option>
              <option value="shift_positive">Shift Bounding Box Positive (+5 mm margin)</option>
            </select>
          </div>
        </div>
      </details>

      <!-- Dropdown B: FR1 Origin / Reference Shift, Manual SMD1 & Fiducial Tilt Rotation -->
      <details id="ddOriginAlign" class="sec-dropdown">
        <summary>
          <span>FR1 Origin Shift, SMD1 &amp; Fiducial Tilt Alignment</span>
          <span id="alignSummaryBadge" class="sub-badge">Tilt: 0.00&deg;</span>
        </summary>
        <div class="sec-body">
          <!-- FR1 Reference / Origin Shift Tool -->
          <div style="background:#f8fafc;border:1px solid #cbd5e1;border-radius:4px;padding:6px 8px;margin-bottom:8px;">
            <div style="font-size:11px;font-weight:700;color:#1e3a8a;margin-bottom:4px;">
              FR1 Cutout Reference / Origin Shift (Re-Zero Coordinates):
            </div>
            <div class="row">
              <select id="originShiftMode" style="flex:1;">
                <option value="fid1">Set Active Fiducial 1 to Target (X, Y)</option>
                <option value="comp1">Set 1st Component (SMD1) to Target (X, Y)</option>
                <option value="bbox_margin">Shift Min Board Corner to Margin (X, Y)</option>
                <option value="delta">Add Custom Offset (&Delta;X, &Delta;Y) to All Parts</option>
              </select>
            </div>
            <div class="row">
              <label>Target X:</label>
              <input type="number" step="0.01" id="originTargetX" value="0.00" style="width:66px;">
              <label>Y:</label>
              <input type="number" step="0.01" id="originTargetY" value="0.00" style="width:66px;">
              <button type="button" onclick="applyOriginShift()" style="background:#dbeafe;border-color:#3b82f6;color:#1e3a8a;">Shift Coords</button>
            </div>
          </div>

          <div class="smd1-banner">
            <span id="cadSmd1Banner">1st Component (SMD1) Orig: &mdash;</span>
          </div>
          <div class="row">
            <label style="width:125px;font-weight:600;">Panel 1 / SMD1 X:</label>
            <input type="number" step="0.01" id="p1x" onchange="recalculate()">
            <button type="button" class="nudge" onclick="nudge('p1x', -0.1)">-0.1</button>
            <button type="button" class="nudge" onclick="nudge('p1x', 0.1)">+0.1</button>
            <label style="margin-left:2px;font-weight:600;">Y:</label>
            <input type="number" step="0.01" id="p1y" onchange="recalculate()">
            <button type="button" class="nudge" onclick="nudge('p1y', -0.1)">-0.1</button>
            <button type="button" class="nudge" onclick="nudge('p1y', 0.1)">+0.1</button>
          </div>
          <div class="row">
            <label style="width:125px;">Panel 2 (Top) X:</label>
            <input type="number" step="0.01" id="p2x" onchange="recalculate()">
            <button type="button" class="nudge" onclick="nudge('p2x', -0.1)">-0.1</button>
            <button type="button" class="nudge" onclick="nudge('p2x', 0.1)">+0.1</button>
            <label style="margin-left:2px;">Y:</label>
            <input type="number" step="0.01" id="p2y" onchange="recalculate()">
            <button type="button" class="nudge" onclick="nudge('p2y', -0.1)">-0.1</button>
            <button type="button" class="nudge" onclick="nudge('p2y', 0.1)">+0.1</button>
          </div>

          <div class="fid-list-box" style="margin-top:6px;">
            <div style="font-size:11px;font-weight:700;color:#334155;margin-bottom:4px;">Fiducials in File:</div>
            <div id="fidListContainer">
              <div style="font-size:11px;color:#64748b;">No fiducials loaded.</div>
            </div>
          </div>

          <div class="row">
            <label for="rotMode" style="width:105px;font-weight:600;">Tilt Method:</label>
            <select id="rotMode" onchange="onModeChange()" style="flex:1;">
              <option value="auto">Auto &mdash; Orthogonal Corner / Outlier Rejection</option>
              <option value="avg">Average of All Fiducial Edges</option>
              <option value="manual">Manual Board Tilt Override (&deg;)</option>
              <option value="sq45">2-Fiducial Square Diagonal (45&deg;)</option>
              <option value="rect2">2-Fiducial Rectangular Diagonal (CAD &Delta;X, &Delta;Y)</option>
            </select>
          </div>
          <div class="row">
            <label for="boardAngle" style="width:105px;">Board Tilt (&deg;CCW):</label>
            <input type="number" step="0.0001" id="boardAngle" value="0.0000" readonly disabled onchange="recalculate()">
            <input type="number" step="0.01" id="cadDx" placeholder="CAD &Delta;X" style="width:68px;" onchange="onModeChange()">
            <input type="number" step="0.01" id="cadDy" placeholder="CAD &Delta;Y" style="width:68px;" onchange="onModeChange()">
          </div>
          <div class="check-row">
            <input type="checkbox" id="rotComp" checked onchange="recalculate()">
            <label for="rotComp">Add board tilt to component Rotation column</label>
          </div>
          <div class="check-row">
            <input type="checkbox" id="resetMcAngle" checked onchange="recalculate()">
            <label for="resetMcAngle">Reset NeoDen 4 <code>mirror_create</code> PCB angle to 0&deg;</label>
          </div>
        </div>
      </details>

      <!-- Dropdown C: Row-# Batch Edit, Sorting & Bottom-Side Mirroring -->
      <details id="ddBatchSort" class="sec-dropdown">
        <summary>
          <span>Batch Edit by Row #, Sort Order &amp; Bottom-Side Mirror</span>
          <span class="sub-badge">Power Tools</span>
        </summary>
        <div class="sec-body">
          <div class="row">
            <label for="batchField" style="width:52px;font-weight:600;">Field:</label>
            <select id="batchField" style="max-width:155px;">
              <option value="feeder">Feeder / FeederNo</option>
              <option value="footprint">Footprint</option>
              <option value="value">Value / Comment</option>
              <option value="nozzle">Nozzle / Head</option>
              <option value="rot_delta">Rotation Offset (+deg)</option>
              <option value="speed">YY1 Mount Speed (%)</option>
              <option value="pick_height">YY1 Pick Height (mm)</option>
              <option value="place_height">YY1 Place Height (mm)</option>
              <option value="mode">YY1 Vision Mode (0-4)</option>
            </select>
            <label for="batchValue" style="font-weight:600;">Val:</label>
            <input type="text" id="batchValue" placeholder="e.g. 14 or -90" style="width:85px;">
          </div>
          <div class="row">
            <label for="batchSelector" style="width:52px;font-weight:600;">Rows:</label>
            <input type="text" id="batchSelector" placeholder="Row #s (e.g. 1-6, 9 or all)" style="flex:1;">
            <button type="button" onclick="applyBatchEdit()" style="background:#dbeafe;border-color:#3b82f6;color:#1e3a8a;">Apply</button>
          </div>
          <div class="row" style="margin-top:6px;padding-top:6px;border-top:1px dashed #cbd5e1;">
            <label style="width:95px;font-weight:600;">Sort Order:</label>
            <button type="button" onclick="runAdvancedAction('sort', 'feeder')">By Feeder #</button>
            <button type="button" onclick="runAdvancedAction('sort', 'footprint')">By Footprint</button>
            <button type="button" onclick="runAdvancedAction('sort', 'designator')">By Designator</button>
          </div>
          <div class="row" style="margin-top:6px;padding-top:6px;border-top:1px dashed #cbd5e1;">
            <label style="width:95px;font-weight:600;">Bottom Mirror:</label>
            <input type="number" step="0.01" id="mirrorWidthX" placeholder="Board W (mm)" style="width:95px;">
            <button type="button" onclick="runAdvancedAction('mirror_x')">Mirror X (X = W &minus; X)</button>
          </div>
        </div>
      </details>

      <!-- Dropdown D: YY1 Auto-Nozzle Change & Global Offsets -->
      <details id="ddNozzleAdv" class="sec-dropdown">
        <summary>
          <span>YY1 Nozzle Change Stations &amp; Global Offsets</span>
          <span class="sub-badge">Optional</span>
        </summary>
        <div class="sec-body">
          <div class="row">
            <label style="width:125px;font-weight:600;">OverallOffset X / Y:</label>
            <input type="number" step="0.01" id="yy1OffX" style="width:74px;" value="0.00" onchange="recalculate()">
            <input type="number" step="0.01" id="yy1OffY" style="width:74px;" value="0.00" onchange="recalculate()">
          </div>
          <div style="display:flex;flex-direction:column;gap:4px;font-size:11px;margin-top:6px;">
            <div class="row">
              <span style="width:42px;font-weight:700;">Slot 1:</span>
              <select id="ncEn0" style="width:56px;" onchange="recalculate()"><option value="OFF">OFF</option><option value="ON">ON</option></select>
              <label>Before#:</label><input type="number" id="ncBef0" value="1" min="1" style="width:46px;" onchange="recalculate()">
              <select id="ncHd0" style="width:66px;" onchange="recalculate()"><option value="Head1">Head1</option><option value="Head2">Head2</option></select>
              <label>Drop:</label><input type="text" id="ncDrp0" value="Station1" style="width:62px;" onchange="recalculate()">
              <label>Pick:</label><input type="text" id="ncPck0" value="Station1" style="width:62px;" onchange="recalculate()">
            </div>
            <div class="row">
              <span style="width:42px;font-weight:700;">Slot 2:</span>
              <select id="ncEn1" style="width:56px;" onchange="recalculate()"><option value="OFF">OFF</option><option value="ON">ON</option></select>
              <label>Before#:</label><input type="number" id="ncBef1" value="1" min="1" style="width:46px;" onchange="recalculate()">
              <select id="ncHd1" style="width:66px;" onchange="recalculate()"><option value="Head1">Head1</option><option value="Head2">Head2</option></select>
              <label>Drop:</label><input type="text" id="ncDrp1" value="Station1" style="width:62px;" onchange="recalculate()">
              <label>Pick:</label><input type="text" id="ncPck1" value="Station1" style="width:62px;" onchange="recalculate()">
            </div>
          </div>
          <div class="row" style="margin-top:8px;padding-top:6px;border-top:1px dashed #cbd5e1;">
            <button type="button" onclick="exportTarget3Directly()" style="width:100%;background:#f1f5f9;">
              Export Bare 11-Col YY1 SD-Card Import Template (.csv)
            </button>
          </div>
        </div>
      </details>

      <!-- Dropdown E: Pre-Flight Diagnostics (Closed by default) -->
      <details id="ddDiag" class="sec-dropdown">
        <summary>
          <span>Pre-Flight Diagnostics &amp; Fiducial Math Log</span>
          <span id="diagSummaryBadge" class="sub-badge">Ready</span>
        </summary>
        <div class="sec-body">
          <div id="diagBox" class="diag-box">Browse and select a KiCad .pos, NeoDen YY1 CSV, or NeoDen 4 CSV file to begin.</div>
        </div>
      </details>

      <!-- Action / Export Bar (Always Visible at Bottom of Left Pane) -->
      <div class="footer-bar">
        <div id="statusBar" class="status-pill">No file loaded &mdash; Browse a file in Section 1.</div>
        <button type="button" id="exportBtn" class="primary" onclick="exportCSV()" disabled>EXPORT NEODEN YY1 CSV</button>
      </div>
    </div>

    <!-- ==================== RIGHT COLUMN: 2D FR1 BOARD MAP + INLINE EDIT TABLE ==================== -->
    <div class="right-pane">
      <!-- Interactive 2D PCB / FR1 Component Locator Map -->
      <details class="board-map-card" open>
        <summary>
          <span>&#128506;&#65039; 2D FR1 Board &amp; Component Locator Map (Click any part to locate &amp; edit)</span>
          <span id="boardDimBadge" class="sub-badge">Board Map</span>
        </summary>
        <div class="board-map-body">
          <div class="board-svg-wrap" id="boardSvgContainer">
            <div style="color:#94a3b8;font-size:12px;">Load a KiCad <code>.pos</code> or NeoDen <code>.csv</code> file to view the interactive 2D FR1 board layout &amp; component locator.</div>
          </div>
          <div class="locator-hud" id="locatorHud">
            <div id="locatorText" style="color:#334155;">
              <strong>FR1 Component Locator:</strong> Click any component pad on the board map above or any row below to see its exact distance from Fiducial 1 &amp; Board Origin.
            </div>
            <div id="locatorActions" style="display:none;gap:4px;align-items:center;">
              <button type="button" class="nudge" onclick="rotateSelectedRow(-90)">Rot -90&deg;</button>
              <button type="button" class="nudge" onclick="rotateSelectedRow(90)">Rot +90&deg;</button>
              <button type="button" class="nudge" onclick="rotateSelectedRow(180)">Rot 180&deg;</button>
              <button type="button" class="nudge" onclick="toggleSelectedRowSkip()">Toggle Place/Skip</button>
            </div>
          </div>
        </div>
      </details>

      <!-- Inline-Editable Component List Preview Table -->
      <div class="right-header">
        <h2>Component List Preview (Direct Inline Editing Enabled)</h2>
        <span id="tableSubHeader">Edit any Feeder, Head, Value, Footprint, X/Y, Rot, Speed, or Skip directly in the table</span>
      </div>
      <div class="table-wrap">
        <table class="comp-table">
          <thead>
            <tr>
              <th style="width:28px;">#</th>
              <th style="width:48px;">Name</th>
              <th style="width:52px;">Feeder</th>
              <th style="width:48px;">Head</th>
              <th style="width:86px;">Value</th>
              <th style="width:68px;">Footprint</th>
              <th style="width:56px;">Orig X</th>
              <th style="width:56px;">Orig Y</th>
              <th style="width:64px;">New X</th>
              <th style="width:64px;">New Y</th>
              <th style="width:108px;">New Rot (&deg;)</th>
              <th style="width:48px;">Spd%</th>
              <th style="width:52px;">PickH</th>
              <th style="width:50px;">Mode</th>
              <th style="width:66px;">Skip</th>
            </tr>
          </thead>
          <tbody id="compTableBody">
            <tr><td colspan="15" style="padding:28px;color:#64748b;">Browse a KiCad <code>.pos</code>, NeoDen YY1 <code>.csv</code>, or NeoDen 4 <code>.csv</code> file on the left to view &amp; edit components.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<script>
let currentData = null;
let selectedRowIndex = null;

function fmtCoord(v, decimals = 4) {
  if (v === null || v === undefined || isNaN(v)) return '0';
  const rounded = parseFloat(Number(v).toFixed(decimals));
  let s = rounded.toString();
  if (s === '-0') s = '0';
  return s;
}

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function nudge(inputId, delta) {
  const el = document.getElementById(inputId);
  if (!el || el.disabled) return;
  const cur = parseFloat(el.value) || 0.0;
  el.value = fmtCoord(cur + delta, 4);
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
  selectedRowIndex = null;
  applyLoadedState(payload, true);
});

function updateTargetFormatHelp() {
  const targetFmt = document.getElementById('targetFormat').value;
  const helpBox = document.getElementById('targetFormatHelp');
  if (!helpBox) return;

  if (targetFmt === 'neoden4') {
    helpBox.innerHTML = '<strong>Target 1 &mdash; NeoDen 4 Job CSV:</strong> Full production file for the <em>NeoDen 4 machine</em>. Generates 4-nozzle configuration with <code>#Feeder</code>/<code>stack</code> tables, pcb clamp lines, and <code>mirror</code>/<code>mark</code> SMD1 coordinates.';
  } else if (targetFmt === 'neoden_yy1') {
    helpBox.innerHTML = '<strong>Target 2 &mdash; NeoDen YY1 Working CSV:</strong> Full production file for the <em>NeoDen YY1 machine</em>. Defaults to <strong>1 Panel (Rows=1, Cols=1)</strong>, preserves exact KiCad values &amp; footprints, and lets you configure feeders by part group or inline.';
  } else if (targetFmt === 'neoden_yy1_import') {
    helpBox.innerHTML = '<strong>Target 3 &mdash; NeoDen YY1 11-Col Import Template:</strong> Simplified 11-column SD-card import template (<code>Designator,Footprint,Mid/Ref/Pad X/Y,Layer,Rotation,Comment</code>) for loading raw CAD positions.';
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

  const settingsBox = document.getElementById('neodenSettingsContainer');
  const stage1Card = document.getElementById('kicadStage1Card');
  const exportBtn = document.getElementById('exportBtn');
  const detailsAlign = document.getElementById('detailsAlignment');

  if (settingsBox && currentData) {
    settingsBox.style.display = 'flex';
  }
  if (stage1Card) {
    stage1Card.style.display = (isKicad && !isYY1Target) ? 'block' : 'none';
  }
  if (detailsAlign && !isYY1Target && currentData) {
    detailsAlign.open = !isKicad;
  }

  if (exportBtn) {
    if (targetFmt === 'neoden_yy1') {
      exportBtn.textContent = 'CONVERT & EXPORT NEODEN YY1 CSV (1 PANEL)';
    } else if (targetFmt === 'neoden_yy1_import') {
      exportBtn.textContent = 'EXPORT NEODEN YY1 11-COL IMPORT CSV';
    } else if (isKicad) {
      exportBtn.textContent = 'CONVERT & EXPORT INITIAL NEODEN 4 CSV';
    } else {
      exportBtn.textContent = 'START / EXPORT NEODEN 4 CSV';
    }
  }
}

function updateActiveRotButtons(rotVal) {
  const rotMap = {
    0: 'rotOff0',
    '-90': 'rotOffM90',
    90: 'rotOffP90',
    180: 'rotOff180'
  };
  Object.keys(rotMap).forEach(k => {
    const btn = document.getElementById(rotMap[k]);
    if (btn) {
      btn.classList.toggle('active', Math.abs((rotVal || 0) - Number(k)) < 0.01);
    }
  });
}

function setGlobalRotOffset(deg) {
  const el = document.getElementById('yy1RotOffset');
  if (!el) return;
  el.value = deg;
  updateActiveRotButtons(deg);
  recalculate('custom');
}

function resetSinglePanel() {
  document.getElementById('yy1Rows').value = 1;
  document.getElementById('yy1Cols').value = 1;
  document.getElementById('yy1OffX').value = '0';
  document.getElementById('yy1OffY').value = '0';
  recalculate('custom');
}

function populateFiducialQuickPick(fids, activeFx, activeFy) {
  const sel = document.getElementById('fidQuickPick');
  if (!sel) return;
  sel.innerHTML = '';
  const customOpt = document.createElement('option');
  customOpt.value = '';
  customOpt.textContent = '-- Select detected corner fiducial --';
  sel.appendChild(customOpt);

  (fids || []).forEach(f => {
    const opt = document.createElement('option');
    opt.value = `${f.x},${f.y}`;
    const cornerLbl = f.corner ? `[${f.corner}] ` : '';
    opt.textContent = `Fid #${f.index} ${cornerLbl}(${fmtCoord(f.x)}, ${fmtCoord(f.y)})`;
    if (Math.abs(f.x - activeFx) < 0.005 && Math.abs(f.y - activeFy) < 0.005) {
      opt.selected = true;
    }
    sel.appendChild(opt);
  });
}

function pickFiducialFromDropdown() {
  const sel = document.getElementById('fidQuickPick');
  if (!sel || !sel.value) return;
  const parts = sel.value.split(',');
  if (parts.length === 2) {
    document.getElementById('yy1FidX').value = fmtCoord(parseFloat(parts[0]), 4);
    document.getElementById('yy1FidY').value = fmtCoord(parseFloat(parts[1]), 4);
    recalculate('custom');
  }
}

async function applyOriginShift(mode) {
  if (!currentData) return;
  let targetX = 0.0;
  let targetY = 0.0;
  if (mode === 'fid1') {
    targetX = parseFloat(document.getElementById('yy1FidX').value) || 0.0;
    targetY = parseFloat(document.getElementById('yy1FidY').value) || 0.0;
  } else {
    targetX = parseFloat(document.getElementById('originTargetX').value) || 0.0;
    targetY = parseFloat(document.getElementById('originTargetY').value) || 0.0;
  }
  const res = await fetch('/api/shift_origin', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      mode: mode,
      target_x: targetX,
      target_y: targetY,
      ...collectPreviewParams('custom')
    })
  });
  const payload = await res.json();
  if (payload.error) {
    alert('Shift Origin Error: ' + payload.error);
    return;
  }
  // Sync Fid 1 inputs with updated state
  document.getElementById('yy1FidX').value = fmtCoord(payload.yy1_fid_x, 4);
  document.getElementById('yy1FidY').value = fmtCoord(payload.yy1_fid_y, 4);
  document.getElementById('p1x').value = fmtCoord(payload.p1_x, 4);
  document.getElementById('p1y').value = fmtCoord(payload.p1_y, 4);
  if (payload.has_p2) {
    document.getElementById('p2x').value = fmtCoord(payload.p2_x, 4);
    document.getElementById('p2y').value = fmtCoord(payload.p2_y, 4);
  }
  applyLoadedState(payload, false);
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
    // Default to NeoDen YY1 for KiCad .pos and YY1 files so YY1 users can immediately configure feeders & export 1 panel
    if (payload.source_format === 'neoden') {
      document.getElementById('targetFormat').value = 'neoden4';
    } else {
      document.getElementById('targetFormat').value = 'neoden_yy1';
    }

    document.getElementById('yy1UnitLen').value = fmtCoord(payload.yy1_unit_length || 20.0, 2);
    document.getElementById('yy1UnitWid').value = fmtCoord(payload.yy1_unit_width || 28.5, 2);
    document.getElementById('yy1Rows').value = payload.yy1_rows || 1;
    document.getElementById('yy1Cols').value = payload.yy1_columns || 1;
    document.getElementById('yy1FidX').value = fmtCoord(payload.yy1_fid_x || 0.0, 4);
    document.getElementById('yy1FidY').value = fmtCoord(payload.yy1_fid_y || 0.0, 4);
    document.getElementById('yy1OffX').value = fmtCoord(payload.yy1_offset_x || 0.0, 2);
    document.getElementById('yy1OffY').value = fmtCoord(payload.yy1_offset_y || 0.0, 2);
    document.getElementById('yy1CoordMode').value = 'preserve';
    document.getElementById('yy1RotOffset').value = fmtCoord(payload.yy1_rot_offset || 0.0, 2);
    document.getElementById('invertRotSign').checked = Boolean(payload.invert_rot_sign);
    updateActiveRotButtons(payload.yy1_rot_offset || 0.0);

    document.getElementById('p1x').value = fmtCoord(payload.p1_x, 4);
    document.getElementById('p1y').value = fmtCoord(payload.p1_y, 4);
    if (payload.has_p2) {
      document.getElementById('p2x').disabled = false;
      document.getElementById('p2y').disabled = false;
      document.getElementById('p2x').value = fmtCoord(payload.p2_x, 4);
      document.getElementById('p2y').value = fmtCoord(payload.p2_y, 4);
    } else {
      document.getElementById('p2x').value = '';
      document.getElementById('p2y').value = '';
      document.getElementById('p2x').disabled = true;
      document.getElementById('p2y').disabled = true;
    }
    document.getElementById('rotMode').value = 'auto';
    document.getElementById('boardAngle').value = payload.recommended_angle.toFixed(4);
    updateBoardAngleLock();
  } else {
    const curRotOff = parseFloat(document.getElementById('yy1RotOffset').value) || 0.0;
    updateActiveRotButtons(curRotOff);
  }

  updateSectionVisibility();
  populateFiducialQuickPick(
    payload.fiducials || [],
    parseFloat(document.getElementById('yy1FidX').value) || 0.0,
    parseFloat(document.getElementById('yy1FidY').value) || 0.0
  );

  const rowsVal = parseInt(document.getElementById('yy1Rows').value, 10) || 1;
  const colsVal = parseInt(document.getElementById('yy1Cols').value, 10) || 1;
  const fidXVal = parseFloat(document.getElementById('yy1FidX').value) || 0.0;
  const fidYVal = parseFloat(document.getElementById('yy1FidY').value) || 0.0;
  const panelBadge = document.getElementById('panelSummaryBadge');
  if (panelBadge) {
    panelBadge.textContent = `${rowsVal}x${colsVal} Panel | Fid1 (${fmtCoord(fidXVal, 2)}, ${fmtCoord(fidYVal, 2)})`;
  }
  const alignBadge = document.getElementById('alignSummaryBadge');
  if (alignBadge) {
    alignBadge.textContent = `${payload.active_angle >= 0 ? '+' : ''}${payload.active_angle.toFixed(2)}°`;
  }
  const diagBadge = document.getElementById('diagSummaryBadge');
  if (diagBadge) {
    diagBadge.textContent = `${(payload.fiducials || []).length} Fids`;
  }
  const dimBadge = document.getElementById('boardDimBadge');
  if (dimBadge) {
    const uLen = parseFloat(document.getElementById('yy1UnitLen').value) || payload.yy1_unit_length || 0;
    const uWid = parseFloat(document.getElementById('yy1UnitWid').value) || payload.yy1_unit_width || 0;
    dimBadge.textContent = `${fmtCoord(uLen, 2)} × ${fmtCoord(uWid, 2)} mm | ${rowsVal}×${colsVal} Panel`;
  }

  const fmtLabels = {
    'kicad_pos': 'KiCad .pos CSV',
    'neoden': 'NeoDen 4 CSV',
    'neoden_yy1': 'NeoDen YY1 Working CSV',
    'neoden_yy1_import': 'NeoDen YY1 11-Col Import CSV'
  };
  document.getElementById('cadSmd1Banner').textContent =
    `1st Component (${payload.c1_name} / ${payload.c1_footprint}) Orig: (${fmtCoord(payload.c1_x, 4)}, ${fmtCoord(payload.c1_y, 4)}) mm`;
  document.getElementById('sourceFormatTag').textContent =
    fmtLabels[payload.source_format] || payload.source_format;

  document.getElementById('undoBtn').disabled = !payload.can_undo;
  document.getElementById('redoBtn').disabled = !payload.can_redo;
  if (payload.batch_message) {
    const bf = document.getElementById('batchFeedback');
    if (bf) bf.textContent = payload.batch_message;
  }

  renderPartGroups(payload.part_groups || []);
  renderFiducialList(payload.fiducials || []);
  renderDiagnostics(payload);
  renderBoardMap(payload);
  renderTable(payload.components || []);
  updateSelectedHud();

  const noCount = (payload.components || []).filter(c => c.skip === 'No').length;
  const yesCount = (payload.components || []).length - noCount;
  const targetFmt = document.getElementById('targetFormat').value;
  const skipHint = (targetFmt === 'neoden_yy1')
    ? 'YY1 Skip: 0=Place, 1=Skip'
    : 'NeoDen 4 Skip: No=Place, Yes=Skip';
  document.getElementById('tableSubHeader').textContent =
    `${payload.components.length} components (Place=${noCount}, Skip=${yesCount}) — ${skipHint}`;
  document.getElementById('statusBar').textContent =
    `Ready: ${payload.filename} [${fmtLabels[payload.source_format] || payload.source_format}] -> Target: ${targetFmt.toUpperCase()} (${payload.components.length} parts, ${rowsVal}x${colsVal} panel)`;
}

function renderPartGroups(groups) {
  const tbody = document.getElementById('partGroupTableBody');
  const badge = document.getElementById('partGroupCountBadge');
  if (!tbody) return;
  tbody.innerHTML = '';
  if (badge) {
    badge.textContent = `${groups.length} group${groups.length === 1 ? '' : 's'}`;
  }
  if (!groups || groups.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="color:#64748b;padding:12px;">No components loaded.</td></tr>';
    return;
  }

  groups.forEach((g, gIdx) => {
    const tr = document.createElement('tr');
    const refsShort = g.designators.length > 5
      ? g.designators.slice(0, 5).join(', ') + ` (+${g.designators.length - 5})`
      : g.designators.join(', ');
    const rowIndicesJson = JSON.stringify(g.row_indices);

    tr.innerHTML = `
      <td>
        <input type="number" min="1" max="99" value="${g.feeder_id}"
          title="Feeder # for ${escapeHtml(g.value)} (${escapeHtml(g.footprint)})"
          onchange='editGroupField(${rowIndicesJson}, {feeder_id: this.value})'>
      </td>
      <td>
        <select onchange='editGroupField(${rowIndicesJson}, {head: this.value})'>
          <option value="1" ${Number(g.head) === 1 ? 'selected' : ''}>H1</option>
          <option value="2" ${Number(g.head) === 2 ? 'selected' : ''}>H2</option>
          <option value="3" ${Number(g.head) === 3 ? 'selected' : ''}>H3</option>
          <option value="4" ${Number(g.head) === 4 ? 'selected' : ''}>H4</option>
        </select>
      </td>
      <td>
        <input type="text" value="${escapeHtml(g.value)}"
          title="Edit Value for all ${g.count} parts in this group (${escapeHtml(g.designators.join(', '))})"
          onchange='editGroupField(${rowIndicesJson}, {value: this.value})'>
      </td>
      <td>
        <input type="text" value="${escapeHtml(g.footprint)}"
          title="Edit Footprint for all ${g.count} parts in this group"
          onchange='editGroupField(${rowIndicesJson}, {footprint: this.value})'>
      </td>
      <td class="pg-refs" title="${escapeHtml(g.designators.join(', '))}">
        <strong>${g.count}&times;</strong> ${escapeHtml(refsShort)}
      </td>
      <td>
        <input type="number" min="1" max="100" value="${g.mount_speed || 50}" style="width:42px;"
          title="Mount speed (%)"
          onchange='editGroupField(${rowIndicesJson}, {mount_speed: this.value})'>
      </td>
      <td>
        <select onchange='editGroupField(${rowIndicesJson}, {skip: this.value})'>
          <option value="No" ${g.skip === 'No' ? 'selected' : ''}>Place</option>
          <option value="Yes" ${g.skip !== 'No' ? 'selected' : ''}>Skip</option>
        </select>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function editGroupField(rowIndices, edits) {
  if (!currentData) return;
  const res = await fetch('/api/edit_part_group', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      row_indices: rowIndices,
      edits: edits,
      ...collectPreviewParams('custom')
    })
  });
  const payload = await res.json();
  if (payload.error) {
    alert('Part Group Edit Error: ' + payload.error);
    return;
  }
  applyLoadedState(payload, false);
}

async function editRowField(rowIndex, edits) {
  if (!currentData) return;
  selectedRowIndex = rowIndex;
  const res = await fetch('/api/edit_row', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      row_index: rowIndex,
      edits: edits,
      ...collectPreviewParams('custom')
    })
  });
  const payload = await res.json();
  if (payload.error) {
    alert('Row Edit Error: ' + payload.error);
    return;
  }
  applyLoadedState(payload, false);
}

function nudgeRowRot(rowIndex, deltaDeg) {
  if (!currentData || !currentData.components[rowIndex]) return;
  const comp = currentData.components[rowIndex];
  let nextRot = (comp.new_rot || 0.0) + deltaDeg;
  while (nextRot > 180.0) nextRot -= 360.0;
  while (nextRot <= -180.0) nextRot += 360.0;
  editRowField(rowIndex, {rot: nextRot});
}

function rotateSelectedRow(deltaDeg) {
  if (selectedRowIndex === null) return;
  nudgeRowRot(selectedRowIndex, deltaDeg);
}

function toggleSelectedRowSkip() {
  if (selectedRowIndex === null) return;
  toggleRowSkip(selectedRowIndex);
}

function selectComponent(rowIndex, scrollToRow = false) {
  selectedRowIndex = rowIndex;
  if (currentData) {
    renderBoardMap(currentData);
    updateSelectedHud();
  }
  const tbody = document.getElementById('compTableBody');
  if (!tbody) return;
  Array.from(tbody.children).forEach((tr, idx) => {
    tr.classList.toggle('selected-row', idx === rowIndex);
    if (idx === rowIndex && scrollToRow) {
      tr.scrollIntoView({behavior: 'smooth', block: 'center'});
    }
  });
}

function updateSelectedHud() {
  const hud = document.getElementById('locatorHud');
  const txt = document.getElementById('locatorText');
  const act = document.getElementById('locatorActions');
  if (!hud || !currentData || selectedRowIndex === null || !currentData.components[selectedRowIndex]) {
    if (txt) {
      txt.innerHTML = '<strong>FR1 Component Locator:</strong> Click any component pad on the board map above or any row below to see its exact distance from Fiducial 1 &amp; Board Origin.';
    }
    if (act) act.style.display = 'none';
    return;
  }
  const c = currentData.components[selectedRowIndex];
  const fidX = parseFloat(document.getElementById('yy1FidX').value) || 0.0;
  const fidY = parseFloat(document.getElementById('yy1FidY').value) || 0.0;
  const dx = c.new_x - fidX;
  const dy = c.new_y - fidY;

  if (txt) {
    txt.innerHTML = `<strong>${escapeHtml(c.name)}</strong> (${escapeHtml(c.value)} / ${escapeHtml(c.footprint || '—')}) &mdash; ` +
      `<strong>Pos:</strong> X=${fmtCoord(c.new_x, 4)}, Y=${fmtCoord(c.new_y, 4)} mm | ` +
      `<strong>Rot:</strong> ${fmtCoord(c.new_rot, 1)}° | <strong>Feeder:</strong> #${c.feeder_id} (H${c.yy1_head || 1}) | ` +
      `<span style="color:#7e22ce;font-weight:700;">Δ from Fid 1: ΔX=${dx >= 0 ? '+' : ''}${fmtCoord(dx, 3)} mm, ΔY=${dy >= 0 ? '+' : ''}${fmtCoord(dy, 3)} mm</span>`;
  }
  if (act) act.style.display = 'inline-flex';
}

function estimateFootprintSize(fp, name) {
  const u = String(fp || '').toUpperCase();
  const n = String(name || '').toUpperCase();
  if (u.includes('SOP8') || u.includes('SOIC-8') || u.includes('SOIC8') || n.startsWith('U')) {
    return {w: 4.9, h: 4.0};
  }
  if (u.includes('1206')) return {w: 3.2, h: 1.6};
  if (u.includes('0805')) return {w: 2.0, h: 1.25};
  if (u.includes('0603')) return {w: 1.6, h: 0.85};
  if (u.includes('0402')) return {w: 1.0, h: 0.5};
  if (u.includes('SOT-23') || u.includes('SOT23')) return {w: 2.9, h: 1.6};
  return {w: 2.2, h: 1.4};
}

function renderBoardMap(payload) {
  const container = document.getElementById('boardSvgContainer');
  if (!container) return;
  const comps = payload.components || [];
  const fids = payload.fiducials || [];
  if (comps.length === 0 && fids.length === 0) {
    container.innerHTML = '<div style="color:#64748b;font-size:12px;">Upload a file to view the interactive 2D FR1 / PCB component &amp; fiducial map.</div>';
    return;
  }

  const uLen = parseFloat(document.getElementById('yy1UnitLen').value) || payload.yy1_unit_length || 20.0;
  const uWid = parseFloat(document.getElementById('yy1UnitWid').value) || payload.yy1_unit_width || 28.5;
  const fid1X = parseFloat(document.getElementById('yy1FidX').value) || 0.0;
  const fid1Y = parseFloat(document.getElementById('yy1FidY').value) || 0.0;

  let minX = Math.min(0, fid1X);
  let maxX = Math.max(uLen, fid1X);
  let minY = Math.min(0, fid1Y);
  let maxY = Math.max(uWid, fid1Y);

  comps.forEach(c => {
    minX = Math.min(minX, c.new_x - 2.5);
    maxX = Math.max(maxX, c.new_x + 2.5);
    minY = Math.min(minY, c.new_y - 2.5);
    maxY = Math.max(maxY, c.new_y + 2.5);
  });
  fids.forEach(f => {
    minX = Math.min(minX, f.x - 2.0);
    maxX = Math.max(maxX, f.x + 2.0);
    minY = Math.min(minY, f.y - 2.0);
    maxY = Math.max(maxY, f.y + 2.0);
  });

  const pad = 3.2;
  const vbMinX = minX - pad;
  const vbMaxX = maxX + pad;
  const vbMinY = minY - pad;
  const vbMaxY = maxY + pad;
  const vbW = Math.max(10, vbMaxX - vbMinX);
  const vbH = Math.max(10, vbMaxY - vbMinY);

  // Flip Y so +Y is UP (matching physical PCB / YY1 bed orientation)
  const mapY = (y) => (vbMaxY + vbMinY) - y;

  let svg = `<svg viewBox="${vbMinX.toFixed(2)} ${vbMinY.toFixed(2)} ${vbW.toFixed(2)} ${vbH.toFixed(2)}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;max-height:198px;">`;

  // Grid lines every 5mm
  const startGridX = Math.floor(vbMinX / 5) * 5;
  const endGridX = Math.ceil(vbMaxX / 5) * 5;
  for (let gx = startGridX; gx <= endGridX; gx += 5) {
    svg += `<line x1="${gx}" y1="${vbMinY}" x2="${gx}" y2="${vbMaxY}" stroke="#1e293b" stroke-width="0.12"/>`;
  }
  const startGridY = Math.floor(vbMinY / 5) * 5;
  const endGridY = Math.ceil(vbMaxY / 5) * 5;
  for (let gy = startGridY; gy <= endGridY; gy += 5) {
    const sy = mapY(gy);
    svg += `<line x1="${vbMinX}" y1="${sy}" x2="${vbMaxX}" y2="${sy}" stroke="#1e293b" stroke-width="0.12"/>`;
  }

  // FR1 Copper Board Outline (0,0 to uLen,uWid)
  const boardTopY = mapY(uWid);
  svg += `<rect x="0" y="${boardTopY.toFixed(3)}" width="${uLen.toFixed(3)}" height="${uWid.toFixed(3)}" rx="0.6" fill="#291d12" fill-opacity="0.72" stroke="#b45309" stroke-width="0.28"/>`;

  // Origin (0,0) axes marker
  const oy0 = mapY(0);
  svg += `<line x1="-1.5" y1="${oy0}" x2="2.5" y2="${oy0}" stroke="#38bdf8" stroke-width="0.18"/>`;
  svg += `<line x1="0" y1="${oy0 + 1.5}" x2="0" y2="${oy0 - 2.5}" stroke="#38bdf8" stroke-width="0.18"/>`;
  svg += `<circle cx="0" cy="${oy0}" r="0.38" fill="#38bdf8"/>`;
  svg += `<text x="0.5" y="${oy0 + 1.2}" fill="#38bdf8" font-size="1.15" font-family="monospace">(0,0)</text>`;

  // Fiducial markers
  fids.forEach(f => {
    const fy = mapY(f.y);
    const isActiveFid = (Math.abs(f.x - fid1X) < 0.01 && Math.abs(f.y - fid1Y) < 0.01);
    const strokeCol = isActiveFid ? '#f59e0b' : '#94a3b8';
    const r = isActiveFid ? 0.85 : 0.6;
    svg += `<g style="cursor:pointer;" onclick="document.getElementById('yy1FidX').value='${fmtCoord(f.x, 4)}';document.getElementById('yy1FidY').value='${fmtCoord(f.y, 4)}';recalculate('custom');">
      <title>Fiducial #${f.index} [${f.corner || '?'}] (${fmtCoord(f.x)}, ${fmtCoord(f.y)}) — Click to set as YY1 Fiducial 1</title>
      <circle cx="${f.x}" cy="${fy}" r="${r}" fill="none" stroke="${strokeCol}" stroke-width="0.24"/>
      <line x1="${f.x - 1.1}" y1="${fy}" x2="${f.x + 1.1}" y2="${fy}" stroke="${strokeCol}" stroke-width="0.18"/>
      <line x1="${f.x}" y1="${fy - 1.1}" x2="${f.x}" y2="${fy + 1.1}" stroke="${strokeCol}" stroke-width="0.18"/>
      <text x="${f.x + 1.0}" y="${fy - 0.6}" fill="${strokeCol}" font-size="1.15" font-weight="bold" font-family="sans-serif">${isActiveFid ? 'Fid1' : 'F' + f.index}</text>
    </g>`;
  });

  // If user entered a custom Fid1 not matching any detected fiducial, draw it too
  const matchesKnownFid = fids.some(f => Math.abs(f.x - fid1X) < 0.01 && Math.abs(f.y - fid1Y) < 0.01);
  if (!matchesKnownFid) {
    const fy = mapY(fid1Y);
    svg += `<g>
      <circle cx="${fid1X}" cy="${fy}" r="0.85" fill="none" stroke="#f59e0b" stroke-width="0.24"/>
      <line x1="${fid1X - 1.1}" y1="${fy}" x2="${fid1X + 1.1}" y2="${fy}" stroke="#f59e0b" stroke-width="0.18"/>
      <line x1="${fid1X}" y1="${fy - 1.1}" x2="${fid1X}" y2="${fy + 1.1}" stroke="#f59e0b" stroke-width="0.18"/>
      <text x="${fid1X + 1.0}" y="${fy - 0.6}" fill="#f59e0b" font-size="1.15" font-weight="bold">Fid1</text>
    </g>`;
  }

  // Ruler line from Fid1 to selected component
  if (selectedRowIndex !== null && comps[selectedRowIndex]) {
    const sc = comps[selectedRowIndex];
    const fy = mapY(fid1Y);
    const cy = mapY(sc.new_y);
    svg += `<line x1="${fid1X}" y1="${fy}" x2="${sc.new_x}" y2="${cy}" stroke="#a855f7" stroke-width="0.22" stroke-dasharray="0.6,0.4"/>`;
  }

  // Components
  comps.forEach((c, idx) => {
    const cx = c.new_x;
    const cy = mapY(c.new_y);
    const sz = estimateFootprintSize(c.footprint, c.name);
    const isSel = (idx === selectedRowIndex);
    const isSkipped = (c.skip !== 'No');
    const fillCol = isSel ? '#7e22ce' : (isSkipped ? '#475569' : '#065f46');
    const strokeCol = isSel ? '#f0abfc' : (isSkipped ? '#94a3b8' : '#34d399');
    const strokeW = isSel ? 0.34 : 0.2;
    // In SVG (+Y down), CCW rotation by +theta in PCB space (+Y up) is -theta in SVG space
    const svgRot = -(c.new_rot || 0.0);
    const halfW = sz.w / 2;
    const halfH = sz.h / 2;

    svg += `<g transform="translate(${cx.toFixed(4)}, ${cy.toFixed(4)}) rotate(${svgRot.toFixed(2)})"
      style="cursor:pointer;" onclick="selectComponent(${idx}, true)">
      <title>${escapeHtml(c.name)}: ${escapeHtml(c.value)} (${escapeHtml(c.footprint)})&#10;Pos: (${fmtCoord(c.new_x, 4)}, ${fmtCoord(c.new_y, 4)}) mm&#10;Rot: ${fmtCoord(c.new_rot, 1)}° | Feeder #${c.feeder_id} | Head ${c.yy1_head}</title>
      <rect x="${(-halfW).toFixed(3)}" y="${(-halfH).toFixed(3)}" width="${sz.w.toFixed(3)}" height="${sz.h.toFixed(3)}" rx="0.22"
        fill="${fillCol}" fill-opacity="0.88" stroke="${strokeCol}" stroke-width="${strokeW}"/>
      <!-- Pin 1 / Orientation Dot & Arrow (points along +X at 0° rotation) -->
      <circle cx="${(-halfW + 0.42).toFixed(3)}" cy="${(-halfH + 0.42).toFixed(3)}" r="0.24" fill="#fde047"/>
      <line x1="0" y1="0" x2="${(halfW * 0.78).toFixed(3)}" y2="0" stroke="#f87171" stroke-width="0.22"/>
      <polygon points="${(halfW * 0.88).toFixed(3)},0 ${(halfW * 0.45).toFixed(3)},-0.32 ${(halfW * 0.45).toFixed(3)},0.32" fill="#f87171"/>
    </g>`;

    // upright designator label
    const lblCol = isSel ? '#f0abfc' : '#f8fafc';
    const fontW = isSel ? '700' : '600';
    svg += `<text x="${cx.toFixed(3)}" y="${(cy - 0.15).toFixed(3)}" text-anchor="middle" fill="${lblCol}" font-size="1.15" font-weight="${fontW}" font-family="sans-serif" style="pointer-events:none;">${escapeHtml(c.name)}</text>`;
  });

  svg += `</svg>`;
  container.innerHTML = svg;
}

function renderFiducialList(fids) {
  const container = document.getElementById('fidListContainer');
  if (!container) return;
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
      <input type="text" class="fid-coord" readonly value="${fmtCoord(f.x, 4)}">
      <label>Y:</label>
      <input type="text" class="fid-coord" readonly value="${fmtCoord(f.y, 4)}">
      <button type="button" class="btn-outline" style="padding:1px 6px;font-size:10px;"
        onclick="document.getElementById('yy1FidX').value='${fmtCoord(f.x, 4)}';document.getElementById('yy1FidY').value='${fmtCoord(f.y, 4)}';recalculate('custom');">Use as YY1 Fid1</button>
      <span class="${badgeClass}" title="${escapeHtml(f.role_note)}">${badgeIcon} ${escapeHtml(f.role_note)}</span>
    `;
    container.appendChild(row);
  });
}

function renderDiagnostics(payload) {
  const lines = [];
  lines.push(`File: ${payload.filename} [Source: ${payload.source_format}] | Fiducials: ${(payload.fiducials || []).length}`);
  if ((payload.fiducials || []).length > 0) {
    const fstr = payload.fiducials.map(f => `Fid${f.index}[${f.corner}]=(${fmtCoord(f.x, 4)}, ${fmtCoord(f.y, 4)})`).join(', ');
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
  const box = document.getElementById('diagBox');
  if (box) box.textContent = lines.join('\n');
}

function renderTable(comps) {
  const tbody = document.getElementById('compTableBody');
  if (!tbody) return;
  tbody.innerHTML = '';
  const targetFmt = document.getElementById('targetFormat').value;
  const isYY1Target = (targetFmt === 'neoden_yy1' || targetFmt === 'neoden_yy1_import');
  const fid1X = parseFloat(document.getElementById('yy1FidX').value) || 0.0;
  const fid1Y = parseFloat(document.getElementById('yy1FidY').value) || 0.0;

  comps.forEach((c, idx) => {
    const tr = document.createElement('tr');
    if (idx === selectedRowIndex) tr.classList.add('selected-row');
    tr.addEventListener('click', (ev) => {
      if (ev.target.tagName !== 'INPUT' && ev.target.tagName !== 'SELECT' && ev.target.tagName !== 'BUTTON' && !ev.target.classList.contains('row-skip-pill')) {
        selectComponent(idx, false);
      }
    });

    const skipClass = c.skip === 'No' ? 'row-skip-no' : 'row-skip-yes';
    const skipDisplay = isYY1Target
      ? (c.skip === 'No' ? '0 (Place)' : '1 (Skip)')
      : c.skip;
    const headVal = isYY1Target ? c.yy1_head : c.nozzle;
    const dx = c.new_x - fid1X;
    const dy = c.new_y - fid1Y;

    tr.innerHTML = `
      <td>${idx + 1}</td>
      <td title="Click to highlight ${escapeHtml(c.name)} on 2D Board Map" style="cursor:pointer;" onclick="selectComponent(${idx}, false)">
        <strong>${escapeHtml(c.name)}</strong>
      </td>
      <td>
        <input type="number" class="inline-cell-input inline-num" min="1" max="99" value="${c.feeder_id}"
          onchange="editRowField(${idx}, {feeder_id: this.value})">
      </td>
      <td>
        <select class="inline-cell-select" onchange="editRowField(${idx}, {head: this.value})">
          <option value="1" ${Number(headVal) === 1 ? 'selected' : ''}>H1</option>
          <option value="2" ${Number(headVal) === 2 ? 'selected' : ''}>H2</option>
          <option value="3" ${Number(headVal) === 3 ? 'selected' : ''}>H3</option>
          <option value="4" ${Number(headVal) === 4 ? 'selected' : ''}>H4</option>
        </select>
      </td>
      <td>
        <input type="text" class="inline-cell-input inline-txt" value="${escapeHtml(c.value)}"
          onchange="editRowField(${idx}, {value: this.value})">
      </td>
      <td>
        <input type="text" class="inline-cell-input inline-txt" value="${escapeHtml(c.footprint || '')}"
          onchange="editRowField(${idx}, {footprint: this.value})">
      </td>
      <td style="color:#64748b;">${fmtCoord(c.old_x, 4)}</td>
      <td style="color:#64748b;">${fmtCoord(c.old_y, 4)}</td>
      <td class="highlight-new">
        <input type="number" step="0.01" class="inline-cell-input inline-coord" value="${fmtCoord(c.new_x, 4)}"
          onchange="editRowField(${idx}, {x: this.value})">
      </td>
      <td class="highlight-new">
        <input type="number" step="0.01" class="inline-cell-input inline-coord" value="${fmtCoord(c.new_y, 4)}"
          onchange="editRowField(${idx}, {y: this.value})">
      </td>
      <td style="font-size:10.5px;color:#475569;" title="Offset from Fiducial 1 (${fmtCoord(fid1X, 2)}, ${fmtCoord(fid1Y, 2)}) mm">
        ${dx >= 0 ? '+' : ''}${fmtCoord(dx, 2)}, ${dy >= 0 ? '+' : ''}${fmtCoord(dy, 2)}
      </td>
      <td class="highlight-new">
        <div class="rot-cell-wrap">
          <button type="button" class="rot-mini-btn" title="Rotate -90°" onclick="nudgeRowRot(${idx}, -90)">-90</button>
          <input type="number" step="1" class="inline-cell-input inline-rot" value="${fmtCoord(c.new_rot, 2)}"
            onchange="editRowField(${idx}, {rot: this.value})">
          <button type="button" class="rot-mini-btn" title="Rotate +90°" onclick="nudgeRowRot(${idx}, 90)">+90</button>
        </div>
      </td>
      <td>
        <input type="number" min="1" max="100" class="inline-cell-input inline-num" value="${c.mount_speed || 50}"
          title="Mount Speed (%)" onchange="editRowField(${idx}, {mount_speed: this.value})">
      </td>
      <td>
        <input type="number" step="0.1" class="inline-cell-input inline-num" style="width:44px;" value="${fmtCoord(c.pick_height || 0, 2)}"
          title="Pick Height (mm)" onchange="editRowField(${idx}, {pick_height: this.value})">
      </td>
      <td>
        <span class="row-skip-pill ${skipClass}" onclick="toggleRowSkip(${idx})">${skipDisplay}</span>
      </td>
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
  const mainPane = document.getElementById('mainTabPane');
  const advPane = document.getElementById('advTabPane');
  const tabMain = document.getElementById('tabBtnMain');
  const tabAdv = document.getElementById('tabBtnAdv');
  if (mainPane) mainPane.style.display = isAdv ? 'none' : 'flex';
  if (advPane) advPane.style.display = isAdv ? 'flex' : 'none';
  if (tabMain) tabMain.classList.toggle('active', !isAdv);
  if (tabAdv) tabAdv.classList.toggle('active', isAdv);
}

function toggleTarget3Unlock() {
  const chk = document.getElementById('unlockTarget3');
  const unlocked = chk ? chk.checked : true;
  const opt3 = document.getElementById('optTarget3');
  if (!opt3) return;
  opt3.style.display = unlocked ? '' : 'none';
  opt3.disabled = !unlocked;
  const sel = document.getElementById('targetFormat');
  if (!unlocked && sel && sel.value === 'neoden_yy1_import') {
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
  const fb = document.getElementById('advActionFeedback') || document.getElementById('batchFeedback');
  if (fb) fb.textContent = `Exported Target 3 (YY1 11-Col Import): ${data.download_name}`;
}

async function runAdvancedAction(actionType, param = '') {
  if (!currentData) {
    alert('Please browse and select a file in Main Controls first.');
    return;
  }
  const bodyObj = {
    action: actionType,
    param: param,
    start_feeder: parseInt(document.getElementById('autoFeederStart')?.value || '1', 10) || 1,
    alt_heads: Boolean(document.getElementById('autoAltHeads')?.checked),
    mirror_width_x: parseFloat(document.getElementById('mirrorWidthX')?.value || document.getElementById('yy1UnitLen')?.value || '0') || 0.0,
    ...collectPreviewParams('custom')
  };
  const res = await fetch('/api/advanced_action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(bodyObj)
  });
  const payload = await res.json();
  const fb = document.getElementById('advActionFeedback') || document.getElementById('batchFeedback');
  if (payload.error) {
    if (fb) fb.textContent = 'Error: ' + payload.error;
    return;
  }
  if (fb) fb.textContent = payload.batch_message || 'Applied advanced action.';
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
    yy1_unit_length: parseFloat(document.getElementById('yy1UnitLen').value) || 20.0,
    yy1_unit_width: parseFloat(document.getElementById('yy1UnitWid').value) || 28.5,
    yy1_rows: parseInt(document.getElementById('yy1Rows').value, 10) || 1,
    yy1_columns: parseInt(document.getElementById('yy1Cols').value, 10) || 1,
    yy1_fid_x: parseFloat(document.getElementById('yy1FidX').value) || 0.0,
    yy1_fid_y: parseFloat(document.getElementById('yy1FidY').value) || 0.0,
    yy1_offset_x: parseFloat(document.getElementById('yy1OffX').value) || 0.0,
    yy1_offset_y: parseFloat(document.getElementById('yy1OffY').value) || 0.0,
    yy1_rot_offset: parseFloat(document.getElementById('yy1RotOffset').value) || 0.0,
    invert_rot_sign: document.getElementById('invertRotSign').checked,
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
  for the web UI. Includes NeoDen 4 & YY1 metadata, part groups for feeder setup,
  fiducials, and per-row component details.
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
      "yy1_rot_offset": analysis.yy1_rot_offset,
      "invert_rot_sign": analysis.invert_rot_sign,
      "recommended_angle": analysis.recommended_angle_deg,
      "active_angle": active_angle,
      "recommended_method": analysis.recommended_method,
      "edge_angles": analysis.edge_angles,
      "warnings": analysis.warnings,
      "can_undo": can_undo,
      "can_redo": can_redo,
      "batch_message": batch_message,
      "part_groups": get_part_groups(analysis),
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
              "old_rot": c.rotation,
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
) -> Dict[str, Any]:
  """
  Captures a deep copy of components, fiducials, panels, and YY1 reference settings
  so multi-step Undo and Redo work across inline edits, group edits, origin shifts,
  feeder auto-assignment, component sorting, and bottom-side X-mirroring.
  """
  return {
      "components": copy.deepcopy(analysis.components),
      "fiducials": copy.deepcopy(analysis.fiducials),
      "panels": copy.deepcopy(analysis.panels),
      "yy1_fid_x": analysis.yy1_fid_x,
      "yy1_fid_y": analysis.yy1_fid_y,
  }


def restore_components_snapshot(
    analysis: NeoDenCSVAnalysis,
    snap: Any,
) -> None:
  """Restores the component and coordinate state from an Undo/Redo snapshot."""
  if isinstance(snap, dict):
    analysis.components = copy.deepcopy(snap["components"])
    analysis.fiducials = copy.deepcopy(snap["fiducials"])
    analysis.panels = copy.deepcopy(snap["panels"])
    analysis.yy1_fid_x = snap["yy1_fid_x"]
    analysis.yy1_fid_y = snap["yy1_fid_y"]
  else:
    analysis.components = copy.deepcopy(snap)


def launch_web_gui(
    initial_file: Optional[str] = None,
    port: int = 0,
    open_browser: bool = True,
) -> None:
  """
  Launches the local 2-column Rufus-style HTTP GUI in the user's default browser.
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
        "neoden4" if analysis.source_format == "neoden" else "neoden_yy1"
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
      if req.get("yy1_rot_offset") is not None:
        analysis.yy1_rot_offset = float(req["yy1_rot_offset"])
      if req.get("invert_rot_sign") is not None:
        analysis.invert_rot_sign = bool(req["invert_rot_sign"])

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
          target_format=req.get("target_format", "neoden_yy1"),
          yy1_coord_mode=req.get("yy1_coord_mode", "preserve"),
          yy1_unit_length=analysis.yy1_unit_length,
          yy1_unit_width=analysis.yy1_unit_width,
          yy1_rows=analysis.yy1_rows,
          yy1_columns=analysis.yy1_columns,
          yy1_fid_x=analysis.yy1_fid_x,
          yy1_fid_y=analysis.yy1_fid_y,
          yy1_offset_x=analysis.yy1_offset_x,
          yy1_offset_y=analysis.yy1_offset_y,
          comp_rot_offset_deg=analysis.yy1_rot_offset,
          invert_rot_sign=analysis.invert_rot_sign,
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
              "neoden4" if analysis.source_format == "neoden" else "neoden_yy1"
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
              "Loaded file. Edit part groups on the left or click any cell on the right."
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

        if self.path == "/api/edit_row":
          row_idx = int(req.get("row_index", -1))
          edits = req.get("edits", {})
          snap = snapshot_components(analysis)
          msg = apply_single_row_edit(analysis, row_idx, edits)
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

        if self.path == "/api/edit_part_group":
          group_key = str(req.get("group_key", ""))
          edits = dict(req.get("edits", {}))
          if "row_indices" in req and "row_indices" not in edits:
            edits["row_indices"] = req["row_indices"]
          snap = snapshot_components(analysis)
          msg = apply_part_group_edit(analysis, group_key, edits)
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

        if self.path == "/api/shift_origin":
          snap = snapshot_components(analysis)
          mode = str(req.get("origin_mode") or req.get("mode") or "fid1")
          tx = float(req.get("target_x", 0.0))
          ty = float(req.get("target_y", 0.0))
          msg = shift_board_origin(analysis, mode, tx, ty)
          app_state["undo_stack"].append(snap)
          app_state["redo_stack"].clear()
          app_state["last_batch_msg"] = msg
          if analysis.panels:
            req["p1_x"] = analysis.panels[0].x
            req["p1_y"] = analysis.panels[0].y
          req["yy1_fid_x"] = analysis.yy1_fid_x
          req["yy1_fid_y"] = analysis.yy1_fid_y
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
                start_feeder=int(req.get("start_feeder", 1)),
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
            app_state["last_batch_msg"] = "Undid last modification."
            if analysis.panels:
              req["p1_x"] = analysis.panels[0].x
              req["p1_y"] = analysis.panels[0].y
            req["yy1_fid_x"] = analysis.yy1_fid_x
            req["yy1_fid_y"] = analysis.yy1_fid_y
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
            app_state["last_batch_msg"] = "Redid modification."
            if analysis.panels:
              req["p1_x"] = analysis.panels[0].x
              req["p1_y"] = analysis.panels[0].y
            req["yy1_fid_x"] = analysis.yy1_fid_x
            req["yy1_fid_y"] = analysis.yy1_fid_y
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
          target_fmt = req.get("target_format", "neoden_yy1")
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
              comp_rot_offset_deg=analysis.yy1_rot_offset,
              invert_rot_sign=analysis.invert_rot_sign,
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
  print(f" NeoDen YY1 & NeoDen 4 CSV / KiCad .pos Utility v7.0 at: {url}")
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
          "NeoDen YY1 & NeoDen 4 Pick-and-Place CSV & KiCad .pos Converter"
          " Utility (v7.0). Run without arguments to launch the 2-column GUI."
      )
  )
  parser.add_argument(
      "input_csv",
      nargs="?",
      help="Input KiCad .pos, NeoDen YY1 .csv, or NeoDen 4 .csv file path",
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
          "Target machine CSV format: 'neoden_yy1' (NeoDen YY1 Working CSV:"
          " NEODEN,YY1,P&P FILE), 'neoden4' (NeoDen 4 Job CSV), or"
          " 'neoden_yy1_import' (NeoDen YY1 11-column SD-card Import Template)"
      ),
  )
  parser.add_argument(
      "--yy1",
      action="store_true",
      help="Shortcut for --target-format neoden_yy1 (export NeoDen YY1 Working CSV)",
  )
  parser.add_argument(
      "--rot-offset",
      type=float,
      default=0.0,
      help="Global component/tape rotation offset in degrees (e.g. -90, 90, 180)",
  )
  parser.add_argument(
      "--invert-rot",
      action="store_true",
      help="Invert component rotation sign (-Rot) before adding rotation offsets",
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
          " feeder|footprint|value|nozzle|rot_delta|speed|pick_height|place_height|mode,"
          " VALUE is new value, COMPONENT_NUMS is e.g. '1-12,15' or 'all'"
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
          or args.rot_offset != 0.0
          or args.invert_rot
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
        "neoden4" if analysis.source_format == "neoden" else "neoden_yy1"
    )

  analysis.yy1_rot_offset = float(args.rot_offset)
  analysis.invert_rot_sign = bool(args.invert_rot)

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
      comp_rot_offset_deg=analysis.yy1_rot_offset,
      invert_rot_sign=analysis.invert_rot_sign,
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

  print("=== NeoDen YY1 & NeoDen 4 CSV / KiCad .pos Utility v7.0 Summary ===")
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
  print(f"Board Rotation   : {chosen_angle:+.4f} deg | Tape Offset: {analysis.yy1_rot_offset:+.1f} deg")
  c1 = analysis.components[0]
  print(
      f"1st Comp ({c1.name:3s})   : Orig ({c1.x:.4f}, {c1.y:.4f}) -> Output"
      f" ({c1.new_x:.4f}, {c1.new_y:.4f}) mm"
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

