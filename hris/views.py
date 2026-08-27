"""
hris/views.py
-------------
Two Django views:

  upload_view  — GET: render the file-upload form.
  preview_view — POST: parse the CSV, run hierarchy analysis, render preview.

All heavy lifting lives in parser.py and analyser.py.  The views are
deliberately thin: validate the request, call the library functions, pass
the results to the template, and handle errors gracefully.
"""

from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from hris.parser import parse_csv
from hris.analyser import analyse


MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB guard


@require_http_methods(["GET"])
def upload_view(request):
    return render(request, "upload.html")


@require_http_methods(["POST"])
def preview_view(request):
    # -- 1. Basic request validation -----------------------------------------
    uploaded_file = request.FILES.get("csv_file")
    if not uploaded_file:
        return render(request, "upload.html", {"error": "No file was uploaded. Please choose a CSV file."})

    if not uploaded_file.name.lower().endswith(".csv"):
        return render(request, "upload.html", {"error": "Only .csv files are accepted."})

    if uploaded_file.size > MAX_UPLOAD_BYTES:
        return render(request, "upload.html", {"error": "File exceeds the 10 MB limit."})

    # -- 2. Parse ------------------------------------------------------------
    try:
        parse_result = parse_csv(uploaded_file)
    except ValueError as exc:
        return render(request, "upload.html", {"error": str(exc)})
    except Exception as exc:
        return render(request, "upload.html", {"error": f"Unexpected error while reading the file: {exc}"})

    # -- 3. Analyse ----------------------------------------------------------
    try:
        analysis = analyse(parse_result.valid_rows)
    except Exception as exc:
        return render(request, "upload.html", {"error": f"Unexpected error during analysis: {exc}"})

    # -- 4. Render -----------------------------------------------------------
    context = {
        "filename": uploaded_file.name,
        # Summary counts
        "total_source_rows": parse_result.total_source_rows,
        "accepted_count": len(analysis.accepted),
        "invalid_count": len(parse_result.invalid_rows),
        "error_count": len(analysis.manager_errors),
        # Detail lists
        "invalid_rows": parse_result.invalid_rows,
        "roots": analysis.roots,
        "managers": sorted(analysis.managers, key=lambda m: m.employee_id),
        "cycle_members": sorted(analysis.cycle_members, key=lambda c: c.employee_id),
        "manager_errors": [
            {"employee": eid, "error": msg}
            for eid, msg in analysis.manager_errors.items()
        ],
    }

    return render(request, "preview.html", context)
