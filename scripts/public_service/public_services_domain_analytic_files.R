require(readxl)
require(sf)
require(tidyverse)
setwd("C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\Data\\Public Services\\")

bus_stops <- read.csv("MTA_Bus_Stops_20260507.csv", stringsAsFactors = F)

districts_shp <- st_read("C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\Data\\Geography\\nycd.shp") %>% 
  filter(BoroCD=="103")

bus_stops_sf <- st_transform(st_as_sf(bus_stops, coords=c("Longitude", "Latitude"), crs=4326), st_crs(districts_shp)) 

cb3_bus_stops <- bus_stops_sf[districts_shp,] %>% 
  group_by(Stop.Name) %>% 
  slice(1) %>% 
  select(Stop.ID, Stop.Name, Georeference, geometry) |> 
  ungroup()

write.csv(cb3_bus_stops, "PUBLIC_SERVICE_DOMAIN_bus_stops_file.csv", row.names = F)



subway_stops <- read.csv("MTA_Subway_Entrances_and_Exits__2024_20260507.csv", stringsAsFactors = F)

subway_stops_sf <- st_transform(st_as_sf(subway_stops, coords=c("Entrance.Longitude", "Entrance.Latitude"), crs=4326), st_crs(districts_shp)) 

cb3_subway_stops <- subway_stops_sf[districts_shp,] %>% 
  filter(Entry.Allowed!="NO")

nearby <- st_is_within_distance(cb3_subway_stops, cb3_subway_stops, 35)

# 2. Build a graph where each stop is a node, edges connect "nearby" stops
edges <- do.call(rbind, lapply(seq_along(nearby), function(i) {
  neighbors <- nearby[[i]]
  neighbors <- neighbors[neighbors != i]  # drop self
  if (length(neighbors) == 0) return(NULL)
  data.frame(from = i, to = neighbors)
}))

cb3_subway_stops_red <- cb3_subway_stops[-c(2, 21),] |> 
  select(Stop.Name, Complex.ID, Constituent.Station.Name, Station.ID, GTFS.Stop.ID, Entrance.Type,
         entrance_georeference, geometry)

write.csv(cb3_subway_stops_red, "PUBLIC_SERVICE_DOMAIN_subway_entrances_file.csv", row.names = F)






#### parks
# Polygon -> single point helper. Facility polygons (courts, fields, pools,
# playgrounds, dog runs) are reduced to one point that is guaranteed to lie
# inside the polygon (point-on-surface, not centroid, so concave shapes stay
# inside). The point is computed in NY State Plane feet (EPSG:2263), then
# returned as lon/lat (EPSG:4326).
polygon_to_lonlat <- function(df, wkt_col = "the_geom") {
  pts <- df %>%
    st_as_sf(wkt = wkt_col, crs = 4326) %>%
    st_make_valid() %>%
    st_transform(2263) %>%
    st_point_on_surface() %>%
    st_transform(4326)
  coords <- st_coordinates(pts)
  pts %>%
    st_drop_geometry() %>%
    mutate(longitude = coords[, 1], latitude = coords[, 2])
}

# --- 1. CB3 park base list ---
cb3_parks <- read_csv("Parks_Properties_20260716.csv") %>%
  filter(BOROUGH == "M", str_detect(COMMUNITYBOARD, "103")) %>%
  st_as_sf(wkt = "multipolygon", crs = 4326) |> 
  transmute(
    GISPROPNUM,
    park_name = SIGNNAME,
    TYPECATEGORY,
    ACRES,
    multipolygon
  )

