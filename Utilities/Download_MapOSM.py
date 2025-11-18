#!/usr/bin/env python3

"""
This program takes DEM or LAZ plate(s)  and uses the coordinates stored in the Geotiff(s) or LAZ files for the plate location and convertes that to WGS 84 Logitude/Latitude coordinates.  Optionally will query OpenStreetMaps.org to download the map.osm file with all of the labled features.  For multiple plates, it finds the maximum and minimums coordinates and uses thos coordinates.   You can then use that to run 'osm2svg_v4.py' to create an initial .SVG of the golf course (or what ever you desire).  
The Overpass Query Language (QL) query is now generated DYNAMICALLY by reading the tags from a provided styles.json file, making the process self-configuring and robust.
"""
import pyproj
import math
import argparse
import numpy as np
import sys
import os
import requests
import json 
import urllib.parse 

# --- Dependency Checks ---
try:
    import rasterio
except ImportError:
    print("ERROR: rasterio library not found. Please install with 'pip install rasterio'")
    sys.exit(1)

try:
    import laspy
except ImportError:
    print("ERROR: laspy library not found. Please install with 'pip install laspy[laz]'")
    sys.exit(1)
    
try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Please install with 'pip install requests'")
    sys.exit(1)


# --- TARGET SCALING CONSTANT ---
METERS_PER_FOOT = 1 / 3.28084
OSM_CRS = "EPSG:4326"

# --- NEW UTILITY FUNCTION TO PARSE STYLES.JSON ---

def get_osm_tags_from_styles(style_path):
    """
    Reads the styles JSON file and extracts all OSM tags (keys) used for styling.
    
    It converts keys using a dot separator (e.g., 'golf.fairway') into 
    the OSM tag format (e.g., 'golf=fairway'). Keys without a dot (e.g., 'highway')
    are preserved as key-only lookups. The key 'Comment' is skipped.
    """
    if not os.path.exists(style_path):
        print(f"FATAL ERROR: Styles file not found at '{style_path}'.")
        sys.exit(1)
        
    try:
        with open(style_path, 'r') as f:
            styles = json.load(f)
            
            # The keys of the styles dictionary are the tags we need to query
            raw_keys = list(styles.keys())
            
            required_tags = []
            
            for key in raw_keys:
                # 1. Skip keys that are clearly not OSM tags (like comments)
                if key.lower() == "comment":
                    continue
                    
                # 2. Convert dot-separated keys (e.g., 'golf.fairway') to 'key=value'
                if '.' in key:
                    # Overpass QL requires key=value, not key.value
                    osm_tag = key.replace('.', '=', 1)
                    required_tags.append(osm_tag)
                else:
                    # 3. For key-only lookups (e.g., 'highway', 'waterway'), preserve the key name
                    required_tags.append(key)
            
            if not required_tags:
                 print("WARNING: Styles file contains no recognizable OSM tags. Query will be empty.")
                 return []
            
            print(f"INFO: Successfully loaded {len(required_tags)} unique OSM tags from '{os.path.basename(style_path)}'.")
            print(f"INFO: Converted Tags (first 5): {required_tags[:5]}...")
            return required_tags
            
    except json.JSONDecodeError:
        print(f"FATAL ERROR: Failed to parse '{style_path}'. Ensure it is valid JSON.")
        sys.exit(1)
    except Exception as e:
        print(f"FATAL ERROR: An unexpected error occurred while reading the styles file: {e}")
        sys.exit(1)


# --- EXISTING METADATA FUNCTIONS (no change) ---
def get_geotiff_metadata(file_path):
    """Reads spatial extent, CRS, and unit conversion factor from a GeoTIFF."""
    try:
        with rasterio.open(file_path) as src:
            bounds = src.bounds
            dem_extent_native = {
                "min_x": bounds.left,
                "max_x": bounds.right,
                "min_y": bounds.bottom,
                "max_y": bounds.top,
            }

            if src.crs and src.crs.to_epsg():
                dem_crs = f"EPSG:{src.crs.to_epsg()}"
            else:
                print(f"ERROR: GeoTIFF {file_path} is missing CRS information.")
                return None, None, None 

            if 'feet' in str(src.crs.linear_units).lower():
                 print(f"INFO: Detected units are {src.crs.linear_units}. Using ft-to-meter conversion.")
                 conversion_factor = METERS_PER_FOOT
            elif 'meter' in str(src.crs.linear_units).lower():
                 print("INFO: Detected units are meters. Conversion factor is 1.0.")
                 conversion_factor = 1.0
            else:
                 print(f"WARNING: Could not reliably determine unit type for {file_path}. Assuming feet.")
                 conversion_factor = METERS_PER_FOOT

            return dem_crs, dem_extent_native, conversion_factor
            
    except rasterio.RasterioIOError:
        print(f"ERROR: Could not open or read the GeoTIFF file at {file_path}")
        return None, None, None 
    except Exception as e:
        print(f"An unexpected error occurred for GeoTIFF file {file_path}: {e}")
        return None, None, None


