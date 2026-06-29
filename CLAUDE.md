# agents.md

## Instructions
Produce a Quarto notebook for me that:
a. use `proposed_concept_for_CB3_equity_report_v2.pdf` and `CB3_Data_Reference_by_Domain_v4.csv` as project context. 
b. for each domain, find the relevant data source in the associated folder and pull the information for each metric.
c. clean and format the raw data, so that there is one row for each tract and a column for every metric in the domain.
d. flag if the data is not already at the tract level (e.g., different geographic unit). Some data may have to be geocoded and spatially joined into a tract.


### Code
- Be parsimonious in your code; do not implement more than what I ask for.
- Should be written in Python. Use `pandas` and `geopandas` to wrangle the data. Mapping should be done in `Folium` unless otherwise specified.
- Write out all dependencies in a `requirements.txt`
- Ccommented for each step/chunk of code. 

### Output
- Clean data should go in data/clean/[domain].csv
- The output file should be a CSV with one row for each tract (GEOID).

### Rules
- Only use the local data in the data/ folder. Do not do a web search for more data.
- Ask for clarification if there is any uncertainty. 
- Propose an approach and wait for approval before writing code
- After each function, summarize what it does and what should be verified
- Flag any assumptions made about data structure or column names
