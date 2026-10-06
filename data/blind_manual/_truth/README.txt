Teammate blind test
===================
1. Edit the files in ../raw/ (Excel/LibreOffice for CSV, QGIS for GeoJSON). Do not read the rules first.
2. For every edit add a row to manual_errors.csv: plot_id (e.g. 12/3), error_kind, what you changed.
   error_kind is one of: owner_name, sale_not_mutated, extent, boundary, missing_revenue_record, missing_tax_record, land_use, floors, utility_line
3. Run:  python -m landconnect.blind score
