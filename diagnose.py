import sys, os
sys.path.insert(0, '.')
os.environ['DJANGO_SETTINGS_MODULE'] = 'diverso_project.settings'
import django
django.setup()

from hris.parser import parse_csv
from hris.analyser import analyse

with open(r'C:\Users\nagoo\Downloads\diverso\sample_hris.csv', 'rb') as f:
    pr = parse_csv(f)

ar = analyse(pr.valid_rows)

print('=== CSV Diagnostics ===')
print(f'Total source rows  : {pr.total_source_rows}')
print(f'Valid (accepted)   : {len(pr.valid_rows)}')
print(f'Invalid rows       : {len(pr.invalid_rows)}')
print(f'Manager errors     : {len(ar.manager_errors)}')
print(f'Root employees     : {len(ar.roots)}')
print(f'Cycle members      : {len(ar.cycle_members)}')
print()
print('--- Invalid rows ---')
for r in pr.invalid_rows:
    no_id = r.raw.get('employee_id') or '(none)'
    print(f'  row {r.source_row}: id={no_id!r} | {r.errors}')
print()
print('--- Manager errors ---')
for eid, msg in ar.manager_errors.items():
    print(f'  {eid}: {msg}')
print()
print('--- Roots ---')
for r in ar.roots:
    print(f'  {r.employee_id} ({r.employee_name})')
print()
print('--- Cycle members ---')
for r in ar.cycle_members:
    print(f'  {r.employee_id} ({r.employee_name})')
print()
