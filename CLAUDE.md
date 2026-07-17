# agents.md

## Instructions
For each domain of the CB3 equity report, produce a `scripts/<domain>/<domain>.py` cleaning script and a `scripts/<domain>/<domain>_maps.qmd` mapping notebook (see `README.md` for the full collaborator guide and shared conventions):

a. Use `docs/proposed_concept_for_CB3_equity_report_v3.pdf` and `docs/CB3_Data_Reference_by_Domain_v4.csv` as project context.

b. For each domain, find the relevant data source in its `data/raw/<Domain>/` folder and pull the information for each metric.

c. Clean and format the raw data into the appropriate output shape (see Output below) — tract-level, point-level, or both, depending on the metric's native geography.

d. Flag if the data is not already at the tract level (e.g., a different geographic unit). Some data may have to be geocoded and spatially joined into a tract.

e. Register every metric in `docs/metric_metadata.yml` (labels, units, and — for demographic backdrop layers only — a palette). This is the only place those are defined; don't hardcode them in scripts or notebooks.

f. Build the domain's Folium maps in the `.qmd` notebook using the shared helpers in `src/map_utils.py` (selectable demographic choropleths as backdrop, the domain's own metrics as bubble/point layers on top), and render with `quarto render` to confirm the maps actually display before considering the notebook done.


### Code
- Be parsimonious in your code; do not implement more than what I ask for.
- Use # %% cell blocks throughout, so it can be run interactively in VS Code
- Should be written in Python. Use `pandas` and `geopandas` to wrangle the data. Mapping should be done in `Folium` unless otherwise specified.
- Write out all dependencies in a `requirements.txt`
- Add verbose comments for each chunk of code. Each function should have a docstring.

### Output
- Clean data should go in data/clean/[domain].csv
- The output file should be a CSV with one row for each tract (GEOID).

### Rules
- Only use the local data in the data/ folder. Do not do a web search for more data.
- Ask for clarification if there is any uncertainty. 
- Propose an approach and wait for approval before writing code
- After each function, summarize what it does and what should be verified
- Flag any assumptions made about data structure or column names
