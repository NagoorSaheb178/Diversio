"""
hris/tests.py
-------------
Automated tests for the HRIS import preview application.

Tests are grouped into three classes:
  ParserTests   — CSV parsing and row-level validation (parser.py)
  AnalyserTests — hierarchy analysis and cycle detection (analyser.py)
  ViewTests     — HTTP upload form and full round-trip (views.py)

All tests call the library functions directly — no browser, no running server.
"""

import io
from django.test import TestCase, Client
from django.urls import reverse

from hris.parser import parse_csv, ParseResult, EmployeeRow
from hris.analyser import analyse, AnalysisResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HEADER = "employee_id,employee_name,email,manager_id,manager_email,department\n"


def make_file(content: str) -> io.BytesIO:
    """Wrap a CSV string as a file-like object the parser accepts."""
    return io.BytesIO(content.encode("utf-8"))


def parse(csv_body: str) -> ParseResult:
    """Convenience: parse a CSV body (header prepended automatically)."""
    return parse_csv(make_file(HEADER + csv_body))


def make_rows(*specs) -> list:
    """
    Build EmployeeRow objects from tuples:
      (employee_id, employee_name, email, manager_id, manager_email, department)
    source_row starts at 2 (row 1 is the header).
    """
    rows = []
    for i, spec in enumerate(specs, start=2):
        eid, name, email, mgr_id, mgr_email, dept = spec
        rows.append(EmployeeRow(
            source_row=i,
            employee_id=eid,
            employee_name=name,
            email=email,
            manager_id=mgr_id,
            manager_email=mgr_email,
            department=dept,
        ))
    return rows


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class ParserTests(TestCase):

    # -- Happy-path -----------------------------------------------------------

    def test_valid_row_is_accepted(self):
        """A well-formed row with all required fields lands in valid_rows."""
        result = parse("EMP-1,Alice Smith,alice@example.com,,,Engineering\n")
        self.assertEqual(len(result.valid_rows), 1)
        self.assertEqual(len(result.invalid_rows), 0)
        emp = result.valid_rows[0]
        self.assertEqual(emp.employee_id, "EMP-1")
        self.assertEqual(emp.email, "alice@example.com")

    def test_total_source_rows_matches(self):
        """total_source_rows equals the number of data rows (header excluded)."""
        csv_body = (
            "EMP-1,Alice,alice@example.com,,,Engineering\n"
            "EMP-2,Bob,bob@example.com,,,Product\n"
        )
        result = parse(csv_body)
        self.assertEqual(result.total_source_rows, 2)

    def test_quoted_name_with_comma(self):
        """A name containing a comma (quoted) parses as a single field."""
        result = parse('"EMP-1","Alvarez, Renée",renee@example.com,,,Operations\n')
        self.assertEqual(len(result.valid_rows), 1)
        self.assertEqual(result.valid_rows[0].employee_name, "Alvarez, Renée")

    # -- Source row numbers ---------------------------------------------------

    def test_source_row_header_is_row_1_first_data_is_row_2(self):
        """
        The CSV header is physical line 1.
        The first employee data row must have source_row == 2.
        """
        result = parse("EMP-1,Alice,alice@example.com,,,Engineering\n")
        self.assertEqual(result.valid_rows[0].source_row, 2)

    def test_source_row_second_data_row_is_3(self):
        """The second data row must have source_row == 3."""
        csv_body = (
            "EMP-1,Alice,alice@example.com,,,Engineering\n"
            "EMP-2,Bob,bob@example.com,,,Product\n"
        )
        result = parse(csv_body)
        self.assertEqual(result.valid_rows[1].source_row, 3)

    def test_source_row_of_invalid_row(self):
        """Invalid rows must carry the correct source_row number."""
        csv_body = (
            "EMP-1,Alice,alice@example.com,,,Engineering\n"   # row 2 — valid
            ",Bob,bob@example.com,,,Product\n"                # row 3 — invalid
        )
        result = parse(csv_body)
        self.assertEqual(result.invalid_rows[0].source_row, 3)

    def test_source_row_with_bom(self):
        """
        A UTF-8 BOM must not shift source row numbers.
        With BOM: header is still physical line 1, first data row is still 2.
        """
        content = b"\xef\xbb\xbf" + (HEADER + "EMP-1,Alice,alice@example.com,,,Eng\n").encode("utf-8")
        result = parse_csv(io.BytesIO(content))
        self.assertEqual(len(result.valid_rows), 1)
        self.assertEqual(result.valid_rows[0].source_row, 2)

    # -- Normalisation --------------------------------------------------------

    def test_email_is_lowercased(self):
        """email and manager_email must be lowercased."""
        result = parse("EMP-1,Alice,ALICE@EXAMPLE.COM,,MANAGER@EXAMPLE.COM,Eng\n")
        emp = result.valid_rows[0]
        self.assertEqual(emp.email, "alice@example.com")
        self.assertEqual(emp.manager_email, "manager@example.com")

    def test_employee_id_case_preserved(self):
        """employee_id is case-sensitive; case must not be altered."""
        result = parse("Emp-ABC,Alice,alice@example.com,,,Engineering\n")
        self.assertEqual(result.valid_rows[0].employee_id, "Emp-ABC")

    def test_whitespace_trimmed(self):
        """Leading/trailing whitespace is stripped from every field."""
        result = parse("  EMP-1  ,  Alice  ,  alice@example.com  ,,,  Engineering  \n")
        emp = result.valid_rows[0]
        self.assertEqual(emp.employee_id, "EMP-1")
        self.assertEqual(emp.employee_name, "Alice")
        self.assertEqual(emp.department, "Engineering")

    def test_bom_stripped(self):
        """UTF-8 BOM is stripped so the header is read correctly."""
        content = b"\xef\xbb\xbf" + (HEADER + "EMP-1,Alice,alice@example.com,,,Eng\n").encode("utf-8")
        result = parse_csv(io.BytesIO(content))
        self.assertEqual(len(result.valid_rows), 1)

    # -- Required-field validation -------------------------------------------

    def test_missing_employee_id_is_invalid(self):
        """A row without employee_id must be rejected with a clear error."""
        result = parse(",Alice Smith,alice@example.com,,,Engineering\n")
        self.assertEqual(len(result.valid_rows), 0)
        self.assertEqual(len(result.invalid_rows), 1)
        errors = result.invalid_rows[0].errors
        self.assertTrue(any("employee_id" in e for e in errors))

    def test_missing_email_is_invalid(self):
        """A row without email must be rejected with a clear error."""
        result = parse("EMP-1,Alice Smith,,,,Engineering\n")
        self.assertEqual(len(result.valid_rows), 0)
        self.assertEqual(len(result.invalid_rows), 1)
        errors = result.invalid_rows[0].errors
        self.assertTrue(any("email" in e for e in errors))

    # -- Duplicate-identity validation ----------------------------------------

    def test_duplicate_employee_id_both_invalid(self):
        """
        When two rows share an employee_id, BOTH must be invalid and excluded
        from analysis — not just the second occurrence.
        """
        csv_body = (
            "EMP-1,Alice Smith,alice@example.com,,,Engineering\n"
            "EMP-1,Alice Duplicate,alice2@example.com,,,Product\n"
        )
        result = parse(csv_body)
        self.assertEqual(len(result.valid_rows), 0,
                         "Both rows sharing a duplicate ID should be invalid")
        self.assertEqual(len(result.invalid_rows), 2)
        for row in result.invalid_rows:
            self.assertTrue(any("Duplicate employee_id" in e for e in row.errors))

    def test_duplicate_email_invalidates_both_rows_after_lowercasing(self):
        """
        Duplicate email detection happens on the normalised (lowercased) value.
        ALICE@example.com and alice@example.com are the same email —
        both rows must be invalid.
        """
        csv_body = (
            "EMP-1,Alice Smith,ALICE@example.com,,,Engineering\n"
            "EMP-2,Alice Dupe,alice@example.com,,,Product\n"
        )
        result = parse(csv_body)
        self.assertEqual(len(result.valid_rows), 0,
                         "Both rows sharing a duplicate normalised email should be invalid")
        self.assertEqual(len(result.invalid_rows), 2)
        for row in result.invalid_rows:
            self.assertTrue(any("Duplicate email" in e for e in row.errors))

    def test_duplicate_employee_id_excluded_from_manager_lookup(self):
        """
        Both rows with a duplicate employee_id are invalid.
        Neither may be used as a manager for another employee.
        """
        csv_body = (
            "EMP-1,Alice,alice@example.com,EMP-DUP,,Engineering\n"   # manager ref
            "EMP-DUP,Bob,bob@example.com,,,Engineering\n"            # both invalid
            "EMP-DUP,Bob2,bob2@example.com,,,Engineering\n"
        )
        result = parse(csv_body)
        # EMP-1 is valid; EMP-DUP rows are invalid
        valid_ids = {r.employee_id for r in result.valid_rows}
        self.assertIn("EMP-1", valid_ids)
        self.assertNotIn("EMP-DUP", valid_ids)
        # Now analyse: EMP-1's manager is not in valid_rows → manager error
        analysis = analyse(result.valid_rows)
        self.assertIn("EMP-1", analysis.manager_errors)

    # -- Header validation ----------------------------------------------------

    def test_missing_header_raises_value_error(self):
        """A CSV missing a required column must raise ValueError."""
        content = "employee_id,employee_name,email\nEMP-1,Alice,alice@example.com\n"
        with self.assertRaises(ValueError):
            parse_csv(make_file(content))


