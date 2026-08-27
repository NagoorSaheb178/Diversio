"""
hris/analyser.py
----------------
Hierarchy and cycle analysis over the set of *valid* employee rows.

Responsibilities
----------------
* Build an in-memory employee lookup (by id and by email).
* Resolve each employee's manager reference, reporting conflicts or not-found
  errors.
* Classify employees as roots (no manager, no error), orphans (manager error),
  or regular reports.
* Detect *true* reporting cycles using a three-colour DFS.
  Per the spec: "Do not classify an employee as cyclic merely because they
  report into a cycle." Only employees that are themselves inside a cycle are
  marked cyclic.
* Produce a manager→direct-reports mapping for all resolved relationships.

No Django imports — framework-free for easy unit testing.

Time / space complexity
-----------------------
For N valid employees:
  * Lookup table construction:  O(N)
  * Manager resolution:         O(N)  — one dict lookup per employee
  * Cycle detection (DFS):      O(N + E) where E ≤ N (each employee has at
    most one manager, so the graph has at most N edges)
  Overall: O(N) time, O(N) space.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from hris.parser import EmployeeRow


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

@dataclass
class ManagerEntry:
    """One manager and their direct reports."""
    employee_id: str
    employee_name: str
    direct_report_count: int
    direct_report_names: List[str]


@dataclass
class AnalysisResult:
    """Everything the view needs to render the import preview."""

    # Employees with no manager and no manager error — true roots of the tree.
    roots: List[EmployeeRow]

    # Managers with at least one resolved direct report.
    managers: List[ManagerEntry]

    # Employees that are *inside* a reporting cycle.
    # Per the spec: "Do not classify an employee as cyclic merely because
    # they report into a cycle."
    cycle_members: List[EmployeeRow]

    # Per-employee manager resolution errors (employee_id → error message).
    manager_errors: Dict[str, str]

    # All accepted employees (mirrors valid_rows).
    accepted: List[EmployeeRow]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_lookups(
    rows: List[EmployeeRow],
) -> tuple[Dict[str, EmployeeRow], Dict[str, EmployeeRow]]:
    """Return (id_lookup, email_lookup) dicts over the given valid rows."""
    by_id: Dict[str, EmployeeRow] = {}
    by_email: Dict[str, EmployeeRow] = {}
    for row in rows:
        by_id[row.employee_id] = row
        by_email[row.email] = row
    return by_id, by_email


def _resolve_manager(
    row: EmployeeRow,
    by_id: Dict[str, EmployeeRow],
    by_email: Dict[str, EmployeeRow],
) -> tuple[Optional[EmployeeRow], Optional[str]]:
    """
    Resolve the manager for *row* following the spec rules.

    Returns (manager_row, error_message). Exactly one of the two will be None.
    """
    has_id = bool(row.manager_id)
    has_email = bool(row.manager_email)

    if not has_id and not has_email:
        return None, None  # root — no error

    manager_by_id: Optional[EmployeeRow] = None
    manager_by_email: Optional[EmployeeRow] = None

    if has_id:
        manager_by_id = by_id.get(row.manager_id)
        if manager_by_id is None:
            if not has_email:
                return None, f"Manager ID '{row.manager_id}' not found"
        # Self-manage check
        if manager_by_id is not None and manager_by_id.employee_id == row.employee_id:
            return None, "Employee cannot be their own manager"

    if has_email:
        manager_by_email = by_email.get(row.manager_email)
        if manager_by_email is None:
            if not has_id:
                return None, f"Manager email '{row.manager_email}' not found"
        if manager_by_email is not None and manager_by_email.employee_id == row.employee_id:
            return None, "Employee cannot be their own manager"

    # Both supplied — must agree
    if has_id and has_email:
        if manager_by_id is None and manager_by_email is None:
            return None, (
                f"Manager ID '{row.manager_id}' and email '{row.manager_email}' both not found"
            )
        if manager_by_id is None:
            return None, (
                f"Manager ID '{row.manager_id}' not found "
                f"(manager_email resolves to '{manager_by_email.employee_id}')"
            )
        if manager_by_email is None:
            return None, (
                f"Manager email '{row.manager_email}' not found "
                f"(manager_id resolves to '{manager_by_id.employee_id}')"
            )
        # Both resolved — check they agree
        if manager_by_id.employee_id != manager_by_email.employee_id:
            return None, (
                f"manager_id '{row.manager_id}' resolves to '{manager_by_id.employee_name}' "
                f"but manager_email '{row.manager_email}' resolves to '{manager_by_email.employee_name}' "
                f"— they conflict"
            )
        # They agree
        return manager_by_id, None

    # Only one supplied and it resolved
    if has_id:
        return manager_by_id, None
    return manager_by_email, None


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------

WHITE, GRAY, BLACK = 0, 1, 2


def _find_cycle_members(
    parent: Dict[str, str],          # employee_id → manager_id (resolved edges only)
    all_ids: Set[str],
) -> Set[str]:
    """
    Three-colour iterative DFS on the manager graph.

    Returns the set of employee IDs that are themselves *inside* a cycle.

    A node is "in a cycle" if, during DFS, we reach it again while it is still
    GRAY (i.e., on the current DFS stack).  We then walk back along the stack
    from that node to the revisited node and mark every node in that segment as
    a cycle member.

    Employees who merely *report into* a cycle (their chain reaches a cycle but
    they are not part of it) are NOT included — only true cycle participants are.

    Complexity: O(N + E) time, O(N) space, where E ≤ N.
    """
    colour: Dict[str, int] = {eid: WHITE for eid in all_ids}
    cycle_members: Set[str] = set()

    def dfs(start: str) -> None:
        stack = [(start, iter(
            [parent[start]] if start in parent else []
        ))]
        path: List[str] = [start]
        colour[start] = GRAY

        while stack:
            node, children = stack[-1]
            try:
                child = next(children)
                if child not in colour:
                    continue  # child is outside valid employees (already handled as error)
                if colour[child] == GRAY:
                    # Found a cycle — mark everyone from child to end of path.
                    # Nodes before path.index(child) are upstream visitors that
                    # merely lead into the cycle; they are intentionally excluded.
                    idx = path.index(child)
                    for member in path[idx:]:
                        cycle_members.add(member)
                elif colour[child] == WHITE:
                    colour[child] = GRAY
                    path.append(child)
                    stack.append((child, iter(
                        [parent[child]] if child in parent else []
                    )))
            except StopIteration:
                colour[node] = BLACK
                path.pop()
                stack.pop()

    for eid in all_ids:
        if colour[eid] == WHITE:
            dfs(eid)

    return cycle_members


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyse(valid_rows: List[EmployeeRow]) -> AnalysisResult:
    """
    Build the full hierarchy analysis from a list of valid employee rows.

    The function:
    1. Builds id/email lookup tables.
    2. Resolves each employee's manager, collecting errors.
    3. Identifies roots and builds manager→reports mapping.
    4. Runs cycle detection — only true cycle members are flagged.
    """
    if not valid_rows:
        return AnalysisResult(
            roots=[],
            managers=[],
            cycle_members=[],
            manager_errors={},
            accepted=[],
        )

    by_id, by_email = _build_lookups(valid_rows)
    manager_errors: Dict[str, str] = {}

    # resolved_parent[eid] = manager_eid  (only for clean resolutions)
    resolved_parent: Dict[str, str] = {}
    # direct_reports[manager_eid] = [report_eid, ...]
    direct_reports: Dict[str, List[str]] = {row.employee_id: [] for row in valid_rows}

    roots: List[EmployeeRow] = []

    for row in valid_rows:
        manager_row, error = _resolve_manager(row, by_id, by_email)
        if error:
            manager_errors[row.employee_id] = error
            # The employee remains accepted but has no resolved relationship
        elif manager_row is None:
            # No manager supplied → root
            roots.append(row)
        else:
            resolved_parent[row.employee_id] = manager_row.employee_id
            direct_reports[manager_row.employee_id].append(row.employee_id)

    # Build ManagerEntry list (managers with ≥1 direct report)
    managers: List[ManagerEntry] = []
    for row in valid_rows:
        reports = direct_reports.get(row.employee_id, [])
        if reports:
            managers.append(ManagerEntry(
                employee_id=row.employee_id,
                employee_name=row.employee_name,
                direct_report_count=len(reports),
                direct_report_names=[by_id[r].employee_name for r in reports],
            ))

    # Cycle detection — three-colour DFS marks only true cycle members.
    # Employees who merely report *into* a cycle are NOT included here.
    all_ids = {row.employee_id for row in valid_rows}
    cycle_member_ids = _find_cycle_members(resolved_parent, all_ids)
    cycle_members = [by_id[eid] for eid in cycle_member_ids if eid in by_id]

    return AnalysisResult(
        roots=roots,
        managers=managers,
        cycle_members=cycle_members,
        manager_errors=manager_errors,
        accepted=valid_rows,
    )
