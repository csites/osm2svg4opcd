import pyproj
import math
import argparse
import numpy as np
import sys
import os

# Try importing necessary libraries and handle missing dependencies
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


# --- TARGET SCALING CONSTANT ---
# We want 1 unit in the final SVG coordinate space to equal 1 meter on the ground.
# 1 meter = 3.28084 feet
METERS_PER_FOOT = 1 / 3.28084

# The target CRS for OpenStreetMap (WGS 84 Lat/Lon)
OSM_CRS = "EPSG:4326"


def get_geotiff_metadata(file_path):
    """Reads spatial extent, CRS, and unit conversion factor from a GeoTIFF."""
    try:
        with rasterio.open(file_path) as src:
            # 1. Get Extent in native units (feet/meters)
            bounds = src.bounds
            dem_extent_native = {
                "min_x": bounds.left,
                "max_x": bounds.right,
                "min_y": bounds.bottom,
                "max_y": bounds.top,
            }

            # 2. Get CRS and convert to EPSG code string
            # Check if CRS is defined (sometimes it's missing)
            if src.crs and src.crs.to_epsg():
                dem_crs = f"EPSG:{src.crs.to_epsg()}"
            else:
                print(f"ERROR: GeoTIFF {file_path} is missing CRS information.")
                return None, None, None 

            # 3. Determine conversion factor
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

            # CRS extraction from LAZ relies on Well-Known Text (WKT) in VLRs, 
            # which is often complex to fully parse for the EPSG code without 
            # external tools or explicit user input.
            
            # 1. Get CRS string (Best Effort)
            if las_file.vlrs:
                # This attempts to read WKT from VLRs, which is the most reliable way
                try:
                    wkt = las_file.header.vlrs.get_wkt()
                    # Use pyproj to try and find the EPSG code from WKT
                    crs = pyproj.CRS.from_wkt(wkt)
                    dem_crs = f"EPSG:{crs.to_epsg()}"
                    print(f"INFO: Successfully extracted CRS from LAZ WKT: {dem_crs}")
                except Exception:
                    dem_crs = None
            else:
                dem_crs = None

            if dem_crs is None:
                # If WKT extraction failed, we require the user to provide the EPSG code 
                # via command line argument, as LAZ metadata is highly variable.
                print("WARNING: Could not automatically detect CRS from LAZ header (VLRs).")
                # We will rely on the user-provided CRS argument (handled in main function).
                dem_crs = "USER_REQUIRED" # Placeholder to flag user input needed
                
            # 2. Determine conversion factor (based on coordinate magnitude)
            x_diff = header.max_x - header.min_x
            if x_diff > 100000: # Typical large coordinate values indicate feet (e.g., State Plane)
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
    """
    Unified function to dispatch to the correct reader based on file extension.
    """
    ext = file_path.lower().split('.')[-1]
    
    if ext in ['tif', 'tiff']:
        return get_geotiff_metadata(file_path)
    elif ext in ['las', 'laz']:
        return get_laz_metadata(file_path)
    else:
        print(f"ERROR: Unsupported file extension '{ext}'. Must be .tif, .tiff, .las, or .laz.")
        return None, None, None


def calculate_osm_bounds_for_plate(dem_extent, transformer_to_osm):
    """
    Transforms the four corners of a single DEM extent from the native DEM_CRS 
    to EPSG:4326 (WGS 84 Lat/Lon) to find the WGS 84 bounding box for this plate.
    """
    min_x, max_x = dem_extent["min_x"], dem_extent["max_x"]
    min_y, max_y = dem_extent["min_y"], dem_extent["max_y"]

    # Transform the four corners to Lat/Lon (WGS 84)
    corners = [
        (min_x, max_y),  # Top Left
        (max_x, max_y),  # Top Right
        (min_x, min_y),  # Bottom Left
        (max_x, min_y),  # Bottom Right
    ]

    lons, lats = zip(*[transformer_to_osm.transform(x, y) for x, y in corners])

    return {
        "top": max(lats),
        "bottom": min(lats),
        "left": min(lons),
        "right": max(lons)
    }

