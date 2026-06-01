import xml.etree.ElementTree as ET
import numpy as np
import os
import math

def get_course_center(osm_file="map.osm"):
    if not os.path.exists(osm_file):
        print(f"❌ Error: {osm_file} not found in current directory.")
        return

    tree = ET.parse(osm_file)
    root = tree.getroot()

    # 1. Dictionary to store all nodes for quick lookup
    nodes = {node.get('id'): (float(node.get('lat')), float(node.get('lon'))) 
             for node in root.findall('node')}

    # 2. Find the way(s) with leisure=golf_course
    target_way = None
    for way in root.findall('way'):
        is_golf = False
        for tag in way.findall('tag'):
            if tag.get('k') == 'leisure' and tag.get('v') == 'golf_course':
                is_golf = True
                break
        if is_golf:
            target_way = way
            break

    if target_way is None:
        print("⚠️ Could not find a 'way' with tag leisure=golf_course.")
        return

    # 3. Extract polygon coordinates
    way_nodes = [nd.get('ref') for nd in target_way.findall('nd')]
    coords = []
    for node_id in way_nodes:
        if node_id in nodes:
            coords.append(nodes[node_id])

    if not coords:
        print("❌ Error: Golf course way found but contains no valid nodes.")
        return

    # 4. Calculate Center of Mass (Average of all points in the polygon)
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    
    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)

    # 5. Calculate 2km Bounds (Approximation for warning)
    # 1 degree lat is ~111,320m
    lat_buffer = 1000 / 111320
    # 1 degree lon is lat dependent
    lon_buffer = 1000 / (111320 * math.cos(math.radians(center_lat)))

    min_lat, max_lat = center_lat - lat_buffer, center_lat + lat_buffer
    min_lon, max_lon = center_lon - lon_buffer, center_lon + lon_buffer

    # 6. Check if course exceeds 2km zone
    course_min_lat, course_max_lat = min(lats), max(lats)
    course_min_lon, course_max_lon = min(lons), max(lons)

    print("-" * 50)
    print(f"⛳ GOLF COURSE FOUND: {osm_file}")
    print(f"📍 Center Lat: {center_lat:.7f}")
    print(f"📍 Center Lon: {center_lon:.7f}")
    print("-" * 50)
    print(f"📐 2km Square Corners (EPSG:4326):")
    print(f"   Top-Left:     {max_lat:.7f}, {min_lon:.7f}")
    print(f"   Bottom-Right: {min_lat:.7f}, {max_lon:.7f}")
    print("-" * 50)

    if (course_min_lat < min_lat or course_max_lat > max_lat or 
        course_min_lon < min_lon or course_max_lon > max_lon):
        print("‼️  WARNING: The course boundary extends OUTSIDE the 2km zone.")
        print("   You may need to increase size to 3km in clippy.py.")
    else:
        print("✅ Success: The course fits within the 2km zone.")
    print("-" * 50)

if __name__ == "__main__":
    get_course_center()
