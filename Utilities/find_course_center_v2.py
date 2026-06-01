#!/usr/bin/env python3
"""
find_course_center_v2.py is used to return the center of a golf course in longitude - latitude.    It find the center from the map.osm looking for the
osm tag leisure.golf_course.   From that it reads the boundries of the course and centers the boundry in a 2km x 2km square.   If the course extends out of that
it will warn us that we need to increase the inner size to 2.5km or 3km.   Otherwise it reports back the new center lon/lat.  That will be used to make the new
inner2k.tif, hillshade, and re-download the Inner/Outer Google, Bing Sat images based on the new coordinate.  Basically we need the center correct before we can
proceed with the inner/outer terrain.obj, so we need this coorection done early after the initial map.osm download.
"""

import xml.etree.ElementTree as ET
import os
import math

def get_course_center(osm_file="map.osm", inner_size=2000, outer_size=4000):
    if not os.path.exists(osm_file):
        print(f"❌ Error: {osm_file} not found.")
        return

    tree = ET.parse(osm_file)
    root = tree.getroot()

    nodes = {node.get('id'): (float(node.get('lat')), float(node.get('lon'))) 
            for node in root.findall('node')}

    coords = []
    # Check ways for the golf_course tag
    for way in root.findall('way'):
        if any(tag.get('k') == 'leisure' and tag.get('v') == 'golf_course' for tag in way.findall('tag')):
            for nd in way.findall('nd'):
                node_id = nd.get('ref')
                if node_id in nodes:
                    coords.append(nodes[node_id])

    if not coords:
        print("⚠ No 'leisure=golf_course' boundary found.")
        return

    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]

    # 1. Midpoint Center calculation
    c_min_lat, c_max_lat = min(lats), max(lats)
    c_min_lon, c_max_lon = min(lons), max(lons)
    center_lat = (c_min_lat + c_max_lat) / 2
    center_lon = (c_min_lon + c_max_lon) / 2

    def get_bbox(c_lat, c_lon, size_m):
        # 1 degree lat is ~111,320m
        lat_buf = (size_m / 2) / 111320
        # 1 degree lon is lat dependent
        lon_buf = (size_m / 2) / (111320 * math.cos(math.radians(c_lat)))
        return (c_lat - lat_buf, c_lon - lon_buf, c_lat + lat_buf, c_lon + lon_buf)

    inner_bbox = get_bbox(center_lat, center_lon, inner_size)
    outer_bbox = get_bbox(center_lat, center_lon, outer_size)

    print("-" * 65)
    print(f"⛳ PROJECT COORDINATES: {osm_file}")
    print(f"📍 Midpoint Center: {center_lat:.7f}, {center_lon:.7f}")
    print("-" * 65)
    
    print(f"🖼️  INNER BOX ({inner_size}m):")
    print(f"    NW Corner: {inner_bbox[2]:.7f}, {inner_bbox[1]:.7f}")
    print(f"    SE Corner: {inner_bbox[0]:.7f}, {inner_bbox[3]:.7f}")
    print(f"    GDAL -te:  {inner_bbox[1]:.7f} {inner_bbox[0]:.7f} {inner_bbox[3]:.7f} {inner_bbox[2]:.7f}")
    
    print("-" * 65)
    
    print(f"🖼️  OUTER BOX ({outer_size}m):")
    print(f"    NW Corner: {outer_bbox[2]:.7f}, {outer_bbox[1]:.7f}")
    print(f"    SE Corner: {outer_bbox[0]:.7f}, {outer_bbox[3]:.7f}")
    
    # Check for clipping
    if c_min_lat < inner_bbox[0] or c_max_lat > inner_bbox[2] or \
       c_min_lon < inner_bbox[1] or c_max_lon > inner_bbox[3]:
        print("\n‼ WARNING: Course still exceeds the Inner 2km Square!")
        print("   Action: Increase 'inner_size' in Kymapfactory4opcd.py.")
    else:
        print("\n✅ Success: Course is fully contained in the Inner Square.")
    print("-" * 65)

    return center_lat, center_lon, inner_bbox, outer_bbox

if __name__ == "__main__":
    get_course_center()