# ---------------------------------------------------------------------------
# Analyser tests
# ---------------------------------------------------------------------------

class AnalyserTests(TestCase):

    # -- Root detection -------------------------------------------------------

    def test_root_has_no_manager(self):
        """An employee with no manager fields must appear in roots."""
        rows = make_rows(
            ("EMP-1", "Alice", "alice@x.com", "", "", "Engineering"),
        )
        result = analyse(rows)
        self.assertEqual(len(result.roots), 1)
        self.assertEqual(result.roots[0].employee_id, "EMP-1")
        self.assertEqual(len(result.manager_errors), 0)

    # -- Manager resolution ---------------------------------------------------

    def test_manager_resolved_by_id(self):
        """When only manager_id is supplied, it resolves by employee ID."""
        rows = make_rows(
            ("MGR-1", "Manager", "mgr@x.com", "", "", "Eng"),
            ("EMP-1", "Alice",   "alice@x.com", "MGR-1", "", "Eng"),
        )
        result = analyse(rows)
        self.assertNotIn("EMP-1", result.manager_errors)
        mgr_entry = next((m for m in result.managers if m.employee_id == "MGR-1"), None)
        self.assertIsNotNone(mgr_entry)
        self.assertEqual(mgr_entry.direct_report_count, 1)

    def test_manager_resolved_by_email(self):
        """When only manager_email is supplied, it resolves by normalised email."""
        rows = make_rows(
            ("MGR-1", "Manager", "mgr@x.com", "", "", "Eng"),
            ("EMP-1", "Alice",   "alice@x.com", "", "mgr@x.com", "Eng"),
        )
        result = analyse(rows)
        self.assertNotIn("EMP-1", result.manager_errors)

    def test_both_manager_refs_agree(self):
        """Both manager_id and manager_email resolving to the SAME employee — no error."""
        rows = make_rows(
            ("MGR-1", "Manager", "mgr@x.com", "", "", "Eng"),
            ("EMP-1", "Alice",   "alice@x.com", "MGR-1", "mgr@x.com", "Eng"),
        )
        result = analyse(rows)
        self.assertNotIn("EMP-1", result.manager_errors)

    def test_manager_can_appear_after_report(self):
        """Manager rows may appear after their direct reports in the file."""
        rows = make_rows(
            ("EMP-1", "Alice",   "alice@x.com", "MGR-1", "", "Eng"),  # report first
            ("MGR-1", "Manager", "mgr@x.com",   "",      "", "Eng"),
        )
        result = analyse(rows)
        self.assertNotIn("EMP-1", result.manager_errors)
        self.assertIn("MGR-1", [r.employee_id for r in result.roots])

    # -- Manager error cases --------------------------------------------------

    def test_manager_not_found_error(self):
        """
        When the referenced manager ID does not exist, a manager error is
        recorded and the employee is NOT a root.
        """
        rows = make_rows(
            ("EMP-1", "Alice", "alice@x.com", "GHOST-99", "", "Eng"),
        )
        result = analyse(rows)
        self.assertIn("EMP-1", result.manager_errors)
        self.assertNotIn("EMP-1", [r.employee_id for r in result.roots])

    def test_manager_email_not_found_error(self):
        """When only manager_email is given and cannot be found, it is an error."""
        rows = make_rows(
            ("EMP-1", "Alice", "alice@x.com", "", "ghost@x.com", "Eng"),
        )
        result = analyse(rows)
        self.assertIn("EMP-1", result.manager_errors)

    def test_conflicting_manager_references(self):
        """
        manager_id and manager_email both resolve, but to DIFFERENT employees
        — must produce a conflict error.
        """
        rows = make_rows(
            ("MGR-A", "ManagerA", "mgra@x.com", "", "", "Eng"),
            ("MGR-B", "ManagerB", "mgrb@x.com", "", "", "Eng"),
            ("EMP-1", "Alice",    "alice@x.com", "MGR-A", "mgrb@x.com", "Eng"),
        )
        result = analyse(rows)
        self.assertIn("EMP-1", result.manager_errors)
        self.assertIn("conflict", result.manager_errors["EMP-1"].lower())

    def test_self_managing_employee_is_error(self):
        """
        An employee who references themselves as manager must produce an error.
        They remain accepted but are not a root.
        """
        rows = make_rows(
            ("EMP-1", "Alice", "alice@x.com", "EMP-1", "", "Eng"),
        )
        result = analyse(rows)
        self.assertIn("EMP-1", result.manager_errors)
        self.assertEqual(len(result.accepted), 1)
        self.assertNotIn("EMP-1", [r.employee_id for r in result.roots])

    def test_manager_error_employee_remains_accepted(self):
        """Employees with manager errors are accepted employees, not invalid rows."""
        rows = make_rows(
            ("EMP-1", "Alice", "alice@x.com", "MISSING", "", "Eng"),
        )
        result = analyse(rows)
        self.assertIn("EMP-1", result.manager_errors)
        accepted_ids = {r.employee_id for r in result.accepted}
        self.assertIn("EMP-1", accepted_ids)

    def test_manager_error_creates_no_reporting_relationship(self):
        """
        An employee with a manager error must not appear as a direct report
        in the managers list.
        """
        rows = make_rows(
            ("EMP-ROOT", "Root", "root@x.com", "", "", "Eng"),
            ("EMP-1",    "Alice", "alice@x.com", "GHOST", "", "Eng"),
        )
        result = analyse(rows)
        # EMP-ROOT has no resolved reports — it should not appear as a manager
        mgr_ids = {m.employee_id for m in result.managers}
        self.assertNotIn("EMP-ROOT", mgr_ids)

    # -- Cycle detection ------------------------------------------------------

    def test_cycle_detection_three_node_cycle_marks_exactly_three(self):
        """
        A three-node cycle (A→B→C→A) must flag exactly A, B, and C as
        cycle members — no more, no fewer.
        """
        rows = make_rows(
            ("A", "Alice", "a@x.com", "C", "", "Eng"),
            ("B", "Bob",   "b@x.com", "A", "", "Eng"),
            ("C", "Carol", "c@x.com", "B", "", "Eng"),
        )
        result = analyse(rows)
        cycle_ids = {e.employee_id for e in result.cycle_members}
        self.assertEqual(cycle_ids, {"A", "B", "C"},
                         "Exactly the three cycle nodes must be flagged")

    def test_reporter_into_cycle_is_not_a_cycle_member(self):
        """
        Per spec: 'Do not classify an employee as cyclic merely because they
        report into a cycle.'

        D → A → B → C → A  (A,B,C form a cycle; D reports into it)
        D must NOT appear in cycle_members.
        """
        rows = make_rows(
            ("A", "Alice", "a@x.com", "C", "", "Eng"),   # A→C (cycle)
            ("B", "Bob",   "b@x.com", "A", "", "Eng"),   # B→A (cycle)
            ("C", "Carol", "c@x.com", "B", "", "Eng"),   # C→B (cycle)
            ("D", "Diana", "d@x.com", "A", "", "Eng"),   # D→A (reports INTO cycle)
        )
        result = analyse(rows)
        cycle_ids = {e.employee_id for e in result.cycle_members}

        self.assertIn("A", cycle_ids, "A is inside the cycle")
        self.assertIn("B", cycle_ids, "B is inside the cycle")
        self.assertIn("C", cycle_ids, "C is inside the cycle")
        self.assertNotIn("D", cycle_ids,
                         "D merely reports into the cycle and must NOT be a cycle member")

    def test_two_node_cycle(self):
        """A mutual-management pair (A→B, B→A) are both cycle members."""
        rows = make_rows(
            ("A", "Alice", "a@x.com", "B", "", "Eng"),
            ("B", "Bob",   "b@x.com", "A", "", "Eng"),
        )
        result = analyse(rows)
        cycle_ids = {e.employee_id for e in result.cycle_members}
        self.assertEqual(cycle_ids, {"A", "B"})

    def test_no_cycles(self):
        """A linear chain produces zero cycle members."""
        rows = make_rows(
            ("ROOT", "Root",  "root@x.com",  "", "", "Eng"),
            ("MID",  "Mid",   "mid@x.com",  "ROOT", "", "Eng"),
            ("LEAF", "Leaf",  "leaf@x.com", "MID",  "", "Eng"),
        )
        result = analyse(rows)
        self.assertEqual(len(result.cycle_members), 0)

    def test_empty_input(self):
        """analyse([]) must return an empty AnalysisResult without raising."""
        result = analyse([])
        self.assertEqual(result.roots, [])
        self.assertEqual(result.cycle_members, [])
        self.assertEqual(result.managers, [])
        self.assertEqual(result.manager_errors, {})


