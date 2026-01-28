# osm2svg4opcd (v7.0)

**OpenStreetMap to SVG Converter for Open Platform Course Design (OPCD)**
    This is a complete rewrite of osm2svg_v5.py described earlier.  It is now called osm2svg_v7.py, where v6 was my development code.  It has undergone many changes to it since v5.  First lets review the command line help.

![Description](./out.jpg)

* To use this program, here is the typical workflow. 1) Follow the OPCD workflow to build your Inner and Outer Lidar / DEM elevation maps in QGIS.  Google "OPCD None to Done" (V4 Toolset) and download any programs you need, QGIS, Inkscape, etc. Please validate that your Inner and Outer QGIS Layers are setup correctly, as they are used to define the borders of the area we work with.  Next,  use QGIS to export a Hillshade image of your course using the 'Calculate from: Layer'. It defines the coordinates from your Inner or Outer layers.  The exported "Geotiff" Hillshade image (.tif), includes all of the coordinates information of the course area you're trying to build.   Typically your Hillshade image will match the 'Inner' layer, and we can use the Utilities/Download_MapOSM.py to download the map.osm (an XML OpenStreetMap of your course and the surrounding area).  You can also use Google, Bing exports from QGIS as input into 'utilities/Download_MapOSM.py'.  If your Hillshade, Google, Bing, DEM, export was clipped to your inner/outer layer then run,
 
``` python3 ./Utilities/Download_MapOSM --styles-file styles.json Hillshade.tif -D```

This will build the 'map.osm' needed for the area defined with the Inner/Outer area masks you defined. If you like, you can also use the OpenStreetMap.org website to lookup your course and use the 'Export' button to create the map.osm.  Note: in the later you may need to adjust the window size manually using Longitude and Latitude adjustments, which is in generic WGS84 coordinates.  The OpenStreetMap.org website has the advantage that the golf course is searchable, and if you feel up to it, you can even contribute a little time to updating or adding features.     

``` Utilities/Download_MapOSM.py [-h] --styles-file STYLES_FILE [--crs CRS] [-D] [-O] data_files [data_files ...] ```
where "data_files" are your laz/dem tiles, or a geotiff image like hillshade, Google or Bing exported from QGIS.  You may supply multiple data_files, and it will stitch them into 1 area and download the map.osm from it.  

* If you have the area defined by Longitude Latitude ranges like the OpenStreetMap.org Export, you can use this utility to pull the map.osm for the area.  ```Utilities/Overpass_downloader.py <lat1> <lon1> <lat2> <lon2> ```


* With the map.osm file, styles.json you can build your SVG mapping of the course and surrounding areas.  map.osm is a standard XML format and can be viewed in an editor.
  
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

* Change 1) styles.json has been expanded to include "clipper_mode": "unbreakable" or "default", which is used in the program's clip logic. Clipping_mode is for cartpaths, road, highways, etc defined by lines.  If they intersect or cross and are of a different type, one of the elements should be clipped, which might be controlled by z-order (the priority of the layering).  However, some roads and highways are "unbreakable" and regardless of z-order (the z-order preference for our features).  Railroad tracks are one example of a feature that should be unbreakable.  Waterways might be another.
*  Change 2) Buildings are a special case entry in our styles.json.  It's optional. If you require the outline of a building floor area in your SVG using the building style.  The building style has one special option, the 'distance-from', which represents the number of meters from the golf course boundary to include housing outlines (defined by the leisure.golf_course style element).  Set distance-from to 0, and no buildings except those on the course are included.  
* Change 3).  We have organized and named all of the Inkscape 'Layers and Objects' to be labeled as style-way_number-segment_number, so for example 'highway.residential-123456789-0' would be a residential road with way_id (corresponding to map.osm XML <node id="123456789') and the segment number corresponds to the clipped segment from any intersections or line breaks.
* Change 4). Added background images.  In the OPCD workflow, with QGIS create and inside and outside area of interest from our lidar images and import a QGIS 'XYZ tile' from Hillshade, Bing, Google, or even OpenStreetmap (not the one we use unfortunately, that would have made life so much easier).  We can then export those QGIS layers as tiffs and remap the coordinate system to a WGS84 form that should match our map.osm area of interest.   You can import these into the SVG as a background image.   For example, one can globally change the global opacity of the SVG streets to let the background show through slightly.   This allows trees, bushes, and land features to show through our color masks.  Very useful for Hillshade images.
* Change 5) We do some slight rounding of corners.  Many of the cartpaths and courses features from OpenStreetMap have been simplified to reduce the number of line segments near curves, creating points and kinks.  This program smooths them slightly.  