# --- 2. Courts, fields, pools, rinks (Planimetric Database) ---
# One row per individual facility feature, with its own lon/lat.
park_facilities <- read_csv("NYC_Planimetric_Database__Open_Space_(Parks)_20260716.csv") %>%
  mutate(
    FEAT_CODE = str_remove_all(FEAT_CODE, ","),
    SUB_CODE  = str_remove_all(SUB_CODE, ",")
  ) %>%
  filter(PARKNUM %in% cb3_parks$GISPROPNUM) %>%
  mutate(facility_type = case_when(
    FEAT_CODE == "4900" ~ "athletic_field_baseball_softball",
    FEAT_CODE == "4920" ~ "athletic_field_football",
    FEAT_CODE == "4930" ~ "athletic_field_soccer",
    FEAT_CODE == "4950" ~ "pool",
    FEAT_CODE == "4960" ~ "running_track",
    FEAT_CODE == "4970" ~ "skating_rink",
    FEAT_CODE == "4910" & SUB_CODE == "491010" ~ "basketball_court",
    FEAT_CODE == "4910" & SUB_CODE == "491060" ~ "tennis_court",
    FEAT_CODE == "4910" ~ "other_court",
    TRUE ~ NA_character_
  )) %>%
  filter(!is.na(facility_type)) %>%
  polygon_to_lonlat() %>%
  select(GISPROPNUM = PARKNUM, facility_type, longitude, latitude)

# --- 3. Playgrounds (one row per play area) ---
playgrounds <- read_csv("DPR_PlayAreas_001_20260716.csv") %>%
  filter(GISPROPNUM %in% cb3_parks$GISPROPNUM) %>%
  polygon_to_lonlat() %>%
  transmute(GISPROPNUM, facility_type = "playground", longitude, latitude)

# --- 4. Dog runs (one row per active dog run) ---
dog_runs <- read_csv("Dog_Runs_20260716.csv") %>%
  filter(GISPROPNUM %in% cb3_parks$GISPROPNUM, DOG_AREA_TYPE == "Dog Run", FEATURESTATUS == "Active") %>%
  polygon_to_lonlat() %>%
  transmute(GISPROPNUM, facility_type = "dog_run", longitude, latitude)

# --- 5. Cleanliness (12-month acceptable rate) ---
cleanliness <- read_csv("Parks_Inspection_Program_–_Inspections_20260716.csv") %>%
  filter(`Prop ID` %in% cb3_parks$GISPROPNUM) %>%
  arrange(`Prop ID`, desc(Date)) |> 
  group_by(GISPROPNUM = `Prop ID`) |> 
  slice(1) |> 
  ungroup() |> 
  mutate(
    condition_acceptable   = as.numeric(`Overall Condition` %in% c("A", "B"), na.rm = TRUE),
  ) |> 
  select(GISPROPNUM, inspection_date=Date, condition_acceptable)

# --- 6. Write the two analytical files ---
# Facility-level file: one row per facility with its own coordinates.
cb3_park_facilities <- bind_rows(park_facilities, playgrounds, dog_runs) %>%
  left_join(
    cb3_parks %>% st_drop_geometry() %>% select(GISPROPNUM, park_name),
    by = "GISPROPNUM"
  ) %>%
  select(GISPROPNUM, park_name, facility_type, longitude, latitude)

write.csv(cb3_park_facilities, "PUBLIC_SERVICE_DOMAIN_park_facilities.csv", row.names = F)

# Park-level file: one row per park with its footprint as a WKT column.
cb3_park_footprints <- cb3_parks %>%
  mutate(geometry_wkt = st_as_text(st_geometry(.))) %>%
  st_drop_geometry() %>%
  left_join(cleanliness, by = "GISPROPNUM")

write.csv(cb3_park_footprints, "PUBLIC_SERVICE_DOMAIN_parks.csv", row.names = F)




##public restrooms
restrooms_sf <- read_csv("Public_Restrooms_20260716.csv") %>%
  st_as_sf(coords = c("Longitude", "Latitude"), crs = 4326, remove = FALSE)

cb3_restrooms <- restrooms_sf[st_transform(districts_shp, 4326), ] %>%
  st_drop_geometry() %>%
  select(
    facility_name = `Facility Name`,
    location_type = `Location Type`,
    status = Status,
    accessibility = Accessibility,
    latitude = Latitude,
    longitude = Longitude
  )

write.csv(cb3_restrooms, "PUBLIC_SERVICE_DOMAIN_public_restrooms.csv", row.names=F)
