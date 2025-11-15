#!/usr/bin/env python3
"""
This program reads a DEM (Elevation Map) in geotiff and reports the map coordinates
in WGS 84 coordinates for OpenStreetmap export function.  Additionally it reports scaling
factors to SVG.
"""
import pyproj
import math
import argparse
import rasterio

# --- TARGET SCALING CONSTANT ---
# We want 1 unit in the final SVG coordinate space to equal 1 meter on the ground.
# The input DEM is in US Survey Feet (for EPSG:3089), so we need the conversion factor.
# 1 meter = 3.28084 feet
METERS_PER_FOOT = 1 / 3.28084

# The target CRS for OpenStreetMap (WGS 84 Lat/Lon)
OSM_CRS = "EPSG:4326"

def get_dem_metadata(file_path):
    """
    Reads the spatial extent (bounding box) and CRS from the GeoTIFF file 
    using the rasterio library.
    """
    try:
        with rasterio.open(file_path) as src:
            # 1. Get Extent in native units (feet)
            bounds = src.bounds
            dem_extent_feet = {
                "min_x": bounds.left,
                "max_x": bounds.right,
                "min_y": bounds.bottom,
                "max_y": bounds.top,
            }

            # 2. Get CRS and convert to EPSG code string
            dem_crs = f"EPSG:{src.crs.to_epsg()}"
            
            # 3. Determine if the input units are feet (e.g., if CRS is 3089)
            if 'feet' in str(src.crs.linear_units).lower():
                 print(f"INFO: Detected units are {src.crs.linear_units}. Using {METERS_PER_FOOT:.6f} for ft-to-meter conversion.")
                 conversion_factor = METERS_PER_FOOT
            elif 'meter' in str(src.crs.linear_units).lower():
                 print("INFO: Detected units are meters. Conversion factor is 1.0.")
                 conversion_factor = 1.0
            else:
                 print("WARNING: Could not reliably determine unit type. Assuming feet.")
                 conversion_factor = METERS_PER_FOOT

            return dem_crs, dem_extent_feet, conversion_factor
            
    except rasterio.RasterioIOError:
        print(f"ERROR: Could not open or read the GeoTIFF file at {file_path}")
        exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        exit(1)

# Initialize the transformer from DEM_CRS (feet) to OSM_CRS (Lat/Lon)
# Note: This is now initialized inside the main block once the CRS is known.
# transformer_to_osm = pyproj.Transformer.from_crs(DEM_CRS, OSM_CRS, always_xy=True)


def calculate_osm_bounds(dem_extent, dem_crs, transformer_to_osm):
    """
    Transforms the four corners of the DEM extent from the native DEM_CRS 
    to EPSG:4326 (WGS 84 Lat/Lon) for OSM export.
    """
    min_x, max_x = dem_extent["min_x"], dem_extent["max_x"]
    min_y, max_y = dem_extent["min_y"], dem_extent["max_y"]

    # Transform the four corners to Lat/Lon (WGS 84)
    # (Lon, Lat) format for pyproj output (always_xy=True)
    
    # Top Left (Max Y, Min X) -> OSM top, left
    lon_left, lat_top = transformer_to_osm.transform(min_x, max_y)
    
    # Bottom Right (Min Y, Max X) -> OSM bottom, right
    lon_right, lat_bottom = transformer_to_osm.transform(max_x, min_y)

    print("--- OSM WGS 84 BOUNDARIES (FOR OSM EXPORT) ---")
    print(f"Top Latitude:    {lat_top:.6f}")
    print(f"Bottom Latitude: {lat_bottom:.6f}")
    print(f"Left Longitude:  {lon_left:.6f}")
    print(f"Right Longitude: {lon_right:.6f}")
    print("-" * 50)
    
    return {
        "top": lat_top,
        "bottom": lat_bottom,
        "left": lon_left,
        "right": lon_right
    }