* The initial goal of this program was to take a golf course from OpenStreetMap (https://openstreetmap.org) and convert it to an Inkscape image (SVG) that could pass the ** GSPro ** course building workflow ** OPCD ** to get the course into ** Blender **, using an OPCD tool called the "Clender"  (A Cloud based conversion too.l that takes a SVG and converts to Blender).  The tools presented here could have many more uses besides golf-course development, for example, to train AIs like SAM (Meta's Segment Anything Model), where key features of OpenStreetMap are translated into an SVG image, and the features need to be labeled.


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

* Here is an example picture of the Seneca Golf course taken from Inkscape with a Hillshade background image and the OpenStreetMap overlay with a global opacity of about 50% on the SVG portion.
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

3. Scaling default is for 1 meter real = 1 mm in Inkscape (1 SVG unit).   Note: Yards, Feet or Inches could be scaled, but it could break things. Conversion factors are in the python code if you would like to try. 

### Step 0: Acquire your map.osm

Use a the Utility Download_MapOSM and provide it with an exported tif (geotif) image from QGIS of you Inner map.   Here, Hillshade works well.

python Utilities/Download_MapOSM.py --styles-file styles.json ~/Projects/Seneca/QGIS/Overlays/Seneca_Hillshade_Inner.tif -D -O


* **Input: styles.json, QGIS Exported Inner map, height map, DEM, Hillshade, other .geotifs.

* **Output: map.osm

### Step 1: Generate Raw SVG with Boundary Clipping

Run the core conversion script. This generates the initial, clipped SVG file containing `<polyline>` and `<path>` elements, **ensuring all features are constrained** to the map area.

python3 osm2svg.py


* **Input:** `map.osm`, `styles.json`. **Optional Inputs: ** 'Geotiff image backgrounds'.

* **Output:** `out.svg` (The primary clipped map output)

### Step 2: Convert Polylines to Paths and Smooth

Run the second-stage script to convert all `<polyline>` elements into `<path>` structures. This step often resolves "Color Errors" in Clender and prepares the geometry for smoothing.

python3 svg_points2path.py


* **Input:** `out.svg`

* **Output:** `paths_out.svg` (Polylines converted to basic paths) and **`smoothed_out.svg`** (Paths converted to Bézier curves for auto-smoothing).

### Step 3: (Optional and only if inset errors are reported.) Finalize for Inset Operations

Run the optional bunker fix script if your `smoothed_out.svg` fails validation due to narrow or complex sandtrap shapes.  Probably not needed. This step widens and rounds bunker shapes that fail 'Clender' with an inset error.  

python3 fix_bunker_inset.py 


* **Input:** `smoothed_out.svg`

* **Output:** `final_smoothed_out.svg`

### Step 4:  This step is optional but it is used to clip any SVG features that may extend out of the boundary of the Inner/Outer layers.  Map.osm does not clip roads at boundaries, just at line breaks.     So this is used to match the SVG to the Inner/Outer layer from the QGIS stage of the OPCD processes.  Still a work in progress.  Known Bug: at the end caps of the paths for roads, cartpaths, and the like  are clipped square and may extend a few pixels outside of the boundry.  It's not noticable until zoomed in.   You can do the same in Inkscape.

python3 svg_clipper.py

* ** Input:**  `smoothed_out.svg'

* *** Output:** `clipped_final.svg`

