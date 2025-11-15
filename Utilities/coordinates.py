#/usr/bin/env python3

import pyproj
import math
import argparse

"""
You can use this program to get the Coordinates for OpenStreetmap that match
the plate area of the height map.   DEM in my case.   You just need to know the extent
"""


# --- INPUT DEM PROPERTIES (FROM QGIS) ---
# The CRS of the DEM data (NAD83 / Kentucky Single Zone (ftUS))
DEM_CRS = "EPSG:3089" 

# The target CRS for OpenStreetMap (WGS 84 Lat/Lon)
OSM_CRS = "EPSG:4326"

# Extents of the DEM in feet (from QGIS "Extent" property)
# (Min X, Min Y) and (Max X, Max Y)
# If you source image for QGIS is in geotif  EPSG:3089 form the look at it's 'Propertes' to find these.
DEM_EXTENT_FEET = {
    "min_x": 4940000.0,
    "max_x": 4945000.0,
    "min_y": 3980000.0,
    "max_y": 3985000.0,
}

# --- TARGET SCALING ---
# We want 1 meter to equal 1 unit in the final SVG coordinate space.
# Since the input DEM is in feet, we need the conversion factor.
# 1 meter = 3.28084 feet
METERS_PER_FOOT = 1 / 3.28084

# --- CORE TRANSFORMATION LOGIC ---

# 1. Initialize the transformer from DEM_CRS (feet) to OSM_CRS (Lat/Lon)
transformer_to_osm = pyproj.Transformer.from_crs(DEM_CRS, OSM_CRS, always_xy=True)
# 2. Initialize the transformer from DEM_CRS (feet) to EPSG:3857 (meters)
# We use 3857 (Web Mercator) just as an intermediate for calculating meter distance, 
# but the transformation uses 3089 as the source.
transformer_to_meters = pyproj.Transformer.from_crs(DEM_CRS, "EPSG:3857", always_xy=True)


