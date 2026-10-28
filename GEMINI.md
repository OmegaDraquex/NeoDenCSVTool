# NeoDen Pick-and-Place & KiCad Tool Guidelines

## 1. NeoDen Machine Domain Specifications

### NeoDen YY1 (Working CSV Specification)
- **Column Layout (13 Columns)**:
  `Designator,Comment,Footprint,Mid X,Mid Y,Rotation,Head,FeederNo,Mount Speed (%),Pick Height(mm),Place Height(mm),Mode,Skip`
- **Vision Mode (Col 12 `Mode`)**:
  - `Mode 3`: **Default safe mode** (Camera Alignment + Vacuum Presence Check). Verifies part is held before placing.
  - `Mode 1`: Camera only (Standard fast recognition).
  - `Mode 4`: Big IC (Enlarged FOV camera for LQFP, TQFP, QFP, QFN, BGA).
  - `Mode 0`: Fast / Bypass Vision (no camera, no vacuum check).
  - `Mode 2`: Vacuum check only.
- **Skip Logic (Col 13 `Skip`)**:
  - `0` = Place (equivalent to `No` in NeoDen 4).
  - `1` = Skip (equivalent to `Yes` in NeoDen 4).
- **Physical Heads**:
  - Physical heads are strictly `1` and `2`. Never output `3` or `4` for YY1.
  - Map/clamp odd nozzles (`1`, `3`) to Head 1, and even nozzles (`2`, `4`) to Head 2.
- **Feeder Zones**:
  - `1–52`: Tape Feeders.
  - `53–80`: Stick / Tray / Matrix Feeders.
  - `81–99`: Bulk / Loose Feeders.
- **Panelization**:
  - Default to single board: `Rows=1, Columns=1`.
  - Panel 2 should be inactive/hidden by default unless the operator specifies multi-paneling.

### NeoDen 4 (Production CSV Specification)
- **Column Layout**: `#Feeder` / `stack` tables, pcb clamp lines, `mirror` / `mark` fiducials, and `comp` lines:
  `comp,Feeder,Nozzle,Designator,Value,Footprint,X,Y,Rotation,Skip`
- **Skip Values**: Text-based `No` (Place) and `Yes` (Skip).
- **Nozzles**: Physical nozzles `1` through `4`.
- **Feeders**: Numbered `1–48` (Front: 1–19, Rear: 20–38, Tray/Special: 39–48).

---

## 2. Embedded Web GUI Architecture & Escaping Invariants

- **Backslash Escaping in Python Multiline Strings**:
  - When writing JavaScript or CSS inside Python string literals (`"""..."""`), backslashes must be double-escaped:
    - Use `\\n` instead of `\n` in JS string joins (e.g. `lines.join('\\n')`).
    - Use `\\D`, `\\s`, `\\d` in JavaScript regular expressions.
  - Alternatively, use raw string literals `r"""..."""` when no Python format substitutions exist.
- **Syntax Verification**:
  - Before committing changes that affect the embedded web GUI, always run `python -m py_compile` to catch escape sequence warnings, and validate JavaScript syntax (e.g. using `node --check` on extracted script blocks).
- **Table Header & Cell Alignment**:
  - The `<thead>` column count and ordering must match `<tbody>` cell rendering 1-to-1 in both `group-table` and `comp-table`.