def get_laz_metadata(file_path):
    """Reads spatial extent, CRS, and unit conversion factor from an LAZ/LAS file."""
    try:
        with laspy.open(file_path) as las_file:
            header = las_file.header
            
            dem_extent_native = {
                "min_x": header.min_x,
                "max_x": header.max_x,
                "min_y": header.min_y,
                "max_y": header.max_y,
            }

            if las_file.vlrs:
                try:
                    wkt = las_file.header.vlrs.get_wkt()
                    crs = pyproj.CRS.from_wkt(wkt)
                    dem_crs = f"EPSG:{crs.to_epsg()}"
                    print(f"INFO: Successfully extracted CRS from LAZ WKT: {dem_crs}")
                except Exception:
                    dem_crs = None
            else:
                dem_crs = None

            if dem_crs is None:
                print("WARNING: Could not automatically detect CRS from LAZ header (VLRs).")
                dem_crs = "USER_REQUIRED"
                
            x_diff = header.max_x - header.min_x
            if x_diff > 100000:
                conversion_factor = METERS_PER_FOOT
                print("INFO: LAZ coordinates have large magnitude difference (>100,000); assuming native units are feet.")
            else:
                conversion_factor = 1.0
                print("INFO: LAZ coordinates have small magnitude difference; assuming native units are meters.")

            return dem_crs, dem_extent_native, conversion_factor

    except Exception as e:
        print(f"An unexpected error occurred for LAZ file {file_path}: {e}")
        return None, None, None


def get_file_metadata(file_path):
    """Unified function to dispatch to the correct reader based on file extension."""
    ext = file_path.lower().split('.')[-1]
    
    if ext in ['tif', 'tiff']:
        return get_geotiff_metadata(file_path)
    elif ext in ['las', 'laz']:
        return get_laz_metadata(file_path)
    else:
        print(f"ERROR: Unsupported file extension '{ext}'. Must be .tif, .tiff, .las, or .laz.")
        return None, None, None


def calculate_osm_bounds_for_plate(dem_extent, transformer_to_osm):
    """Transforms the four corners of a single DEM extent to WGS 84 Lat/Lon."""
    min_x, max_x = dem_extent["min_x"], dem_extent["max_x"]
    min_y, max_y = dem_extent["min_y"], dem_extent["max_y"]

    corners = [
        (min_x, max_y),
        (max_x, max_y),
        (min_x, min_y),
        (max_x, min_y),
    ]

    lons, lats = zip(*[transformer_to_osm.transform(x, y) for x, y in corners])

    return {
        "top": max(lats),
        "bottom": min(lats),
        "left": min(lons),
        "right": max(lons)
    }

def calculate_final_svg_parameters(global_dem_extent, dem_crs, conversion_factor):
    """Calculates SVG viewBox parameters based on the combined global DEM extent."""
    width_native = global_dem_extent["max_x"] - global_dem_extent["min_x"]
    height_native = global_dem_extent["max_y"] - global_dem_extent["min_y"]

    width_m = width_native * conversion_factor
    height_m = height_native * conversion_factor
    
    viewbox_width = math.ceil(width_m)
    viewbox_height = math.ceil(height_m)

    print("--- FINAL SVG SCALING PARAMETERS ---")
    print(f"DEM CRS (Used for Scaling): {dem_crs}")
    print(f"Combined DEM Width (Native Units): {width_native:.2f}")
    print(f"Combined DEM Height (Native Units): {height_native:.2f}")
    print(f"SVG ViewBox Width (meters): {viewbox_width}")
    print(f"SVG ViewBox Height (meters): {viewbox_height}")
    print(f"Conversion Factor (Meters/Native Unit): {conversion_factor:.6f}")
    print("-" * 50)
    
    return {
        "viewbox_width": viewbox_width,
        "viewbox_height": viewbox_height,
        "global_min_x": global_dem_extent["min_x"],
        "global_min_y": global_dem_extent["min_y"],
        "dem_crs": dem_crs,
        "conversion_factor": conversion_factor
    }


