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
- Data-cleaning scripts (`scripts/<domain>/<domain>.py`) use `# %%` cell blocks throughout, so they can be run interactively in VS Code. Mapping notebooks (`scripts/<domain>/<domain>_maps.qmd`) are Quarto documents using ` ```{python} ` cells instead, rendered with `quarto render` — do not use `# %%` blocks in `.qmd` files.
- Should be written in Python. Use `pandas`, `geopandas`, `shapely`, and `rasterio` to wrangle the data. Mapping should be done in `Folium` unless otherwise specified.
- Write out all dependencies in a `requirements.txt`
- Add verbose comments for each chunk of code. Each function should have a docstring.

### Output
- Clean data should go in `data/clean/`.
- **Tract-level CSV** (`data/clean/<domain>_tract.csv` or `data/clean/<domain>.csv`): one row per census tract (GEOID), one column per metric. This is the default shape and every domain produces one.
- **Point-level CSV** (`data/clean/<domain>_<metric_group>_points.csv`), optional, one per metric or related group of metrics: one row per building/school/site, used instead of (or alongside) the tract-level CSV when a metric has native, finer-grained coordinates that would lose information if collapsed to a tract centroid (e.g. individual schools, subsidized buildings, rodent inspection sites).
- **Other units**, optional, as the domain's source data requires (e.g. DSNY cleaning sections, PM2.5 grid cells) — same one-row-per-unit principle, with a GEOID or spatial join back to tracts where needed for demographic context.

### Rules
- Only use the local data in the data/ folder. Do not do a web search for more data.
- Registration in `metric_metadata.yml` is NOT optional. Every metric needs an entry.
- Ask for clarification if there is any uncertainty. 
- Always propose an approach and wait for approval before writing code.
- After each function, summarize what it does and what should be verified.
- Flag any assumptions made about data structure or column names.
- If a future data source ever needs an API key (none currently do), it should go in a git-ignored `.env`/local config, never hardcoded in a script.
