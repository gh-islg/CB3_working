require(readxl)
setwd("C:\\Users\\15394061\\OneDrive - CUNY\\CB3\\Data\\Education\\")

schools <- read_xlsx("LCGMS_SchoolData_20260427_1424.xlsx") %>% 
  filter(`Community District`==103) %>% 
  mutate(`Location Type Description`=case_when(`Managed By Name`=="Charter" ~ paste(`Location Type Description`, "- Charter"),
         .default=`Location Type Description`)) %>% 
  select(ats_system_code=`ATS System Code`,
         bldg_code=`Building Code`,         
         name=`Location Name`,
         beds_number=`BEDS Number`,
         admin_district=`Administrative District Code`,
         type=`Location Type Description`,
         category=`Location Category Description`,
         grades=Grades,
         address=`Primary Address`,
         zip=Zip,
         district=`Community District`,
         tract=`Census Tract`,
         nta=NTA_Name,
         borough_block_lot=`Borough Block Lot`) %>% 
  left_join(read_xlsx("pta-financial-reporting-20251126.xlsx", sheet="School") %>% 
              select(bldg_code=`School Code`, pta_beginning_bal=`Beginning Balance`,
                     pta_tot_inc=`Total Income`, pta_tot_exp=`Total Expenses`,
                     pta_ending_bal=`Ending Balance`)) %>% 
  left_join(read_xlsx("2025-students-in-temporary-housing-report.xlsx", sheet="School-level") %>% 
              mutate(bldg_code=substr(DBN,3,6)) %>% 
              select(bldg_code, n_students_temp_housing=`# Students in Temporary Housing`,
                     pct_students_temp_housing=`% Students in Temporary Housing`)) %>% 
  left_join(read.csv("Enrollment_Capacity_And_Utilization_Reports_20260420.csv") %>% 
              select(bldg_code=Bldg.ID,
                     bldg_capacity=Target.Bldg.Cap,
                     bldg_utilization=Target.Bldg.Util,
                     bldg_utilization_date=Data.As.Of) %>% 
              mutate(bldg_utilization_date=lubridate::mdy(bldg_utilization_date),
                     bldg_utilization=bldg_utilization/100,
                     bldg_overcrowded=as.numeric(bldg_utilization>=1)) %>% 
              filter(!is.na(bldg_utilization)) %>% 
              unique() %>% 
              group_by(bldg_code) %>% 
              arrange(bldg_code, desc(bldg_utilization_date)) %>% 
              slice(1)) %>% 
  left_join(read.csv("2017-18__-_2021-22_Demographic_Snapshot_20260630.csv") %>% 
              mutate(bldg_code=substr(DBN,3,6)) %>% 
              select(bldg_code, pct_iep_2122=X..Students.with.Disabilities.1,
                     pct_ell_2122=X..English.Language.Learners.1,
                     pct_poverty_2122=X..Poverty.1,
                     prek_seats_2122=Grade.PK..Half.Day...Full.Day.,
                     threek_seats_2122=Grade.3K))

write.csv(schools, "EDUCATION_DOMAIN_school_level_analytic_file.csv", row.names = F)

tract <- read.csv("acs_5yr_2024_B01001.csv") %>% 
  select(state, county, tract, NAME, male_under5=B01001_003E,
         female_under5=B01001_027E,
         male_5to9=B01001_004E,
         female_5to9=B01001_028E,
         male_10to14=B01001_005E,
         female_10to14=B01001_029E,
         male_15to17=B01001_006E,
         female_15to17=B01001_030E,
         male_18to19=B01001_007E,
         female_18to19=B01001_031E) %>% 
  mutate(tot_under_5 = male_under5+female_under5,
         tot_5to19=male_5to9+male_10to14+male_15to17+male_18to19+female_5to9+female_10to14+female_15to17+female_18to19) %>% 
  filter(NAME %in% c("Census Tract 2.01; New York County; New York",
                     "Census Tract 2.02; New York County; New York",
                     "Census Tract 6; New York County; New York",
                     "Census Tract 8; New York County; New York",
                     "Census Tract 10.01; New York County; New York",
                     "Census Tract 10.02; New York County; New York",
                     "Census Tract 12; New York County; New York",
                     "Census Tract 14.01; New York County; New York",
                     "Census Tract 14.02; New York County; New York",
                     "Census Tract 16; New York County; New York",
                     "Census Tract 18; New York County; New York",
                     "Census Tract 20; New York County; New York",
                     "Census Tract 22.01; New York County; New York",
                     "Census Tract 22.02; New York County; New York",
                     "Census Tract 24; New York County; New York",
                     "Census Tract 25; New York County; New York",
                     "Census Tract 26.01; New York County; New York",
                     "Census Tract 26.02; New York County; New York",
                     "Census Tract 27; New York County; New York",
                     "Census Tract 28; New York County; New York",
                     "Census Tract 29.02; New York County; New York",
                     "Census Tract 30.01; New York County; New York",
                     "Census Tract 30.02; New York County; New York",
                     "Census Tract 32; New York County; New York",
                     "Census Tract 34; New York County; New York",
                     "Census Tract 36.01; New York County; New York",
                     "Census Tract 36.02; New York County; New York",
                     "Census Tract 38; New York County; New York",
                     "Census Tract 40.01; New York County; New York",
                     "Census Tract 40.02; New York County; New York",
                     "Census Tract 42; New York County; New York"))

write.csv(tract, "EDUCATION_DOMAIN_tract_level_analytic_file.csv", row.names = F)
