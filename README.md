# osm2svg4opcd

**OpenStreetMap to SVG Converter for Open Platform Course Design (OPCD)**

A specialized tool built to streamline the creation of high-quality, pre-processed SVG map data for golf course simulation design, particularly for the **GSPro** / **OPCD** workflow leading into **Blender** (via "Clender").

## 🚀 Key Features

This tool processes raw OpenStreetMap (`.osm`) data and converts it into a scale-accurate SVG file, ready for the next stages of the OPCD pipeline.

* **Boundary Clipping (V6 Feature):** **CRUCIAL FOR OPCD MATCHING.** Ensures that all geometry (roads, water, paths) is precisely clipped to the geographic bounds defined in the input `.osm` file. This prevents bleed and guarantees a clean, exact match to your Lidar/DEM area of interest (the **OPCD Inner terrain** boundary).

* **MultiPolygon Relation Support (V5 Feature):** Correctly handles complex area features (like fairways, water bodies, or building footprints) defined by OSM `<relation>` tags, including support for outer boundaries and inner holes (`fill-rule="evenodd"`).

* **Scale-Accurate Output:** The SVG output is scaled such that 1 real-world meter is represented by 1mm in the SVG document, ensuring dimensional consistency for your 3D workflow.

* **Customizable Styling and Z-Ordering:** Feature identification, color assignment, line thickness, and draw order are fully controlled by the centralized `styles.json` configuration file.

* **Second-Stage Processing Support:** Designed to feed into secondary scripts (`svg_points2path.py`) to convert SVG `<polyline>` elements to optimized `<path>` structures, which resolves common "Color Errors" reported by the Clender process.

## 📐 Core Concepts

### OpenStreetMap Data (`map.osm`)

The input file, `map.osm`, is an XML-based language containing geographical features identified by volunteers. Each feature is defined by **Key/Value** pairs (e.g., `<tag k='highway' v='primary'/>`).

The **`styles.json`** file is the mapping engine. It dictates which OSM tags are searched for and what SVG attributes (like `fill`, `stroke`, `stroke-width`, and `z-order`) are applied to the resulting geometry. You can easily add support for new features (like specific building types) by updating this file.

### Licensing

* The source code (e.g., `osm2svg.py`, `svg_points2path.py`, `styles.json`) is released under the **MIT License**.

* The raw data file (`map.osm`), exported from OpenStreetMap, is covered under the **Open Data Commons Open Database License (ODbL)**.

### Utility Scripts for Data Acquisition

To simplify obtaining the required `map.osm` file, the project includes utility scripts designed to use the Overpass API:

* **`overpass_downloader.py`** and **`Download_MapOSM.py`**: These helper tools are designed to take coordinates derived from your OPCD QGIS GeoTIFFs, calculate the necessary bounding box, and download the corresponding `map.osm` data from the Overpass API, ensuring the OSM file matches your terrain area.

## 🛠️ Usage Workflow (3-Step Process)

This project is currently a three-step process to generate a Clender-compatible SVG.

### Prerequisites

1. **Download your data:** Obtain your `map.osm` file (either manually via OpenStreetMap's "Export" function or using the provided utility scripts), ensuring its bounding box precisely matches the longitude and latitude coordinates of your terrain's Inner Lidar/DEM images (often determined in QGIS).

2. **Configuration:** Ensure your styling is correct in `styles.json`.

3. **Scaling:** Set your desired map scale ratio in `scale_config.txt` (e.g., `1000` for $1:1000$, or a custom value for imperial units).

### Step 1: Generate Raw SVG with Boundary Clipping

Run the core conversion script. This generates the initial, clipped SVG file containing `<polyline>` and `<path>` elements, **ensuring all features are constrained** to the map area.

python3 osm2svg.py


* **Input:** `map.osm`, `styles.json`, `scale_config.txt`

* **Output:** `out.svg` (The primary clipped map output)

### Step 2: Convert Polylines to Paths and Smooth

Run the second-stage script to convert all `<polyline>` elements into `<path>` structures. This step often resolves "Color Errors" in Clender and prepares the geometry for smoothing.

python3 svg_points2path.py


* **Input:** `out.svg`

* **Output:** `paths_out.svg` (Polylines converted to basic paths) and **`smoothed_out.svg`** (Paths converted to Bézier curves for auto-smoothing).

### Step 3: (Optional and only if inset errors are reported.) Finalize for Inset Operations

Run the optional bunker fix script if your `smoothed_out.svg` fails validation due to narrow or complex sandtrap shapes.

python3 fix_bunker_inset.py


* **Input:** `smoothed_out.svg`

* **Output:** `final_smoothed_out.svg`

### Step 4:  This step is optional but it is used to clip any SVG features that may extend out of the boundary.   It's used to match the SVG to the Inner elevation map from the QGIS stage of the OPCD processes.  Still a work in progress.  Know bug: at the end caps of the paths for roads, cartpaths, and the like  are clipped square and may extend a few pixels outside of the boundry.  It's not noticable until zoomed in.

python3 svg_clipper.py

* ** Input:**  `smoothed_out.svg'

* *** Output:** `clipped_final.svg`