def calculate_svg_parameters(dem_extent, dem_crs, conversion_factor):
    """
    Calculates the required SVG viewBox parameters based on the DEM extent.
    1 unit in SVG will equal 1 meter on the ground.
    """
    width_native = dem_extent["max_x"] - dem_extent["min_x"]
    height_native = dem_extent["max_y"] - dem_extent["min_y"]

    # Convert dimensions from native units (feet) to meters
    width_m = width_native * conversion_factor
    height_m = height_native * conversion_factor
    
    # SVG ViewBox dimensions are in meters (1 unit = 1 meter)
    viewbox_width = math.ceil(width_m)
    viewbox_height = math.ceil(height_m)

    # We need a WGS 84 to SVG coordinate function. This requires the DEM's 
    # native CRS for intermediate projection.
    
    print("--- SVG SCALING PARAMETERS ---")
    print(f"DEM CRS: {dem_crs}")
    print(f"DEM Width (Native Units): {width_native:.2f}")
    print(f"DEM Height (Native Units): {height_native:.2f}")
    print(f"SVG ViewBox Width (meters): {viewbox_width}")
    print(f"SVG ViewBox Height (meters): {viewbox_height}")
    print(f"Conversion Factor (Meters/Native Unit): {conversion_factor:.6f}")
    print("-" * 50)

    # Create the final transformation chain: WGS 84 -> DEM_CRS
    wgs84_to_native = pyproj.Transformer.from_crs(OSM_CRS, dem_crs, always_xy=True)

    def wgs84_to_svg_coords(lon, lat):
        """
        Transforms (Lon, Lat) from OSM into SVG (X, Y) coordinates (in meters) 
        relative to the DEM's bottom-left corner (min_x, min_y).
        """
        # 1. Transform WGS 84 to DEM_CRS (native units)
        x_native, y_native = wgs84_to_native.transform(lon, lat)
        
        # 2. Calculate offset from the DEM's min_x, min_y
        offset_x_native = x_native - dem_extent["min_x"]
        offset_y_native = y_native - dem_extent["min_y"]
        
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
                    "by reading the metadata of a GeoTIFF heightmap.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "tif_file", 
        help="Path to the GeoTIFF (.tif) heightmap file (e.g., N076E233_2023_DEM_Phase2_cog.tif)"
    )
    args = parser.parse_args()
    
    # 1. Read metadata from the GeoTIFF
    dem_crs, dem_extent_feet, conversion_factor = get_dem_metadata(args.tif_file)
    
    # 2. Initialize the transformer from DEM_CRS (e.g., 3089) to OSM_CRS (4326)
    # This must be done after the DEM_CRS is read.
    transformer_to_osm = pyproj.Transformer.from_crs(dem_crs, OSM_CRS, always_xy=True)
    
    # 3. Calculate OSM bounds
    osm_bounds = calculate_osm_bounds(dem_extent_feet, dem_crs, transformer_to_osm)
    
    # 4. Calculate SVG parameters
    svg_params = calculate_svg_parameters(dem_extent_feet, dem_crs, conversion_factor)
    
    # --- TEST OUTPUT ---
    print("\n--- TEST: Check origin and extreme points ---")
    
    # Test 1: Bottom-Left (Min X, Min Y) should map to SVG (0, Max Height)
    # We must transform the corner of the DEM extent to WGS 84 first
    test_bl_lon, test_bl_lat = transformer_to_osm.transform(dem_extent_feet["min_x"], dem_extent_feet["min_y"])
    svg_x_bl, svg_y_bl = svg_params["wgs84_to_svg_func"](test_bl_lon, test_bl_lat)
    print(f"Bottom-Left -> SVG ({svg_x_bl:.3f}, {svg_y_bl:.3f}) (Should be ~0, Max Height)")
    
    # Test 2: Top-Left (Min X, Max Y) should map to SVG (0, 0)
    test_tl_lon, test_tl_lat = transformer_to_osm.transform(dem_extent_feet["min_x"], dem_extent_feet["max_y"])
    svg_x_tl, svg_y_tl = svg_params["wgs84_to_svg_func"](test_tl_lon, test_tl_lat)
    print(f"Top-Left -> SVG ({svg_x_tl:.3f}, {svg_y_tl:.3f}) (Should be ~0, 0)")
    
    # Test 3: Top-Right (Max X, Max Y) should map to SVG (Max Width, 0)
    test_tr_lon, test_tr_lat = transformer_to_osm.transform(dem_extent_feet["max_x"], dem_extent_feet["max_y"])
    svg_x_tr, svg_y_tr = svg_params["wgs84_to_svg_func"](test_tr_lon, test_tr_lat)
    print(f"Top-Right -> SVG ({svg_x_tr:.3f}, {svg_y_tr:.3f}) (Should be Max Width, ~0)")

    print(f"\nRecommended SVG ViewBox: 0 0 {svg_params['viewbox_width']} {svg_params['viewbox_height']}")
    print("\nThis script must be run once to get the bounding box for your OSM download.")
    
