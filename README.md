# osm2svg4opcd (v9.0)

**OpenStreetMap to SVG Converter for Open Platform Course Design (OPCD)**

This is a large update of osm2svg_v7.py tool. Version 9 (v9) represents the stable release of the development code previously referred to as version 8. It includes significant architectural changes to support the modern OPCD workflow.  In this version we are takinging advantage of the new 'Clender' Beta mesher which does not need clips and cuts of cartpaths, corners are managed well.  We also do some smoothing of corners on the course features.  Tees, Green, Fairways, Sandtraps.   We maintain the out.svg measurements of 1mm SVG = 1 meter real.  We have developed some nice cut rules, so at interestions of like features (roads, streets, etc) the shapes are unioned.  If the interesection is of different features a cartpath to a road, the feature with a higher z-order take presidence and the lower z-order gets clipped (or gilotiened).  We also introduce a new styles.json entry called "clipper_mode" which can be either "unbreakable" or "default", which is used for structures nothing can clip, like railroads, creeks and the like.

** New in V9. **
Since we are using the WGS94 coordinates used by OpenStreetmap, our terrain.obj needs to be built from the same coordinates for proper terrain allignment.  We introduce 'lidar2obj.py' which will take a heightmap from QGIS exported as geotiff and convert it to a terrain.obj suitable for use inside blender and ready for the OPCD Tools  conform mesh to terrain.  The usage is very simple; if you can build lidar/Dem elevation map for your outer.  This insures the entire .blend from cleander is confomed;  then run ``` python3 ./lidar2obj.py --infile Seneca_Lidar_Outer.tif --scale_z 0.3048 --sigma 5.0 ``` obviously using your own Lidar/DEM outer.   

![Description](./out.jpg)

# Typical Workflow
To use this program effectively, follow the standard OPCD setup, specifically the instructions provided in the "None to Done" V4 Toolset document.  Download any prerequisit programs you need, QGIS, Inkscape, and optionally your other tools.  As instructed in the "None to Done" document, build your Inner and Outer Lidar/DEM elevation maps in QGIS.  These layers define the geographic boundaries of your project.

Export from QGIS: Export a Hillshade and/or Aerial image (or XYZ Tiles google, bing) as a GeoTIFF (.tif). Ensure you use the "Calculate from: <Layer>" and select your Inner or Outer layer during export.  The resulting GeoTIFF will contain the coordinate metadata needed for alignment and clipping.

Acquire OSM Data: Use the provided utility: ``` python Utilities/Download_MapOSM.py --styles-file styles.json Hillshade.tif -D``` This automatically downloads a map.osm file precisely clipped to the bounds of your GeoTIFF.

Alternative Acquisition: You can also use the website OpenStreetMap.org and select the "Export" button, though you may need to manually adjust the WGS84 Longitude and Latitude coordinates to match your project area.


* To rehash; the typical workflow is as follows. Use the OPCD workflow to build your Inner and Outer Lidar / DEM elevation maps in QGIS.  Google "OPCD None to Done" (V4 Toolset)".  Please make sure you get your Inner and Outers setup correctly as it used to define the borders of the area we work with.  Use QGIS to export a Hillshade image of your course using 'Layer' to define coordinates from your Inner or Outer layer.  This can be helpful because if you export your Hillshade as a "Geotiff" image (.tif), it includes all of the GIS coordinates for the course your trying to build.   With you Hillshade image typically matched to the 'Inner' layer, we can us then Utilities/Download_MapOSM.py to download map.osm (an XML OpenStreetMap image).  You can also do the same as Hillshade with the QGIS XYZ Tile tool for Google, Bing and even OpenStreetMap.  Sadly that OpenStreetMap query is in a completely wrong format which is why this program exists.   If your Hillshade, Google, Bing, DEM, export was clipped to your inner/outer layer, then run;  "Utilities/Download_MapOSM --styles-file styles.json Hillshade.tif -D".   This will build the map.osm we need of the area in the Inner/outer area defined by your geotiff image; hillshade.tif   You can also use the OpenStreetMap.org website to lookup your course and use the 'Export' button to create the map.osm.  Just note; in the later you may need to adjust the window size manually with Longitude and Latitude adjustments to the WGS84 coordinates.  The OpenStreetMap.org website is community driven and if you feel up to it, contribute some time to updating or adding features not described in your local area.     

* ``` Utilities/Download_MapOSM.py [-h] --styles-file STYLES_FILE [--crs CRS] [-D] [-O] data_files [data_files ...] ```
(Where "data_files" are your laz/dem tiles, or a geotiff image from QGIS)

