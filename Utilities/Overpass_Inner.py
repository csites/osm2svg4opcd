#!/usr/bin/env python3

import requests
import json
import sys
import time
import math

# Base URL for the Overpass API
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OUTPUT_FILE = "map.osm"
STYLES_FILE = "styles.json"

# --- Area Calculation Constants ---
EARTH_RADIUS_KM = 6371.0
# We are fixing the box to 2km x 2km (4 km2), which is well within your 8.0 limit.
SQUARE_SIDE_M = 2000.0 
HALF_SIDE_M = SQUARE_SIDE_M / 2.0

def degrees_to_radians(degrees):
    return degrees * math.pi / 180.0

def load_styles(filename):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading styles: {e}")
        sys.exit(1)

def get_overpass_filters(styles_map):
    filters = []
    for style_key in styles_map.keys():
        if style_key == "Comment": continue
        if '.' in style_key:
            key, value = style_key.split('.', 1)
            filters.append(f'["{key}"="{value}"]')
        else:
            filters.append(f'["{style_key}"]')
    return sorted(list(set(filters)))

def build_overpass_query(styles, bbox_coords):
    bbox_str = ",".join(map(str, bbox_coords))
    tag_filters = get_overpass_filters(styles)
    
    query_parts = ["[out:xml][timeout:180];\n(\n"]
    for tag_filter in tag_filters:
        query_parts.append(f"  node{tag_filter}({bbox_str});\n")
        query_parts.append(f"  way{tag_filter}({bbox_str});\n")
        query_parts.append(f"  relation{tag_filter}({bbox_str});\n")
    
    query_parts.append(");\n(._;>;);\nout geom;")
    return "".join(query_parts)

def fetch_overpass_data(query, max_retries=5):
    print("Executing Overpass QL query...")
    for attempt in range(max_retries):
        try:
            response = requests.post(OVERPASS_URL, data=query.encode('utf-8'))
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            delay = 2 ** attempt
            print(f"Error {e}. Retrying in {delay}s...")
            time.sleep(delay)
    return None

def main():
    if len(sys.argv) != 3:
        print("Usage: python Overpass_downloader.py <center_lat> <center_lon>")
        print("Example: python Overpass_downloader.py 48.8584 2.2945")
        sys.exit(1)

    try:
        center_lat = float(sys.argv[1])
        center_lon = float(sys.argv[2])
    except ValueError:
        print("Error: Coordinates must be numeric.")
        sys.exit(1)

    # --- Calculate Bounding Box for 2000m x 2000m Square ---
    # Offset for Latitude (1 degree is approx 111,111 meters)
    lat_offset = HALF_SIDE_M / 111111.0
    
    # Offset for Longitude (Adjusted for Earth's curvature at this latitude)
    # Formula: 111,111 * cos(latitude)
    lon_offset = HALF_SIDE_M / (111111.0 * math.cos(math.radians(center_lat)))

    min_lat = center_lat - lat_offset
    max_lat = center_lat + lat_offset
    min_lon = center_lon - lon_offset
    max_lon = center_lon + lon_offset

    bbox_coords = (min_lat, min_lon, max_lat, max_lon)
    
    print(f"Center: {center_lat}, {center_lon}")
    print(f"Calculated 2km x 2km Box: {bbox_coords}")

    styles = load_styles(STYLES_FILE)
    query = build_overpass_query(styles, bbox_coords)
    osm_data = fetch_overpass_data(query)

    if osm_data:
        # Inject <bounds> tag for compatibility with your osm2svg script
        bounds_tag = f'<bounds minlat="{min_lat}" minlon="{min_lon}" maxlat="{max_lat}" maxlon="{max_lon}"/>'
        
        osm_tag_end = osm_data.find('>', osm_data.find('<osm'))
        newline_index = osm_data.find('\n', osm_tag_end)
        
        if newline_index != -1:
            inserted_data = osm_data[:newline_index+1] + bounds_tag + "\n" + osm_data[newline_index+1:]
        else:
            inserted_data = osm_data[:osm_tag_end+1] + bounds_tag + osm_data[osm_tag_end+1:]

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(inserted_data)
        print(f"\n✅ Data written to {OUTPUT_FILE}")
    else:
        print("\n❌ Failed to generate map file.")

if __name__ == "__main__":
    main()