def calculate_final_svg_parameters(global_dem_extent, dem_crs, conversion_factor):
    """
    Calculates the required SVG viewBox parameters and transformation function 
    based on the *combined* global DEM extent.
    """
    width_native = global_dem_extent["max_x"] - global_dem_extent["min_x"]
    height_native = global_dem_extent["max_y"] - global_dem_extent["min_y"]

    # Convert dimensions from native units (feet/etc.) to meters
    width_m = width_native * conversion_factor
    height_m = height_native * conversion_factor
    
    # SVG ViewBox dimensions are in meters (1 unit = 1 meter)
    viewbox_width = math.ceil(width_m)
    viewbox_height = math.ceil(height_m)

    # Create the final transformation chain: WGS 84 -> DEM_CRS
    wgs84_to_native = pyproj.Transformer.from_crs(OSM_CRS, dem_crs, always_xy=True)

    print("--- FINAL SVG SCALING PARAMETERS ---")
    print(f"DEM CRS (Used for Scaling): {dem_crs}")
    print(f"Combined DEM Width (Native Units): {width_native:.2f}")
    print(f"Combined DEM Height (Native Units): {height_native:.2f}")
    print(f"SVG ViewBox Width (meters): {viewbox_width}")
    print(f"SVG ViewBox Height (meters): {viewbox_height}")
    print(f"Conversion Factor (Meters/Native Unit): {conversion_factor:.6f}")
    print("-" * 50)


    def wgs84_to_svg_coords(lon, lat):
        """
        Transforms (Lon, Lat) from OSM into SVG (X, Y) coordinates (in meters) 
        relative to the global DEM's bottom-left corner (min_x, min_y).
        """
        # 1. Transform WGS 84 to DEM_CRS (native units)
        x_native, y_native = wgs84_to_native.transform(lon, lat)
        
        # 2. Calculate offset from the GLOBAL DEM's min_x, min_y
        offset_x_native = x_native - global_dem_extent["min_x"]
        offset_y_native = y_native - global_dem_extent["min_y"]
        
        # 3. Scale offset from native units to meters (1 unit = 1 meter)
        svg_x_m = offset_x_native * conversion_factor
        svg_y_m = offset_y_native * conversion_factor
        
        # 4. Invert Y-axis (DEM is Y-up, SVG is Y-down)
        svg_y_m = viewbox_height - svg_y_m 

        return svg_x_m, svg_y_m

    return {
        "viewbox_width": viewbox_width,
        "viewbox_height": viewbox_height,
        "wgs84_to_svg_func": wgs84_to_svg_coords
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calculate OSM WGS 84 bounds and SVG scaling parameters "
                    "by reading the metadata of one or more GeoTIFF or LAZ/LAS heightmaps.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    # Use nargs='+' to accept one or more files
    parser.add_argument(
        "data_files", 
        nargs='+',
        help="One or more paths to GeoTIFF (.tif) or LAZ/LAS (.laz, .las) files."
    )
    parser.add_argument(
        "--crs",
        help="The EPSG code of the native CRS, e.g., EPSG:3089. Mandatory if any LAZ/LAS file is used and its CRS cannot be inferred."
    )
    args = parser.parse_args()
    
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
        
        # 1. Read metadata from the file (GeoTIFF or LAZ)
        dem_crs, dem_extent_native, conversion_factor = get_file_metadata(data_file)
        
        if dem_crs is None:
            continue
            
        # If LAZ failed to get the CRS, use the user-provided one
        if dem_crs == "USER_REQUIRED":
            if args.crs:
                dem_crs = args.crs
            else:
                print(f"FATAL ERROR: LAZ file {data_file} requires the native CRS (e.g., EPSG:3089) to be specified using the '--crs' argument.")
                sys.exit(1)

        # Enforce consistency check (all plates must use the same CRS)
        if first_dem_crs is None:
            first_dem_crs = dem_crs
            first_conversion_factor = conversion_factor
            # Initialize the WGS 84 transformers based on the first plate's CRS
            try:
                transformer_to_osm = pyproj.Transformer.from_crs(dem_crs, OSM_CRS, always_xy=True)
            except Exception as e:
                print(f"FATAL ERROR: Invalid CRS '{dem_crs}' provided or detected. Check the EPSG code. Details: {e}")
                sys.exit(1)
            
        elif dem_crs != first_dem_crs:
            print(f"FATAL ERROR: Plate {os.path.basename(data_file)} uses CRS {dem_crs}, but the first plate used {first_dem_crs}. All plates must share the same CRS for combined processing.")
            sys.exit(1)
        
        if conversion_factor != first_conversion_factor:
            print("WARNING: Conversion factor differs between plates. Using the first plate's factor.")

        # 2. Update the global Native CRS extent (for SVG scaling)
        global_min_x = min(global_min_x, dem_extent_native["min_x"])
        global_max_x = max(global_max_x, dem_extent_native["max_x"])
        global_min_y = min(global_min_y, dem_extent_native["min_y"])
        global_max_y = max(global_max_y, dem_extent_native["max_y"])

        # 3. Calculate the WGS 84 bounds for this plate (for OSM export bounding box)
        plate_osm_bounds = calculate_osm_bounds_for_plate(dem_extent_native, transformer_to_osm)

        # 4. Update the global WGS 84 extent
        global_min_lon = min(global_min_lon, plate_osm_bounds["left"])
        global_max_lon = max(global_max_lon, plate_osm_bounds["right"])
        global_min_lat = min(global_min_lat, plate_osm_bounds["bottom"])
        global_max_lat = max(global_max_lat, plate_osm_bounds["top"])
        
    if first_dem_crs is None:
        print("\nERROR: No valid data files were processed.")
        sys.exit(1)

    # Final aggregated extent in Native CRS
    global_dem_extent = {
        "min_x": global_min_x,
        "max_x": global_max_x,
        "min_y": global_min_y,
        "max_y": global_max_y,
    }

    # 5. Calculate final SVG parameters using the combined extent
    svg_params = calculate_final_svg_parameters(global_dem_extent, first_dem_crs, first_conversion_factor)
    
    print("--- FINAL AGGREGATED OSM WGS 84 BOUNDARIES (FOR OSM EXPORT) ---")
    print(f"Top Latitude:    {global_max_lat:.6f}")
    print(f"Bottom Latitude: {global_min_lat:.6f}")
    print(f"Left Longitude:  {global_min_lon:.6f}")
    print(f"Right Longitude: {global_max_lon:.6f}")
    print("-" * 50)
    print("\nUse the above WGS 84 boundaries to download your OpenStreetMap data.")
    print(f"Recommended SVG ViewBox: 0 0 {svg_params['viewbox_width']} {svg_params['viewbox_height']}")
    
    # --- TEST OUTPUT (using the global extent) ---
    print("\n--- TEST: Check origin and extreme points of the COMBINED area ---")
    
    # Test 1: Bottom-Left (Min X, Min Y) should map to SVG (0, Max Height)
    test_bl_lon, test_bl_lat = transformer_to_osm.transform(global_dem_extent["min_x"], global_dem_extent["min_y"])
    svg_x_bl, svg_y_bl = svg_params["wgs84_to_svg_func"](test_bl_lon, test_bl_lat)
    print(f"Bottom-Left -> SVG ({svg_x_bl:.3f}, {svg_y_bl:.3f}) (Should be ~0, Max Height)")
    
    # Test 2: Top-Left (Min X, Max Y) should map to SVG (0, 0)
    test_tl_lon, test_tl_lat = transformer_to_osm.transform(global_dem_extent["min_x"], global_dem_extent["max_y"])
    svg_x_tl, svg_y_tl = svg_params["wgs84_to_svg_func"](test_tl_lon, test_tl_lat)
    print(f"Top-Left -> SVG ({svg_x_tl:.3f}, {svg_y_tl:.3f}) (Should be ~0, 0)")
    
    # Test 3: Top-Right (Max X, Max Y) should map to SVG (Max Width, 0)
    test_tr_lon, test_tr_lat = transformer_to_osm.transform(global_dem_extent["max_x"], global_dem_extent["max_y"])
    svg_x_tr, svg_y_tr = svg_params["wgs84_to_svg_func"](test_tr_lon, test_tr_lat)
    print(f"Top-Right -> SVG ({svg_x_tr:.3f}, {svg_y_tr:.3f}) (Should be Max Width, ~0)")