* If you have the area defined by Longitude, latitude ranges you can use this to pull the map.osm file for that area. ```Utilities/Overpass_downloader.py <lat1> <lon1> <lat2> <lon2> ```



```
osm2svg4opcd$ python ./osm2svg_v7.py --help
 usage: osm2svg_v7.py [-h] [--infile INFILE] [--outfile OUTFILE] [--styles STYLES] [--background1 BACKGROUND1]
                     [--background2 BACKGROUND2] [--background3 BACKGROUND3] [--background4 BACKGROUND4]

 Converts OpenStreetMap data (in projected meters) to an SVG file, with optional GeoTIFF background layers.

 options:
  -h, --help            show this help message and exit
  --infile INFILE       Input OSM XML file (default: map.osm)
  --outfile OUTFILE     Output SVG file (default: out.svg)
  --styles STYLES       JSON style definition file (default: styles.json)
  --background1 BACKGROUND1
                        Path to the first GeoTIFF image file for the background.
  --background2 BACKGROUND2
                        Path to the second GeoTIFF image file for the background.
  --background3 BACKGROUND3
                        Path to the third GeoTIFF image file for the background.
  --background4 BACKGROUND4
                        Path to the fourth GeoTIFF image file for the background.

```

## CHANGES in v7.

* Change 1) styles.json has been expanded to include "clipper_mode": "unbreakable" or "default", which is used in the program's clip logic. Clipping_mode is for cartpaths, road, highways etc.  Typically when a cartpath, waterway, crosses or intersect,  we need to decide how to manage the interesection.  Typically this is managed by the "z-order" styles.json entry where the higher z-order remains unclipped and the lower "z-order" is clipped.   Some roads and highways regardless of z-order, are "unbreakable".   Railroad tracks are one example of a feature that should be unbreakable.  Waterways might be another.
* Change 2) buildings are a special case entry in our styles.json.  It's optional, but if you want to outline the building floor area in your SVG using the building style.  The building style has one special option 'distance-from' which represents the number of meters from the golf course boundry (as defined in the leisure.golf_course style) to include housing outlines in your svg.  Set distance-from to 0 and no buildings except those on the course are identified.  
* Change 3).  We have organized and named all of the Inkscape 'Layers and Objects' to be labled as style-way_number-segment_number, so for example 'highway.residential-123456789-0' would be a residential road with way_id (corresponding to map.osm XML <node id="123456789') and the segment number corresponds to the clipped segment from any intersections.
* Change 4). Added background images.  In the OPCD workflow, use QGIS to create an Inside and Outside area of interest from your lidar images and export a QGIS 'XYZ tile' for Hillshade, Bing, Google and even OpenStreetmap (not the one we use unfortunately, it would have made life so much easier).  We can then export those QGIS layers as tiffs and remap the coordinate system the a WGS84 form that should match our map.osm area of interest.   You can the import these into the svg as a background image.   Then for example, one can globally change the global opacity of the SVG streets to let the background show through slightly.   This shows trees, bushes and land features to show throught.  Very useful for Hillshade images.
* Change 5) We do some slight rounding of corners.  Many of the cartpaths and courses features from OpenStreetMap have simplified and reduced the number of linesegments near curves creating points and kinks in things like cartpaths, greens, fairways.   This program atempts to smooth them.  