def download_osm_data(bounds, required_tags, output_file='map.osm'):
    """
    Downloads OSM data, constructing the QL query dynamically from the required_tags list.
    """
    overpass_url = "http://overpass-api.de/api/interpreter"
    
    # Overpass QL requires bounds in the format: (south lat, west lon, north lat, east lon)
    bbox_str = f"{bounds['bottom']:.6f},{bounds['left']:.6f},{bounds['top']:.6f},{bounds['right']:.6f}"
    
    query_elements = []
    
    # Dynamically generate Overpass QL elements from the required_tags list
    for tag in required_tags:
        # tag is now in the format 'key=value' or 'key'
        
        # 1. Query for Ways and Relations (Area/Line features)
        # Use simple tag lookup: [key=value] or [key]
        if '=' in tag:
            # key=value, e.g., 'golf=fairway'
            query_elements.append(f'  way[{tag}]({bbox_str});')
            query_elements.append(f'  relation[{tag}]({bbox_str});')
            # For specific key=value pairs, nodes might be relevant (e.g., golf=pin)
            if tag.split('=', 1)[0] in ["golf", "amenity", "natural", "building"]: 
                 query_elements.append(f'  node[{tag}]({bbox_str});')
        else:
            # key only, e.g., 'highway', 'waterway' (meaning any value for this key)
            query_elements.append(f'  way[{tag}]({bbox_str});')
            query_elements.append(f'  relation[{tag}]({bbox_str});')
            query_elements.append(f'  node[{tag}]({bbox_str});')
            
    if not query_elements:
        print("WARNING: No valid OSM tags were extracted to build the query. Skipping download.")
        return

    # Combine the dynamically generated elements into the final QL query
    query = f'''
[out:xml][timeout:60];
(
{'\n'.join(query_elements)}
);

// Recurse: Get all nodes belonging to the ways/relations found above. 
// This is essential as the ways/relations only contain references to node IDs.
(._;>;); 
out body; 
'''
    payload = {'data': query}
    
    print(f"\n--- Attempting to download OSM data via Overpass QL ---")
    print(f"Bounding Box: {bbox_str} (lat_min, lon_min, lat_max, lon_max)")
    print(f"Query built dynamically using {len(required_tags)} tags from styles file.")
    print(f"Requesting from Overpass API: {overpass_url}")

    try:
        response = requests.post(overpass_url, data=payload, timeout=70)
        response.raise_for_status() 
        
        osm_xml_content = response.content.decode('utf-8')

        # 1. Construct the mandatory <bounds> tag using the calculated coordinates
        bounds_tag = (
            f'<bounds minlat="{bounds["bottom"]:.6f}" minlon="{bounds["left"]:.6f}" '
            f'maxlat="{bounds["top"]:.6f}" maxlon="{bounds["right"]:.6f}"/>'
        )

        # 2. Find the correct XML insertion point (right after the root <osm> tag closes)
        osm_tag_start = osm_xml_content.find('<osm')
        osm_tag_end = osm_xml_content.find('>', osm_tag_start)
        
        if osm_tag_end != -1:
            # Insertion point is RIGHT AFTER the closing angle bracket of the <osm> tag
            insertion_point = osm_tag_end + 1
            
            # Insert the bounds tag on its own line after the <osm> root tag
            modified_content = (
                osm_xml_content[:insertion_point] + 
                '\n' + bounds_tag + '\n' +
                osm_xml_content[insertion_point:]
            )

            # 3. Save the modified content
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(modified_content)
                
            print(f"SUCCESS: OSM data downloaded ({len(response.content)/1024:.2f} KB) and modified.")
            print(f"File saved to '{output_file}'. Now contains all tags defined in your styles file.")
        else:
            print("ERROR: Could not find the root <osm> tag in the downloaded XML.")
            
    except requests.exceptions.HTTPError as http_err:
        print(f"FATAL ERROR: HTTP error occurred during OSM download: {http_err}")
    except requests.exceptions.RequestException as req_err:
        print(f"FATAL ERROR: A connection error occurred during OSM download: {req_err}")
    except Exception as e:
        print(f"ERROR: Failed to process or save the file: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calculate OSM WGS 84 bounds and SVG scaling parameters "
                    "by reading the metadata of one or more GeoTIFF or LAZ/LAS heightmaps. "
                    "The Overpass Query is automatically generated from the styles file.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "data_files", 
        nargs='+',
        help="One or more paths to GeoTIFF (.tif) or LAZ/LAS (.laz, .las) files."
    )
    parser.add_argument(
        "--styles-file",
        required=True, # MANDATORY NEW ARGUMENT
        help="Path to the JSON file containing the OSM tags and styling rules (e.g., styles.json)."
    )
    parser.add_argument(
        "--crs",
        help="The EPSG code of the native CRS, e.g., EPSG:3089. Mandatory if any LAZ/LAS file is used and its CRS cannot be inferred."
    )
    parser.add_argument(
        "-D", "--download",
        action="store_true",
        help="If set, automatically downloads the resulting OpenStreetMap data "
             "as 'map.osm' using the calculated WGS 84 bounds via the Overpass API."
    )
    parser.add_argument(
        "-O", "--output-config",
        action="store_true",
        help="If set, saves the SVG transformation parameters to 'svg_config.json'."
    )
    args = parser.parse_args()
    
    # --- STEP 1: Load Required Tags from Styles File ---
    required_tags = get_osm_tags_from_styles(args.styles_file)

    # --- GLOBAL AGGREGATION VARIABLES ---
    global_min_x, global_min_y = np.inf, np.inf
    global_max_x, global_max_y = -np.inf, -np.inf
    global_min_lon, global_min_lat = np.inf, np.inf
    global_max_lon, global_max_lat = -np.inf, -np.inf
    
    first_dem_crs = None
    first_conversion_factor = None
    
    print("--- Multi-Plate Data Processor (GeoTIFF & LAZ) ---")

    for i, data_file in enumerate(args.data_files):
        print(f"\n--- Processing Plate {i+1}/{len(args.data_files)}: {os.path.basename(data_file)} ---")
        
        dem_crs, dem_extent_native, conversion_factor = get_file_metadata(data_file)
        
        if dem_crs is None: continue
            
        if dem_crs == "USER_REQUIRED":
            if args.crs:
                dem_crs = args.crs
            else:
                print(f"FATAL ERROR: LAZ file {data_file} requires the native CRS to be specified using the '--crs' argument.")
                sys.exit(1)

        if first_dem_crs is None:
            first_dem_crs = dem_crs
            first_conversion_factor = conversion_factor
            try:
                # Transformer to convert from DEM CRS (e.g., EPSG:3089) to OSM WGS 84 (EPSG:4326)
                transformer_to_osm = pyproj.Transformer.from_crs(dem_crs, OSM_CRS, always_xy=True)
            except Exception as e:
                print(f"FATAL ERROR: Invalid CRS '{dem_crs}' provided or detected. Details: {e}")
                sys.exit(1)
            
        elif dem_crs != first_dem_crs:
            print(f"FATAL ERROR: Plate {os.path.basename(data_file)} uses CRS {dem_crs}, but the first plate used {first_dem_crs}. All plates must share the same CRS for combined processing.")
            sys.exit(1)
        
        if conversion_factor != first_conversion_factor:
            print("WARNING: Conversion factor differs between plates. Using the first plate's factor.")

        global_min_x = min(global_min_x, dem_extent_native["min_x"])
        global_max_x = max(global_max_x, dem_extent_native["max_x"])
        global_min_y = min(global_min_y, dem_extent_native["min_y"])
        global_max_y = max(global_max_y, dem_extent_native["max_y"])

        plate_osm_bounds = calculate_osm_bounds_for_plate(dem_extent_native, transformer_to_osm)

        global_min_lon = min(global_min_lon, plate_osm_bounds["left"])
        global_max_lon = max(global_max_lon, plate_osm_bounds["right"])
        global_min_lat = min(global_min_lat, plate_osm_bounds["bottom"])
        global_max_lat = max(global_max_lat, plate_osm_bounds["top"])
        
    if first_dem_crs is None:
        print("\nERROR: No valid data files were processed.")
        sys.exit(1)

    global_dem_extent = {
        "min_x": global_min_x,
        "max_x": global_max_x,
        "min_y": global_min_y,
        "max_y": global_max_y,
    }

    global_osm_bounds = {
        "top": global_max_lat,
        "bottom": global_min_lat,
        "left": global_min_lon,
        "right": global_max_lon
    }

    # 5. Calculate final SVG parameters
    svg_params = calculate_final_svg_parameters(global_dem_extent, first_dem_crs, first_conversion_factor)
    
    print("--- FINAL AGGREGATED OSM WGS 84 BOUNDARIES (FOR OSM EXPORT) ---")
    print(f"Top Latitude:    {global_max_lat:.6f}")
    print(f"Bottom Latitude: {global_min_lat:.6f}")
    print(f"Left Longitude:  {global_min_lon:.6f}")
    print(f"Right Longitude: {global_max_lon:.6f}")
    print("-" * 50)
    print(f"Recommended SVG ViewBox: 0 0 {svg_params['viewbox_width']} {svg_params['viewbox_height']}")
    
    # 6. Optional Config Save Step
    if args.output_config:
        config_data = {
            "viewbox_width": svg_params["viewbox_width"],
            "viewbox_height": svg_params["viewbox_height"],
            "dem_crs": svg_params["dem_crs"],
            "global_min_x": global_dem_extent["min_x"], 
            "global_min_y": global_dem_extent["min_y"], 
            "conversion_factor": svg_params["conversion_factor"],
        }
        config_file = 'svg_config.json'
        with open(config_file, 'w') as f:
            json.dump(config_data, f, indent=4)
        print(f"\nSUCCESS: Saved SVG transformation configuration to '{config_file}'.")

    # 7. Optional Download Step
    if args.download:
        # Pass the dynamically extracted tags to the download function
        download_osm_data(global_osm_bounds, required_tags)

    # --- TEST OUTPUT ---
    wgs84_to_native = pyproj.Transformer.from_crs(OSM_CRS, first_dem_crs, always_xy=True)
    
    def wgs84_to_svg_coords(lon, lat):
        """Replicates the transformation logic for the test."""
        # Use the corrected wgs84_to_native transformer
        x_native, y_native = wgs84_to_native.transform(lon, lat)
        offset_x_native = x_native - global_dem_extent["min_x"]
        offset_y_native = y_native - global_dem_extent["min_y"]
        svg_x_m = offset_x_native * first_conversion_factor
        svg_y_m = offset_y_native * first_conversion_factor
        svg_y_m = svg_params["viewbox_height"] - svg_y_m 
        return svg_x_m, svg_y_m

    print("\n--- TEST: Check origin and extreme points of the COMBINED area ---")
    
    test_bl_lon, test_bl_lat = transformer_to_osm.transform(global_dem_extent["min_x"], global_dem_extent["min_y"])
    svg_x_bl, svg_y_bl = wgs84_to_svg_coords(test_bl_lon, test_bl_lat)
    print(f"Bottom-Left -> SVG ({svg_x_bl:.3f}, {svg_y_bl:.3f}) (Should be ~0, Max Height)")
    
    test_tl_lon, test_tl_lat = transformer_to_osm.transform(global_dem_extent["min_x"], global_dem_extent["max_y"])
    svg_x_tl, svg_y_tl = wgs84_to_svg_coords(test_tl_lon, test_tl_lat)
    print(f"Top-Left -> SVG ({svg_x_tl:.3f}, {svg_y_tl:.3f}) (Should be ~0, 0)")
    
    test_tr_lon, test_tr_lat = transformer_to_osm.transform(global_dem_extent["max_x"], global_dem_extent["max_y"])
    svg_x_tr, svg_y_tr = wgs84_to_svg_coords(test_tr_lon, test_tr_lat)
    print(f"Top-Right -> SVG ({svg_x_tr:.3f}, {svg_y_tr:.3f}) (Should be Max Width, ~0)")