# ---------------------------------------------------------------------------
# View / integration tests
# ---------------------------------------------------------------------------

class ViewTests(TestCase):

    def setUp(self):
        self.client = Client()

    def test_upload_page_get(self):
        """GET / returns 200 and renders the upload form."""
        response = self.client.get(reverse("upload"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "csv_file")

    def test_preview_with_no_file_returns_error(self):
        """POST /preview/ without a file returns 200 with a readable error."""
        response = self.client.post(reverse("preview"), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No file was uploaded")

    def test_preview_valid_csv(self):
        """A valid CSV upload renders the preview page successfully."""
        csv_content = (
            HEADER
            + "EMP-1,Alice,alice@example.com,,,Engineering\n"
            + "EMP-2,Bob,bob@example.com,EMP-1,,Engineering\n"
        )
        f = io.BytesIO(csv_content.encode("utf-8"))
        f.name = "test.csv"
        response = self.client.post(reverse("preview"), {"csv_file": f})
        self.assertEqual(response.status_code, 200)
        # Page must show the total source row count
        self.assertContains(response, "2")

    def test_preview_non_csv_rejected(self):
        """Uploading a non-.csv extension returns an error message."""
        f = io.BytesIO(b"some data")
        f.name = "data.xlsx"
        response = self.client.post(reverse("preview"), {"csv_file": f})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only .csv files")

    def test_preview_malformed_csv_missing_header(self):
        """A CSV without required headers returns an error, not a 500."""
        f = io.BytesIO(b"col1,col2\nval1,val2\n")
        f.name = "bad.csv"
        response = self.client.post(reverse("preview"), {"csv_file": f})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "missing")