* The initial goal of this program was to take a golf course from OpenStreetMap (https://openstreetmap.org) and
convert it to an Inkscape image (SVG) that could pass the ** GSPro ** course building workflow ** OPCD ** to get the course into ** Blender **, using an OPCD tool called the "Clender".   However, this tool could have many more uses where key features of OpenStreetMap need to be translated into a SVG image; for example training an AI with OSM features with labled images (ie. classifiers).


## 🚀 Key Features

This tool processes raw OpenStreetMap (`.osm`) data and converts it into a scale-accurate SVG file, ready for the next stages of the OPCD pipeline.

* **Boundary Clipping (V6 Feature):** **CRUCIAL FOR OPCD MATCHING.** Ensures that all geometry (roads, water, paths) is precisely clipped to the geographic bounds defined in the input `.osm` file. This prevents bleed and guarantees a clean, exact match to your Lidar/DEM area of interest (the **OPCD Inner terrain** boundary).

* **MultiPolygon Relation Support (V5 Feature):** Correctly handles complex area features (like fairways, water bodies, or building footprints) defined by OSM `<relation>` tags, including support for outer boundaries and inner holes (`fill-rule="evenodd"`).

* **Scale-Accurate Output:** The SVG output is scaled such that 1 real-world meter is represented by 1mm in the SVG document, ensuring dimensional consistency for your 3D workflow.

* **Customizable Styling and Z-Ordering:** Feature identification, color assignment, line thickness, and draw order are fully controlled by the centralized `styles.json` configuration file.

* **Second-Stage Processing Support:** Designed to feed into secondary scripts (`svg_points2path.py`) to convert SVG `<polyline>` elements to optimized `<path>` structures, which resolves common "Color Errors" reported by the Clender process.

## 📐 Core Concepts

### OpenStreetMap Data (`map.osm`)

* The input file, `map.osm`, is an XML-based language containing geographical features identified by volunteers. Each feature is defined by **Key/Value** pairs (e.g., `<tag k='highway' v='primary'/>`).  Map.osm is obtained by going to the OpenStreetMaps website, finding the location of interest, and using the website's Export function.  Geographically OpenStreetMap uses WGS 84 coordinates.   An excellent alternative is to use the Overpass API, which is used in the Utility programs to download a map.osm of the area of interest you provide (also in WGS 84 coordinates).  

* The **`styles.json`** file is the mapping engine. It dictates which OSM tags are searched for and what SVG attributes (like `fill`, `stroke`, `stroke-width`, and `z-order`) are applied to the resulting geometry. You can easily add support for new features (like specific building types) by updating this file.  In the next version, I will be adding a new command for stroke-based objects (roads, paths, highways, railways, waterways) called 'clipper-mode': either 'default' or 'unbreakable'.  This is to process intersections and insert breaks in the line segments to prevent overlaps.  'clipper-mode' is only valid with stroke objects (line segments). 

* Here is an exmple picture of Seneca Golf course taken from Inkscape with a Hillshade background image and the openstreetnmap overlay with a global opacity of about 50% on the SVG portion.
![Description](./out_hillshade.jpg)


### Licensing

* The source code (e.g., `osm2svg_v7.py`, `svg_points2path.py`, `styles.json`) is released under the **MIT License**.

* The raw data file (`map.osm`), exported from OpenStreetMap, is covered under the **Open Data Commons Open Database License (ODbL)**.

### Utility Scripts for Data Acquisition

To simplify obtaining the required `map.osm` file, the project includes utility scripts designed to use the Overpass API:

* **`overpass_downloader.py`** and **`Download_MapOSM.py`**: These helper tools are designed to take coordinates derived from your OPCD QGIS GeoTIFFs, calculate the necessary bounding box, and download the corresponding `map.osm` data from the Overpass API, ensuring the OSM file matches your terrain area.

## 🛠️ Usage Workflow (3-Step Process)

This project is currently a three-step process to generate a Clender-compatible SVG.

### Prerequisites

1. **Download your data:** Obtain your `map.osm` file (either manually via OpenStreetMap's "Export" function or using the provided utility scripts), ensuring its bounding box precisely matches the longitude and latitude coordinates of your terrain's Inner Lidar/DEM images (often determined in QGIS).

2. **Configuration:** Ensure your styling is correct in `styles.json`.  Example: Do you want to include buildings? Change a color?  Change the width of a cartpath?  Stroke-width is in meters. Change the z-order (who's on top?).

3. Scaling default is for 1 meter real = 1 mm in inkscape (1 SVG unit).   Note: Yards, Feet or Inches could be scaled but it could break things. (conversion factors are in the code if you want to try). 

### Step 0: Acquire your map.osm

Use a the Utility Download_MapOSM and provide it with an exported tif (geotif) image from QGIS of you Inner map.   Here Hillshade works well.

python Utilities/Download_MapOSM.py --styles-file styles.json ~/Projects/Seneca/QGIS/Overlays/Seneca_Hillshade_Inner.tif -D -O


* **Input: styles.json, QGIS Exported Inner map, height map, DEM, Hillshade, other .geotifs.

* **Output: map.osm

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

### Step 4:  This step is optional but it is used to clip any SVG features that may extend out of the boundary.   It's used to match the SVG to the Inner elevation map from the QGIS stage of the OPCD processes.  Still a work in progress.  Known Bug: at the end caps of the paths for roads, cartpaths, and the like  are clipped square and may extend a few pixels outside of the boundry.  It's not noticable until zoomed in.

python3 svg_clipper.py

* ** Input:**  `smoothed_out.svg'

* *** Output:** `clipped_final.svg`

