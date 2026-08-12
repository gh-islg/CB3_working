require(dplyr)
require(readxl)
require(censusapi)


acs_vars <- listCensusMetadata(
  name = "acs/acs5", 
  type="variables",
  vintage = 2024)

nyc_rent_burden_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B25070*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_rent_burden_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B25070.csv", row.names=F)


nyc_cost_burden_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B25074*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_cost_burden_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B25074.csv", row.names=F)


nyc_occ_room_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B25014*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_occ_room_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B25014.csv", row.names=F)


nyc_house_units_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B25001.*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_house_units_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B25001.csv", row.names=F)


nyc_tenure_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B25003.*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_tenure_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B25003.csv", row.names=F)


nyc_pop_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B01001*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_pop_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B01001.csv", row.names=F)


nyc_poverty_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B17020*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_poverty_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B17020.csv", row.names=F)


nyc_language_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B16001*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_language_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B16001.csv", row.names=F)


nyc_income_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B19001*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_income_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B19001.csv", row.names=F)

nyc_median_inc_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B19013*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_median_inc_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B19013.csv", row.names=F)


nyc_hh_lang_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("C16002*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_hh_lang_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_C16002.csv", row.names = F)


nyc_lang_age_tract <- getCensus(
  name = "acs/acs5", 
  vars = c("NAME", acs_vars$name[grepl("B16004*", acs_vars$name)]),
  region = "tract:*", 
  regionin = "state:36+county:005,047,061,081,085", 
  vintage = 2024)
write.csv(nyc_lang_age_tract, "C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\acs_5yr_2024_B16004.csv", row.names = F)
