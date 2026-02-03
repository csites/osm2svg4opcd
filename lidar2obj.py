#!/usr/bin/env python3
""" lidar2obj.py Reads the input file (a geotiff export from QGIS of your Inner/Outer lidar/DEM layers.  We use map.osm to get the coordinates of the area of our out.svg, shift and scale to match the svg coordinates.   We then create the terrain.obj file for blender perfectly alligned to the svg.
"""
import numpy as np
import rasterio
from rasterio.warp import transform as row_transform
import xml.etree.ElementTree as ET
import argparse
import sys
import math
from scipy.ndimage import gaussian_filter

# --- Constants from osm2svg_v9 ---
METERS_PER_DEGREE_LAT = 111320
MM_PER_METER = 1000
METER_LENGTH = 1.0
SVG_MM_EQUIVALENCE = MM_PER_METER * METER_LENGTH

# Globals to be synced via map.osm
MIN_LAT, MAX_LAT, MIN_LON, MAX_LON = 0, 0, 0, 0
METERS_PER_DEGREE_LON_FACTOR = 0
METERS_PER_DEGREE_LAT_FACTOR = 0
REAL_TO_SVG_SCALE = 0

def get_bounds_from_osm(osm_file):
    """Parses map.osm to find the master bounding box."""
    try:
        tree = ET.parse(osm_file)
        root = tree.getroot()
        bounds = root.find('bounds')
        if bounds is not None:
            return {
                'minlat': float(bounds.get('minlat')),
                'maxlat': float(bounds.get('maxlat')),
                'minlon': float(bounds.get('minlon')),
                'maxlon': float(bounds.get('maxlon'))
            }
    except Exception as e:
        print(f"Error reading bounds from {osm_file}: {e}")
        sys.exit(1)

        
def calculate_and_set_projection(bounds):
    """Syncs the projection math to match osm2svg_v9 exactly."""
    global MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, REAL_TO_SVG_SCALE
    global METERS_PER_DEGREE_LON_FACTOR, METERS_PER_DEGREE_LAT_FACTOR

    MIN_LAT, MAX_LAT = bounds['minlat'], bounds['maxlat']
    MIN_LON, MAX_LON = bounds['minlon'], bounds['maxlon']

    avg_lat_rad = (MIN_LAT + MAX_LAT) / 2.0 * (math.pi / 180.0)
    METERS_PER_DEGREE_LON_FACTOR = METERS_PER_DEGREE_LAT * math.cos(avg_lat_rad)
    METERS_PER_DEGREE_LAT_FACTOR = METERS_PER_DEGREE_LAT
    REAL_TO_SVG_SCALE = MM_PER_METER / SVG_MM_EQUIVALENCE


def heightmap_to_obj(input_path, output_path, scale_z, sigma=3.0):
    try:
        with rasterio.open(input_path) as dataset:
            # Read as Float32 to prevent "staircase" rounding
            data = dataset.read(1).astype(np.float32)
            if sigma > 0:
                data = gaussian_filter(data, sigma=sigma)
                
            rows, cols = data.shape
            
            # 1. Grid Setup
            c_indices, r_indices = np.meshgrid(np.arange(cols), np.arange(rows))
            raw_xs, raw_ys = rasterio.transform.xy(dataset.transform, r_indices, c_indices)
            
            # 2. Convert to Lat/Lon
            lons, lats = row_transform(dataset.crs, 'EPSG:4326', 
                                     np.array(raw_xs).flatten(), 
                                     np.array(raw_ys).flatten())
            lons = np.array(lons).reshape(rows, cols)
            lats = np.array(lats).reshape(rows, cols)

            # 3. Precision Elevation Grounding
            # Replace NoData with the minimum to avoid "pits" to the center of the earth
            nodata = dataset.nodata
            valid_mask = (data != nodata) if nodata is not None else np.isfinite(data)
            min_z = np.min(data[valid_mask])
            
            with open(output_path, 'w') as f:
                f.write(f"# Seneca Terrain: Precision Aligned\n")

                for r in range(rows):
                    for c in range(cols):
                        lon, lat = lons[r, c], lats[r, c]
                        
                        # X: Easting from Western edge
                        x_m = (MIN_LON - lon) * METERS_PER_DEGREE_LON_FACTOR
                        # Y: Northing from Southern edge (Matches Blender Y-Up)
                        y_m = (lat - MIN_LAT) * METERS_PER_DEGREE_LAT_FACTOR
                        
                        # Final Scaling
                        x = x_m * REAL_TO_SVG_SCALE
                        y = y_m * REAL_TO_SVG_SCALE
                        # Subtract min_z and scale - use float precision
                        z = (float(data[r, c]) - float(min_z)) * scale_z
                        
                        # UNITY STYLE HANDSHAKE:
                        # If Unity is negative X, we may need to flip the X sign 
                        # to match your specific SVG import orientation.
                        # For now, we stay positive to match the SVG's 0,0 origin.
                        f.write(f"v {x:.6f} {z:.6f} {y:.6f}\n")

                # 4. Faces (Unchanged)
                for r in range(rows - 1):
                    for c in range(cols - 1):
                        v1 = r * cols + c + 1
                        v2 = r * cols + (c + 1) + 1
                        v3 = (r + 1) * cols + (c + 1) + 1
                        v4 = (r + 1) * cols + c + 1
                        f.write(f"f {v1} {v2} {v3} {v4}\n")

        print(f"✅ Success! Mesh grounded at Z=0 with float precision.")
        
    except Exception as e:
        print(f"Error: {e}")

        

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--infile", required=True, help="Input GeoTIFF")
    parser.add_argument("--outfile", default="terrain.obj", help="Output OBJ")
    parser.add_argument("--mapfile", default="map.osm", help="OSM file for bounds")
    parser.add_argument("--scale_z", type=float, default=1.0, help="Vertical scale")
    parser.add_argument("--sigma", type=float, default=3.0, help="Gaussian image smoothing factor 0-5")
    args = parser.parse_args()

    bounds = get_bounds_from_osm(args.mapfile)
    calculate_and_set_projection(bounds)
    heightmap_to_obj(args.infile, args.outfile, args.scale_z, args.sigma)
