#!/usr/bin/env python3

import requests
import json
import sys
import time
import math
import argparse

# Base URL for the Overpass API
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
STYLES_FILE = "styles.json"

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
    parser = argparse.ArgumentParser(description="Download OSM data for a square/rectangular area around a center point.")
    
    # Positional arguments
    parser.add_argument("lat", type=float, help="Center latitude")
    parser.add_argument("lon", type=float, help="Center longitude")
    
    # Optional arguments
    parser.add_argument("--output_file", "-o", default="map.osm", help="Path and name of output file (default: map.osm)")
    parser.add_argument("--distance_m_h", type=float, help="Height of the search area in meters")
    parser.add_argument("--distance_m_w", type=float, help="Width of the search area in meters")
    parser.add_argument("--distance_mwxh", type=float, default=2000.0, help="Square side length in meters (default: 2000)")

    args = parser.parse_args()

    # Determine final dimensions
    # If specific H or W are provided, they override the square default
    width_m = args.distance_m_w if args.distance_m_w else args.distance_mwxh
    height_m = args.distance_m_h if args.distance_m_h else args.distance_mwxh

    # --- Calculate Bounding Box ---
    # Latitude offset (constant: ~111,111m per degree)
    lat_offset = (height_m / 2.0) / 111111.0
    
    # Longitude offset (depends on latitude: 111,111 * cos(lat))
    lon_dist_per_degree = 111111.0 * math.cos(math.radians(args.lat))
    lon_offset = (width_m / 2.0) / lon_dist_per_degree

    min_lat, max_lat = args.lat - lat_offset, args.lat + lat_offset
    min_lon, max_lon = args.lon - lon_offset, args.lon + lon_offset

    bbox_coords = (min_lat, min_lon, max_lat, max_lon)
    
    print(f"Target: {args.lat}, {args.lon}")
    print(f"Dimensions: {width_m}m (W) x {height_m}m (H)")
    print(f"BBox: {bbox_coords}")

    styles = load_styles(STYLES_FILE)
    query = build_overpass_query(styles, bbox_coords)
    osm_data = fetch_overpass_data(query)

    if osm_data:
        # Inject <bounds> tag
        bounds_tag = f'<bounds minlat="{min_lat}" minlon="{min_lon}" maxlat="{max_lat}" maxlon="{max_lon}"/>'
        osm_tag_end = osm_data.find('>', osm_data.find('<osm'))
        newline_index = osm_data.find('\n', osm_tag_end)
        
        if newline_index != -1:
            inserted_data = osm_data[:newline_index+1] + bounds_tag + "\n" + osm_data[newline_index+1:]
        else:
            inserted_data = osm_data[:osm_tag_end+1] + bounds_tag + osm_data[osm_tag_end+1:]

        with open(args.output_file, 'w', encoding='utf-8') as f:
            f.write(inserted_data)
        print(f"\n✅ Data written to {args.output_file}")
    else:
        print("\n❌ Failed to generate map file.")

if __name__ == "__main__":
    main()
    