def calculate_osm_bounds(dem_extent):
    """
    Transforms the four corners of the DEM extent from EPSG:3089 (feet) 
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

def calculate_svg_parameters(dem_extent):
    """
    Calculates the required SVG viewBox parameters based on the DEM extent in meters.
    1 unit in SVG will equal 1 meter on the ground.
    """
    width_ft = dem_extent["max_x"] - dem_extent["min_x"]
    height_ft = dem_extent["max_y"] - dem_extent["min_y"]

    # Convert dimensions from feet to meters
    width_m = width_ft * METERS_PER_FOOT
    height_m = height_ft * METERS_PER_FOOT
    
    # SVG ViewBox dimensions are in meters (1 unit = 1 meter)
    viewbox_width = math.ceil(width_m)
    viewbox_height = math.ceil(height_m)

    # We want the OSM coordinates to align with a new origin (0, 0) in the SVG.
    # The new origin (0, 0) corresponds to the bottom-left corner of the DEM extent 
    # (min_x, min_y).
    
    # Since we are setting up a projection from Lat/Lon to a custom 
    # meter-based (0, 0) origin, we need the meter offset.
    
    print("--- SVG SCALING PARAMETERS ---")
    print(f"DEM Width (feet): {width_ft:.2f}")
    print(f"DEM Height (feet): {height_ft:.2f}")
    print(f"SVG ViewBox Width (meters): {viewbox_width}")
    print(f"SVG ViewBox Height (meters): {viewbox_height}")
    print(f"Scaling Factor (Meters/Foot): {METERS_PER_FOOT:.6f}")
    print("-" * 50)

    # The transformation function needed in osm2svg_v5.py
    # This will transform a WGS 84 (Lon, Lat) point into SVG (X, Y) meter coordinates.
    # The SVG X/Y should be relative to the DEM's bottom-left corner.
    
    # Create the final transformation chain: WGS 84 -> EPSG:3089
    # The result (x_ft, y_ft) will be in feet relative to the global origin of EPSG:3089
    wgs84_to_ft = pyproj.Transformer.from_crs(OSM_CRS, DEM_CRS, always_xy=True)

    def wgs84_to_svg_coords(lon, lat):
        """
        Transforms (Lon, Lat) from OSM into SVG (X, Y) coordinates (in meters) 
        relative to the DEM's bottom-left corner (min_x, min_y).
        """
        # 1. Transform WGS 84 to EPSG:3089 (feet)
        x_ft, y_ft = wgs84_to_ft.transform(lon, lat)
        
        # 2. Calculate offset from the DEM's min_x, min_y
        offset_x_ft = x_ft - dem_extent["min_x"]
        offset_y_ft = y_ft - dem_extent["min_y"]
        
        # 3. Scale offset from feet to meters (1 unit = 1 meter)
        svg_x_m = offset_x_ft * METERS_PER_FOOT
        svg_y_m = offset_y_ft * METERS_PER_FOOT
        
        # SVG uses Y-down, but EPSG:3089 is Y-up. We must invert Y.
        # Max Y in the DEM's bounding box corresponds to Y=0 in the SVG.
        # This gives us the correct Y-down coordinate system for SVG.
        svg_y_m = viewbox_height - svg_y_m 

        return svg_x_m, svg_y_m

    return {
        "viewbox_width": viewbox_width,
        "viewbox_height": viewbox_height,
        "wgs84_to_svg_func": wgs84_to_svg_coords
    }

if __name__ == "__main__":
    osm_bounds = calculate_osm_bounds(DEM_EXTENT_FEET)
    svg_params = calculate_svg_parameters(DEM_EXTENT_FEET)
    
    print("\n--- TEST: Check origin and extreme points ---")
    
    # Test 1: Bottom-Left (Min X, Min Y) should map to SVG (0, Max Height)
    test_bl_lon, test_bl_lat = transformer_to_osm.transform(DEM_EXTENT_FEET["min_x"], DEM_EXTENT_FEET["min_y"])
    svg_x_bl, svg_y_bl = svg_params["wgs84_to_svg_func"](test_bl_lon, test_bl_lat)
    print(f"Bottom-Left (Lon, Lat): ({test_bl_lon:.6f}, {test_bl_lat:.6f}) -> SVG ({svg_x_bl:.3f}, {svg_y_bl:.3f})")
    
    # Test 2: Top-Left (Min X, Max Y) should map to SVG (0, 0)
    test_tl_lon, test_tl_lat = transformer_to_osm.transform(DEM_EXTENT_FEET["min_x"], DEM_EXTENT_FEET["max_y"])
    svg_x_tl, svg_y_tl = svg_params["wgs84_to_svg_func"](test_tl_lon, test_tl_lat)
    print(f"Top-Left (Lon, Lat): ({test_tl_lon:.6f}, {test_tl_lat:.6f}) -> SVG ({svg_x_tl:.3f}, {svg_y_tl:.3f})")
    
    # Test 3: Top-Right (Max X, Max Y) should map to SVG (Max Width, 0)
    test_tr_lon, test_tr_lat = transformer_to_osm.transform(DEM_EXTENT_FEET["max_x"], DEM_EXTENT_FEET["max_y"])
    svg_x_tr, svg_y_tr = svg_params["wgs84_to_svg_func"](test_tr_lon, test_tr_lat)
    print(f"Top-Right (Lon, Lat): ({test_tr_lon:.6f}, {test_tr_lat:.6f}) -> SVG ({svg_x_tr:.3f}, {svg_y_tr:.3f})")

    print("\n[The SVG ViewBox will be 5000 units wide by 5000 units high (since 5000 ft * 0.3048 m/ft is ~1524 m, and the test extent is 5000x5000 ft).]")
    print(f"Recommended SVG ViewBox: 0 0 {svg_params['viewbox_width']} {svg_params['viewbox_height']}")
    
    # This script must be run once to get the bounding box for your OSM download.


    
