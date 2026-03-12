# Boundary Analysis

The goal of this project is to generate a script (or set of scripts) that produce the following outputs:

1. A collection of GIS datasets (all polygons) that represent the best source of boundaries for ESPEN sub-implementation units (subIUs) for each country in the ESPEN AFRO region.
2. A lookup table that matches each ESPEN IU ID to the name and ID from the appropriate GIS dataset that it most closely matches. Matches will be based on the similarity of the name, after accounting for parent administrative units (e.g., Waterloo, Ohio can't be matched to Waterloo, Indiana).
3. A lookup table that matches each ESPEN subIU ID to the name and ID from the appropriate GIS dataset that it most closely matches. Matches will be based on the similarity of the name, after accounting for parent administrative units (e.g., Waterloo, Ohio can't be matched to Waterloo, Indiana).
4. A table (can be in CSV format) with one row per country, and columns showing the number of ESPEN IUs, the number of ESPEN IUs with a good match to the identified GIS dataset, number of ESPEN subIUs, etc. The columns will be defined iteratively as we work on the project.

Here are some guiding principles for the project:

- Treat `subIU_EpiData_202507.csv` as the main source of truth when it comes to the official list of ESPEN IUs and subIUs.

## Next steps
1. I did a fair amount of work on this project 6 months ago, but the work was complicated and I've forgotten much. Analyze the contents of `archive/Country Data`. I believe each country folder has a Python script that features a lot of name standardization that I conducted 6 months ago. Write a file `country_data_summary.md` that describes what the scripts in that folder are doing, and offer suggestions for preserving that work but getting things more organized moving forward.
2. Your summary of the subfolder looks good, so let's start implementing your recommendations. Leave the contents of the `archive` subfolder alone, we will create and edit new files as we go. Implement your first recommendation, '1. Create a Shared Module`.
3. Now implement your '2. Use Configuration Files'. Create a YAML for each country in the Country Data folder, and analyze each country-specific Python file to populate the YAML files. 
4. Now implement your '3. Create Standardized Name Mapping Tables'. Pull out all of the manual corrections from the various country-specific Python scripts and make sure that each one is represented here. 
5. The community mappings extracted in step 4 are actually substring operations (not whole-value replacements like the ADM1/ADM2 mappings). These include:
     - Substring removals: removing prefixes like "HD " or suffixes like " HC"
     - Substring replacements: replacing characters like "é" -> "e" or "Ã¨" -> "e"

  Move these patterns into the country-specific YAML config files under a new `name_cleaning` section. The structure should be:

  ```yaml
  name_cleaning:
    prefixes_to_remove: ["HD ", "CS "]
    suffixes_to_remove: [" HC", " IHC"]
    substrings_to_replace:
      "é": "e"
      "è": "e"
      " District Council": ""

  Note: Some replacements like accent removal (é -> e) may be redundant with the standardization.remove_accents: true setting. Flag these in comments but still include them to preserve the original script's intent.

  After updating the YAML files, delete the data/mappings/community/ directory and data/mappings/all_community_mappings.csv since they will no longer be needed.
  ```
6. Now implement your '4. Single Entry Point Script'. 

7. I believe I've had you address '5. Preserve Manual Work' but assess if there is any other manual work we should save.

   **Completed - Step 7 Assessment:**

   Found and preserved additional manual work that was missed in step 4:

   - **Nigeria ADM2 mappings (75 corrections)**: The `archive/Country Data/Nigeria/nigeria_cleaned_adm2.csv` file contained 746 rows of ADM2 mappings, but only 1 was originally extracted. 75 of these were actual corrections (not just case normalization), including real name differences like Biu→BAYO, Boki→BIASE, Bassa→BOKKOS, and disambiguation entries like Irepodun→IREPODUN (OSUN).

   - **Mali ADM2 mappings (11 corrections)**: Extracted from `archive/Country Data/Mali/admin2_to_iu_names.csv`, including Commune 1→Commune I, Kalabancoro→Kalaban Coro, Taoudenni→Taoudeni.

   - **Eswatini ADM2 mappings (5 corrections)**: Extracted from `archive/Country Data/Eswatini/admin2_to_iu_names.csv`, including Mpolonjeni→Mpholonjeni, Kukhanyeni→Ekukhanyeni.

   - **Chad N'DJAMENA corrections (6 additions)**: Added N'DJAMENA CENTRE→NDJAMENA-CENTRE and similar variants that were in the scripts but not in the mapping files.

   Created `scripts/extract_additional_mappings.py` to perform this extraction. Total ADM2 mappings increased from 45 to 142.

   **Other archive contents noted but not extracted:**
   - Source data files (health facilities, administrative boundaries) - already in archive
   - Nominatim geocoding results - used for matching but not name corrections
   - Previous match results (matched.csv files) - output data, not input corrections
   - `admin2_to_iu_names.csv` files for Chad, Mali, Mozambique, Niger, Congo, Eswatini - many entries are "No match" markers or already extracted   
   
8. Ok, it seems that we now have a way to produce results country by country that preserves most of the manual work I did months ago. I now want a way to run the script once for each country in order to recreate the data in `results_summary.csv`. I want to be able to quickly refresh this analysis without interacting with Claude.

   **Completed - Step 8:**

   Created `scripts/run_all_countries.py` - a batch script to run matching for all countries.

   Usage:
   ```bash
   # Run all countries (writes output to output/<country>/ and results_summary.csv)
   python scripts/run_all_countries.py

   # Run specific countries only
   python scripts/run_all_countries.py --countries angola nigeria kenya

   # Dry run (no output files written)
   python scripts/run_all_countries.py --dry-run

   # Quiet mode (one line per country)
   python scripts/run_all_countries.py --quiet

   # Custom ESPEN file
   python scripts/run_all_countries.py --espen-file path/to/file.csv
   ```

   The script:
   - Auto-detects ESPEN reference file (prefers CSV over XLSX)
   - Iterates through all country configs in `config/countries/`
   - Runs the matching pipeline for each
   - Writes per-country outputs to `output/<country>/matched.csv`
   - Generates consolidated `results_summary.csv` with columns: country, matched, ref, ref_unmatched, unclean, unmatched
   - Reports errors gracefully for countries with missing GIS files or config issues

   Current results (exact matching only, without jellyfish/scipy):
   - 16 countries successful, 14 failed (missing GIS files or column issues)
   - 48.8% overall match rate across successful countries 
   
9. The output of running all the countries looks reasonable, but it doesn't exactly line up with the totals I got from my previous work. I think at one point you said something about the scripts falling back on perfect matches if `fuzzy` or related modules weren't available. Assess whether this was the case with the latest run.

   **Completed - Step 9 Assessment:**

   Yes, the latest run **only performed exact matching** because `jellyfish` is not installed.

   **Dependency check:**
   - `jellyfish`: NOT AVAILABLE (required for Soundex and string similarity)
   - `scipy.optimize.linear_sum_assignment`: AVAILABLE (for Hungarian algorithm)

   The code in `run_matching_pipeline()` checks for both libraries. If either is missing, it sets `fuzzy_available = False` and only runs Step 1 (exact matches).

   **Impact on results (comparing archive with fuzzy vs current exact-only):**

   | Country | Archive (fuzzy) | Current (exact) | Difference |
   |---------|-----------------|-----------------|------------|
   | Nigeria | 8,265 | 6,459 | -1,806 |
   | Tanzania | 2,935 | 0 | -2,935 |
   | Zimbabwe | 1,439 | 0 | -1,439 |
   | Kenya | 1,311 | 1,204 | -107 |
   | Guinea | 279 | 178 | -101 |
   | Botswana | 180 | 0 | -180 |
   | Zambia | 401 | 1,083 | +682 (improved ADM mappings) |

   Countries with 0 current matches (Tanzania, Zimbabwe, Botswana, CAR) relied entirely on fuzzy matching in the original scripts.

   **To enable full fuzzy matching, install:**
   ```bash
   pip install jellyfish
   ```

   (scipy is already available) 
   
10. Don't allow the script to fall back on perfect matching if the modules for fuzzy matching aren't available. There's no point in the exercise if fuzzy matching isn't possible. Instead, the script should exit with an erorr indicating what the problem is.

    **Completed - Step 10:**

    Updated both `run_matching.py` and `run_all_countries.py` to require fuzzy matching dependencies.

    Added `check_fuzzy_matching_dependencies()` function that:
    - Checks for `jellyfish` and `scipy.optimize.linear_sum_assignment`
    - Exits with code 1 if either is missing
    - Provides clear error message with install instructions

    Example output when dependencies missing:
    ```
    ERROR: Required dependencies for fuzzy matching are not available.

    Missing packages:
      - jellyfish

    Install with:
      pip install jellyfish

    Fuzzy matching is required for this script to produce meaningful results.
    ```

    The fallback to exact-only matching has been removed from `run_matching_pipeline()`. 
	
11. I've just created a new conda environment to work in. Install all of the dependencies (especially the ones required for fuzzy matching) into this new conda environment.

    **Completed - Step 11:**

    Installed all required dependencies via conda:

    ```bash
    conda install -y jellyfish scipy pandas pyyaml
    conda install -y fiona geopandas
    conda install -y openpyxl
    ```

    **Installed packages:**
    | Package | Version | Purpose |
    |---------|---------|---------|
    | pandas | 2.3.3 | Data manipulation |
    | pyyaml | 6.0.3 | YAML config file parsing |
    | jellyfish | 1.2.1 | Fuzzy string matching (Soundex, Levenshtein) |
    | scipy | 1.16.3 | Hungarian algorithm (linear_sum_assignment) |
    | fiona | 1.10.1 | GIS file I/O |
    | geopandas | 1.1.1 | GIS data manipulation |
    | openpyxl | 3.1.5 | Excel file support |

    **Test result with Angola (fuzzy matching now working):**
    - Exact matches: 222
    - Fuzzy matches (distance <= 1): 77
    - Fuzzy matches (score < 0.25): 11
    - **Total: 310 matches (57.5% match rate)**

    Compare to previous exact-only: 222 matches (41.2%)

12. Produce a table (and paste it below in this prompt file) summarizing the meaning of each column in `results_summary.csv`, including the datasource that produced the counts.

    **Completed - Step 12:**

    | Column | Description | Data Source | Calculation |
    |--------|-------------|-------------|-------------|
    | `country` | Country name from config | `config/countries/<name>.yaml` → `country_name` field | Direct from config |
    | `ref` | Number of ESPEN reference records for this country | ESPEN file (e.g., `subIU_Demographics_202503_noduplicates.csv`) filtered by `Country == country_name` | `len(ref)` after filtering |
    | `unclean` | Number of GIS features (polygons/points) loaded | Country-specific GIS file specified in `config/countries/<name>.yaml` → `gis.file` | `len(unclean)` from GIS file |
    | `matched` | Number of successful matches between ESPEN records and GIS features | Output of matching pipeline (exact + fuzzy) | `len(matched)` after all matching steps |
    | `ref_unmatched` | Number of ESPEN records without a GIS match | Calculated | `ref - matched` |
    | `unmatched` | Number of GIS features without an ESPEN match | Remaining GIS features after matching | `len(unclean) - len(matched)` |

    **Notes:**
    - `ref` counts ESPEN subIU records (communities), not IUs
    - `unclean` counts GIS features which may be at different admin levels depending on the country's GIS source
    - `matched` includes exact matches (Step 1) + fuzzy matches with Levenshtein distance ≤ 1 (Step 2) + fuzzy matches with Levenshtein score < 0.25 (Step 3)
    - `error` appears when the country could not be processed (missing GIS file, config issue, etc.) 

13. So does `unclean` involve any filtering of the records in the GIS dataset, or is it a count of GIS features before any operations are performed? Put the answer below.

    **Answer:**

    `unclean` is a count of **ALL GIS features** in the file with **no filtering applied**.

    The `load_gis_data()` function (lines 109-165 in `run_matching.py`):
    1. Opens the GIS file and loads all features
    2. Renames columns based on config mappings (e.g., `ADMIN1` → `adm1`)
    3. Returns all rows - no filtering by country, admin level, or any other criteria

    The count is taken immediately after loading, before any standardization or matching operations.

    This means if a GIS file contains features for multiple countries or irrelevant feature types, they would all be included in the `unclean` count. The assumption is that each country's GIS file contains only features relevant to that country. 	
	
14. Let's change the column names of `results_summary.csv` to these:
	'country' : 'country'
	'ref' : 'espen_subiu_count'
	'unclean' : 'gis_src_feature_count'
	'matched' : 'good_match_count'
	'ref_unmatched' : 'espen_subiu_no_matches'
	'unmatched' : 'gis_src_no_matches'

    **Completed - Step 14:**

    Updated `write_results_summary()` in `scripts/run_all_countries.py` to use the new column names.

    New CSV header:
    ```
    country,espen_subiu_count,gis_src_feature_count,good_match_count,espen_subiu_no_matches,gis_src_no_matches
    ```
	
15. I think things are going well for the "standard" case, which is the situation where a country has a GIS dataset with polygons. However, there are a number of cases where the country's original Python script was matching against a set of points (sometimes held in a CSV file and sometimes in a .shp or something similar). Add an optional flag that indicates whether the country is using point data to match to.

16. Assess the countries where the script returns "No GIS file specified in the config" to see which ones should get the new flag developed in the previous step.

    **Completed - Steps 15 & 16:**

    **Step 15 - Added `data_type` flag:**

    Updated `config/base.yaml` to include:
    ```yaml
    gis:
      file: ""
      format: "shp"           # Now supports: shp, gpkg, gdb, csv
      data_type: "polygon"    # "polygon" (default) or "point"
    ```

    Updated `load_gis_data()` in `run_matching.py` to handle CSV files for point data.

    **Step 16 - Assessment of countries with missing GIS configs:**

    All 8 countries use **point data from Nominatim geocoding results** (CSV files):

    | Country | Nominatim File | Status |
    |---------|---------------|--------|
    | Burkina Faso | burkina_nominatim_filtered.csv | Working (1175 points, 90.6% match) |
    | Congo | congo_nominatim_filtered.csv | Working |
    | Eswatini | eswatini_nominatim_filtered.csv | Working |
    | Mali | mali_nominatim_filtered.csv | Working |
    | Mozambique | mozambique_nominatim_filtered.csv | Empty file (0 bytes) |
    | Niger | niger_nominatim_filtered.csv | Working |
    | The Gambia | gambia_nominatim_filtered.csv | Working |
    | Togo | togo_nominatim_filtered.csv | Working (391 points, 66.8% match) |

    Updated all 8 country configs with:
    - `gis.file`: nominatim CSV filename
    - `gis.format`: csv
    - `gis.data_type`: point
    - `gis.columns`: mapped to Nominatim column names (NAME, Admin_1, Admin_2, Community_ID) 
	
17. A number of countries are failing with a message "'DataFrame' object has no attribute 'to_file'". Assess what is causing that to happen and offer a solution below.

    **Completed - Step 17:**

    **Cause:** The `write_outputs()` function tried to use `fiona.open()` on CSV files. Fiona only supports GIS vector formats (shapefiles, geopackages, etc.), not CSVs. When it failed, the code fell back to geopandas which returned a regular DataFrame instead of a GeoDataFrame, causing the `to_file()` error.

    **Solution:** Updated `write_outputs()` to detect CSV-based point data and skip shapefile output for those countries. The matched.csv is still written, but merged.shp and unmatched.shp are skipped.

    ```python
    # Check if this is point/CSV data - skip shapefile output for CSV sources
    file_format = gis_config.get('format', 'shp')
    gis_file = gis_config.get('file', '')
    is_csv = file_format == 'csv' or gis_file.lower().endswith('.csv')

    if is_csv:
        print("\nNote: Skipping shapefile output for CSV-based point data")
        return
    ```

    **Result:** Burkina Faso and Togo (and other CSV-based countries) now complete successfully. 
	
18. Analyze the error below and explain what is causing it. Don't fix it, I'm trying to understand the scripts and worfklow better.
```
(fuzzy) mckinnoj@LAPTOP-PU0GHQMK:~/git/boundaries$ python scripts/run_all_countries.py --countries cameroon
ESPEN Boundary Matching - Batch Run
Time: 2026-01-13 12:31:45
Config directory: /home/mckinnoj/git/boundaries/config/countries
ESPEN file: /home/mckinnoj/git/boundaries/archive/subIU_Demographics_202503_noduplicates.csv
Output directory: /home/mckinnoj/git/boundaries/output

Processing 1 countries...
============================================================

[1/1] cameroon
----------------------------------------
Loading config from /home/mckinnoj/git/boundaries/config/countries/cameroon.yaml

============================================================
Processing: Cameroon
============================================================

Loaded 1 ADM1 mappings, 0 ADM2 mappings
Loading ESPEN reference data for Cameroon...
  Found 2001 ESPEN records for Cameroon
Loading GIS data from /home/mckinnoj/git/boundaries/archive/Country Data/Cameroon/cmr_admin3.shp...
  Loaded 360 features
Error processing Cameroon: "['col_to_match'] not in index"
  ERROR: "['col_to_match'] not in index"...

Wrote results summary to /home/mckinnoj/git/boundaries/results_summary.csv

============================================================
OVERALL SUMMARY
============================================================
Countries processed: 1
Successful: 0
Failed: 1

Failed countries:
  - Cameroon: "['col_to_match'] not in index"
(fuzzy) mckinnoj@LAPTOP-PU0GHQMK:~/git/boundaries$
```

    **Analysis:**

    The error occurs because of a **column name mismatch** between the config and the actual shapefile.

    **The config specifies:**
    ```yaml
    gis:
      columns:
        name: name    # <-- expects a column called "name"
    ```

    **But the actual shapefile (`cmr_admin3.shp`) has these columns:**
    - `adm3_name` (this is likely what should be used)
    - `adm3_name1`, `adm3_name2`, `adm3_name3`
    - `adm2_name`, `adm1_name`, `adm0_name`
    - `adm3_pcode`, etc.

    There is **no column called `name`** in the shapefile.

    **How the error happens:**
    1. `load_gis_data()` tries to map columns from the config
    2. It checks: `if name_col and name_col in unclean.columns:` → this is `False` because `"name"` is not in the shapefile
    3. So `unclean['col_to_match']` is never created
    4. At the end, `return unclean[['adm1', 'adm2', 'id', 'col_to_match']].copy()` fails because `col_to_match` doesn't exist

    **To fix:** Update the config to use `adm3_name` instead of `name`:
    ```yaml
    gis:
      columns:
        name: adm3_name
    ```
19. Assess why Cameroon failed below and put your explanation in this file.
```
(fuzzy) mckinnoj@LAPTOP-PU0GHQMK:~/git/boundaries$ python scripts/run_all_countries.py --countries cameroon
ESPEN Boundary Matching - Batch Run
Time: 2026-01-13 12:39:11
Config directory: /home/mckinnoj/git/boundaries/config/countries
ESPEN file: /home/mckinnoj/git/boundaries/archive/subIU_Demographics_202503_noduplicates.csv
Output directory: /home/mckinnoj/git/boundaries/output

Processing 1 countries...
============================================================

[1/1] cameroon
----------------------------------------
Loading config from /home/mckinnoj/git/boundaries/config/countries/cameroon.yaml

============================================================
Processing: Cameroon
============================================================

Loaded 1 ADM1 mappings, 0 ADM2 mappings
Loading ESPEN reference data for Cameroon...
  Found 2001 ESPEN records for Cameroon
Loading GIS data from /home/mckinnoj/git/boundaries/archive/Country Data/Cameroon/cmr_admin3.shp...
  Loaded 360 features

Applying standardization...

Warning: 9 ADM1 names in GIS not found in ESPEN:
  - ADAMAWA
  - EAST
  - FAR-NORTH
  - NORTH
  - NORTH-WEST
  - SOUTH
  - SOUTH-WEST
  - WEST
  - littoral

Step 1: Finding exact matches...
  Found 0 exact matches

Step 2: Fuzzy matching (Levenshtein distance <= 1)...
Error processing Cameroon: 'levenshtein_distance'
  ERROR: 'levenshtein_distance'...

Wrote results summary to /home/mckinnoj/git/boundaries/results_summary.csv

============================================================
OVERALL SUMMARY
============================================================
Countries processed: 1
Successful: 0
Failed: 1

Failed countries:
  - Cameroon: 'levenshtein_distance'
(fuzzy) mckinnoj@LAPTOP-PU0GHQMK:~/git/boundaries$
```

    **Analysis - Step 19:**

    The error `'levenshtein_distance'` is a **KeyError** that occurs at line 276 in `run_matching.py`:
    ```python
    new_matches = fuzzy[fuzzy['levenshtein_distance'] <= lev_dist_threshold].copy()
    ```

    This happens because `fuzzy_match_1_to_1()` returned an **empty DataFrame with no columns**.

    **Root Cause: All rows excluded by groupby due to NaN values**

    The `fuzzy_match_1_to_1()` function groups records by (adm1, adm2):
    ```python
    for (adm1, adm2), grp in dataset_to_match.groupby(['adm1', 'adm2'], sort=False):
    ```

    Pandas `groupby()` **excludes rows where any groupby column is NaN**. If either `adm1` or `adm2` is NaN for all rows, the loop never executes, `records` stays empty, and `pd.DataFrame([])` is returned with no columns.

    **Evidence:**

    1. The warning shows 9 ADM1 values exist in GIS data (including "littoral" - note the lowercase, which is suspicious given `case: upper` standardization)

    2. The `adm1_mappings` is **backwards**:
       ```yaml
       adm1_mappings:
         LITTORAL: littoral   # Maps uppercase TO lowercase
       ```
       After `case: upper` standardization, "LITTORAL" gets mapped back to "littoral" (lowercase), which won't match ESPEN's uppercase values.

    3. Most critically: **ALL 9 GIS ADM1 names are not found in ESPEN**. This means the GIS and ESPEN data use completely different naming conventions (likely English vs French):
       - GIS: ADAMAWA, EAST, FAR-NORTH, NORTH, etc.
       - ESPEN: Probably Adamaoua, Est, Extrême-Nord, Nord, etc. (French names)

    **Why the KeyError specifically:**

    The most likely explanation is that the `adm2` column contains all NaN values after loading. This could happen if:
    - The `adm2_name` column in `cmr_admin3.shp` contains null/empty values
    - Or the column name in the shapefile doesn't exactly match the config

    When all `adm2` values are NaN:
    1. `groupby(['adm1', 'adm2'])` produces 0 groups (all rows excluded)
    2. The for loop never executes
    3. `records = []` remains empty
    4. `pd.DataFrame([])` returns a DataFrame with 0 rows AND 0 columns
    5. `fuzzy['levenshtein_distance']` raises KeyError (column doesn't exist)

    **To Fix:**

    1. **Check the actual shapefile columns** to see what `adm2_name` contains
    2. **Add ADM1 mappings** for the English→French name differences:
       ```yaml
       adm1_mappings:
         ADAMAWA: ADAMAOUA
         EAST: EST
         FAR-NORTH: EXTREME-NORD
         NORTH: NORD
         NORTH-WEST: NORD-OUEST
         SOUTH: SUD
         SOUTH-WEST: SUD-OUEST
         WEST: OUEST
       ```
    3. **Verify ADM2 column** - check if `adm2_name` exists and has valid data in the shapefile
	
20. I agree with the diagnosis that the error in step 19 is caused by there being zero matches. Add an error message that is more descriptive when this situation comes up.

    **Completed - Step 20:**

    Updated `run_matching_pipeline()` in `scripts/run_matching.py` to detect when `fuzzy_match_1_to_1()` returns an empty DataFrame and provide a descriptive error message.

    **Changes:**
    - Added check after Step 2's `fuzzy_match_1_to_1()` call
    - Diagnoses the root cause by checking:
      - Whether all adm1/adm2 values are NaN
      - Whether there's any overlap between GIS and ESPEN adm1/adm2 values
    - Raises a `ValueError` with actionable guidance

    **Example error message for Cameroon:**
    ```
    Fuzzy matching returned no results. No ADM1 overlap between GIS (9 values) and ESPEN (10 values).
    GIS ADM1 names may need mapping to ESPEN names (see adm1_mappings in config).
    ```

    **Also added:** Warning message in Step 3 if fuzzy matching returns empty results (less critical since Step 2 would have already caught configuration issues).

21. During my manual work, I was somewhat lazily when it came to my ADM references. When I mapped GIS columns to ADM1 and ADM2, I was avoiding the fact that the ESPEN dataset's definition of ADM1 and ADM2 do not necessarily align with the GIS dataset's defintion. Don't change the way the columns are handled, but make it clear in the Python and YAML files that setting ADM1 and ADM2 for the GIS dataset is referring to ESPEN_ADM1 and ESPEN_ADM2.

    **Completed - Step 21:**

    Renamed GIS column mappings from `adm1`/`adm2` to `adm1_espen`/`adm2_espen` to make clear these refer to ESPEN's administrative hierarchy, not the GIS dataset's native levels.

    **Changes made:**

    1. **config/base.yaml**: Renamed columns and added documentation explaining:
       - `adm1_espen`: GIS column whose values map to ESPEN Admin_1
       - `adm2_espen`: GIS column whose values map to ESPEN Admin_2
       - Guidance on finding correct GIS columns and handling level mismatches

    2. **scripts/run_matching.py**: Updated `load_gis_data()` function:
       - Added docstring explaining the ESPEN vs GIS level distinction
       - Updated to read `adm1_espen`/`adm2_espen` with fallback to old names for backwards compatibility
       - Added inline comments clarifying the mapping

    3. **All 30 country config files**: Updated column names from `adm1`/`adm2` to `adm1_espen`/`adm2_espen`

    **Example (Cameroon) showing the clarified naming:**
    ```yaml
    gis:
      columns:
        id: adm3_pcode
        name: adm3_name1
        # Note: GIS has adm1=region, adm2=département, adm3=arrondissement
        # But ESPEN's Admin_2 is at the arrondissement level (same as GIS adm3)
        adm1_espen: adm1_name1
        adm2_espen: adm1_name1  # Reuse region - no GIS column matches ESPEN Admin_2 level
    ```
	
22. While investigating issues with Chad, I uncovered some new capabilities we should add to the main processing script to better handle cases where the GIS input is in the form of points. In these situations, we can spatially assign ESPEN IUs to the points using the ESPEN cartography. This is a more reliable way of determining which IU the subIU belongs to than doing a fuzzy match based on name, which is the standard approach for the other countries. Check out the script `fuzzy_matching_chad_villages.py` to understand how this technique was used.

So, add a country-specific config parameter that specifies whether ESPEN IUs should be spatially assigned to the points before trying to match those points to subIUs. This parameter should only be applicable to the case where subIU names are being matched to points.

    **Completed - Step 22:**

    Added spatial IU assignment capability for point data. When enabled, points are spatially joined to ESPEN IU cartography polygons to determine their ADMIN1/ADMIN2 values, which is more reliable than name-based matching.

    **Changes made:**

    1. **config/base.yaml**: Added new config parameters:
       ```yaml
       gis:
         spatial_iu_assignment: false  # Enable spatial join for point data
         iu_cartography_file: ""       # Path to ESPEN IU cartography shapefile
       ```

    2. **scripts/run_matching.py**:
       - Updated `load_gis_data()` to accept `country_name` parameter
       - Added `_load_gis_with_spatial_assignment()` function that:
         - Loads point data as GeoDataFrame (with geometry)
         - Loads ESPEN IU cartography and filters to the country
         - Performs spatial join to assign ADMIN1/ADMIN2 from IU polygons
         - Reports how many points were successfully assigned

    3. **config/countries/chad.yaml**: Updated to use spatial IU assignment:
       ```yaml
       gis:
         file: tcd_p_ppl_Villages_cleaned_inseed.shp
         data_type: point
         spatial_iu_assignment: true
         iu_cartography_file: ESPEN_IU_2022.shp
         columns:
           id: OBJECTID
           name: Nom
           adm1_espen: null  # Values come from spatial join
           adm2_espen: null
       ```

    **How it works:**
    1. Point data is loaded with geometry preserved
    2. ESPEN IU cartography polygons are loaded and filtered to the country
    3. A spatial join (`gpd.sjoin(..., predicate='within')`) assigns each point to the IU polygon it falls within
    4. The IU's ADMIN1/ADMIN2 values are used for grouping in fuzzy matching
    5. Points outside all IU polygons get NaN values (and won't match)

    **When to use:**
    - Point data where administrative columns don't align with ESPEN's hierarchy
    - When the point data's native ADM columns are unreliable or missing
    - Requires `data_type: point` to be set

23. Diagnose and fix the `'<' not supported between instances of 'str' and 'float'` error thrown by Chad.

    **Completed - Step 23:**

    **Cause:** The error occurred in `run_country()` when trying to sort unmatched ADM names that included NaN values. Python's `sorted()` can't compare strings with floats (NaN is a float).

    ```python
    # This fails when unmatched_adm1 contains NaN values
    for name in sorted(unmatched_adm1)[:10]:
    ```

    **Fix:** Filter out NaN values before sorting:
    ```python
    valid_names = [n for n in unmatched_adm1 if pd.notna(n)]
    for name in sorted(valid_names)[:10]:
    ```

    **Result:** Chad now runs successfully:
    - 16,963 point features loaded
    - 16,862 spatially assigned to IU polygons
    - 599 matches found (305 exact + 222 fuzzy distance≤1 + 72 fuzzy score<0.25)
    - 3.5% match rate

    Note: The remaining unmatched ADM1 names ("BARH EL GAZEL", "NDJAMENA") need ADM1 mappings in the config to match ESPEN's spellings 
	
24. DRC is currently failing with `Democratic Republic of the Congo: GIS file not found: GRID3_COD_health_areas_v5_0.shp` but it looks like that file is present in the `DRC, Congo` folder. Is the failure caused by some inconsistency in how the various files refer to the country DRC?

    **Completed - Step 24:**

    **Cause:** The folder name `"DRC, Congo"` didn't match either:
    - The `country_name` ("Democratic Republic of the Congo")
    - The config filename pattern (`drc_congo` → can't match `"drc,_congo"` due to comma)

    **Fix:** Added `country_folder` config option to specify the folder name when it differs from `country_name`.

    **Changes:**
    1. **config/base.yaml**: Added new field:
       ```yaml
       country_folder: ""  # Folder name in archive/Country Data/ (if different from country_name)
       ```

    2. **scripts/run_matching.py**: Updated path logic to use `country_folder` if specified:
       ```python
       country_folder = config.get('country_folder') or country_name
       country_dir = project_root / 'archive' / 'Country Data' / country_folder
       ```

    3. **config/countries/drc_congo.yaml**: Added folder specification:
       ```yaml
       country_name: Democratic Republic of the Congo
       country_folder: "DRC, Congo"
       ```

    **Result:** DRC now runs successfully with 4,206+ matches 
	
25. The Gambia is failing because the script can't find any matches for that country. I think that's just because it's called "Gambia" in the ESPEN dataset.

    **Completed - Step 25:**

    **Cause:** Config had `country_name: The Gambia` but ESPEN uses `"Gambia"` (without "The").

    **Fix:** Updated `config/countries/the_gambia.yaml`:
    ```yaml
    country_name: Gambia          # Match ESPEN's naming
    country_folder: The Gambia    # Archive folder name
    ```

    **Result:** The Gambia now runs successfully:
    - 428 GIS features (Nominatim points)
    - 368 matches (355 exact + 13 fuzzy)
    - 86.0% match rate 