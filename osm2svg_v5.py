#!/usr/bin/env python3
"""
osm2svg_v5.py takes map.osm and coverts it to out.svg with parameters set to those specified in styles.json.
This version was updated to handle the the map.osm from Overpass as well as OpenStreetMap.  This version also
scales and clips map.osm features to the the coordinates specified for the map.osm download.   Additionally
it sets the Default scale to be 1 meter = 1 mm in SVG units.
"""

import xml.etree.ElementTree as ET
import math
import sys
import os
import json 

# --- Global Configuration and Assumed Inputs ---
inputFile = "map.osm"
outputFile = "out.svg" 
styleFile = "styles.json"

# Default Scale Configuration
SCALE_RATIO_DENOMINATOR = 1000 
SCALE_CONFIG_FILE = "scale_config.txt"

# Define constants for geometric calculations
EARTH_RADIUS_M = 6371000  # Mean Earth radius in meters
METERS_PER_DEGREE_LAT = 111320 

# --- Global Projection Variables (Set by calculate_and_set_projection) ---
MIN_LAT = 0.0
MAX_LAT = 0.0
MIN_LON = 0.0
MAX_LON = 0.0

MM_PER_METER = 1.0 

METERS_PER_DEGREE_LON_FACTOR = 0.0
METERS_PER_DEGREE_LAT_FACTOR = 0.0 

SVG_WIDTH_MM = 0.0
SVG_HEIGHT_MM = 0.0

# Global containers for parsed data (used in main and helper functions)
nodes = {}
ways = {}

""" Map.osm has many features that extend beyond the boundries we specified on download.
    (example roads, highways, creeks) can extend for a longer distance that we want.
    So here is a Utility for Line Segment Clipping (Liang-Barsky-inspired)
"""
def clip_polyline_to_bounds(coords, min_x, min_y, max_x, max_y):
    """
    Clips a polyline (list of points) to a rectangular bounding box.
    Returns a new list of coordinates, including new intersection points.
    
    The output polyline will be broken into multiple segments if it enters 
    and leaves the box multiple times.
    """
    clipped_coords = []
    
    # Iterate over segments (P1, P2)
    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i+1]
        
        # t parameters for parametric line: P(t) = P1 + t * (P2 - P1), 0 <= t <= 1
        t0 = 0.0
        t1 = 1.0
        
        dx = x2 - x1
        dy = y2 - y1
        
        # Clipping loop: 4 boundaries (left, right, bottom, top)
        for p, q in [
            (-dx, x1 - min_x),  # Left boundary (x >= min_x)
            (dx, max_x - x1),   # Right boundary (x <= max_x)
            (-dy, y1 - min_y),  # Bottom boundary (y >= min_y)
            (dy, max_y - y1)    # Top boundary (y <= max_y)
        ]:
            if p == 0:
                # Parallel line: check if it's outside the bounds
                if q < 0:
                    t0 = 2.0 # Force segment rejection
                    break 
            else:
                r = q / p
                if p < 0:
                    # Entry point check
                    t0 = max(t0, r)
                else:
                    # Exit point check
                    t1 = min(t1, r)

        # If t0 < t1, the segment is visible (or partially visible)
        if t0 < t1:
            segment_start = None
            segment_end = None
            
            # Start Point P(t0)
            if t0 > 0.0:
                segment_start = (x1 + t0 * dx, y1 + t0 * dy)
            else:
                # t0 == 0.0 means P1 is inside or on boundary
                segment_start = (x1, y1)
                
            # End Point P(t1)
            if t1 < 1.0:
                segment_end = (x1 + t1 * dx, y1 + t1 * dy)
            else:
                # t1 == 1.0 means P2 is inside or on boundary
                segment_end = (x2, y2)
                
            # Add segment to clipped coordinates list
            # We only add the start point if it's the *first* point of a 
            # visible segment (i.e., not a duplicate of the previous end point)
            
            if not clipped_coords or clipped_coords[-1] != segment_start:
                clipped_coords.append(segment_start)
            
            clipped_coords.append(segment_end)
            
    return clipped_coords


def convert_stroke_to_path(way_coords, stroke_width, attrs, radius):
    """
    Calculates a complex path 'd' string for stroke-to-path conversion.
    """
    if not way_coords:
        return ""
    
    # Placeholder implementation: Returns a simple polyline path
    path_d = f"M {way_coords[0][0]:.4f},{way_coords[0][1]:.4f}"
    for x, y in way_coords[1:]:
        path_d += f"L {x:.4f},{y:.4f}"
        
    # If a way is closed, close the path (important for area features converted from strokes)
    if way_coords[0] == way_coords[-1]:
        path_d += " Z"

    return path_d


def get_way_coordinates(way_id):
    """Retrieves and projects the SVG coordinates for a single way ID."""
    coords = []
    global nodes, ways
    if way_id in ways:
        for nodeid in ways[way_id]:
            if nodeid in nodes:
                lon, lat = nodes[nodeid]
                x, y = lon_lat_to_svg_xy(lon, lat)
                coords.append((x, y))
    return coords


def join_ways_into_path_d(way_coords_list):
    """
    Joins coordinate segments (lists of (x, y) tuples) into a single 
    SVG path 'd' string segment (M...L...Z). Assumes segments are ordered 
    and closed by the MultiPolygon definition.
    """
    all_coords = []
    for segment in way_coords_list:
        all_coords.extend(segment)
    
    if not all_coords:
        return ""

    path_d = f"M {all_coords[0][0]:.4f},{all_coords[0][1]:.4f}"
    
    for x, y in all_coords[1:]:
        path_d += f" L {x:.4f},{y:.4f}"
        
    # If the first and last point are the same, close the path (crucial for valid polygons)
    if math.isclose(all_coords[0][0], all_coords[-1][0], abs_tol=0.0001) and \
       math.isclose(all_coords[0][1], all_coords[-1][1], abs_tol=0.0001):
        path_d += " Z"
    
    return path_d


def process_multipolygon_relation(relation, styles):
    """
    Processes OSM relations tagged as type=multipolygon into a single 
    SVG path element, handling outer and inner rings (holes).
    """
    # 1. Identify style from relation tags
    style_data = None
    feature_tag = None
    
    for tag in relation.findall('tag'):
        searchtag = f"{tag.get('k')}.{tag.get('v')}"
        if searchtag in styles:
            style_data = styles[searchtag]
            feature_tag = searchtag
            break
        elif tag.get('k') in styles:
            style_data = styles[tag.get('k')]
            feature_tag = tag.get('k')
            break
            
    if style_data is None:
        return None

    # 2. Separate members by role (outer/inner)
    outer_way_coords_list = []
    inner_way_coords_list = []
    
    bbox = (0, 0, SVG_WIDTH_MM, SVG_HEIGHT_MM)
    
    for member in relation.findall('member'):
        if member.get('type') == 'way':
            way_id = member.get('ref')
            role = member.get('role')
            
            # Get projected coordinates
            coords = get_way_coordinates(way_id)
            if not coords:
                continue

            # CRITICAL: Clip the coordinates for the way segment
            clipped_coords = clip_polyline_to_bounds(coords, *bbox)
            
            if not clipped_coords:
                continue

            # Store the clipped coordinates list
            if role == 'outer':
                outer_way_coords_list.append(clipped_coords)
            elif role == 'inner':
                inner_way_coords_list.append(clipped_coords)
            
    if not outer_way_coords_list:
        return None
        
    # 3. Join coordinate lists and build path segments (M...Z)
    outer_d = join_ways_into_path_d(outer_way_coords_list)
    
    if not outer_d:
        return None
        
    # Inner ring(s) - these create holes
    inner_d_parts = []
    for inner_coords_list in inner_way_coords_list:
        inner_d_parts.append(join_ways_into_path_d([inner_coords_list]))
        
    # 4. Combine outer and inner path data (Path with holes)
    final_path_d = outer_d + " " + " ".join(inner_d_parts)

    # 5. Generate SVG element
    svg_element = (
        f'<path d="{final_path_d}" '
        f'{style_data["svg_style"]} '
        f'fill-rule="evenodd" ' # Essential for handling holes in SVG
        f'id="rel_{relation.get("id")}_{feature_tag}"/>\n'
    )
    
    # 6. Return feature object
    return {
        'z': style_data['z-order'],
        'svg': svg_element
    }


# --- Core Projection Functions (Unchanged) ---
def read_scale_config():
    """Reads the scale ratio denominator from the configuration file and calculates MM_PER_METER."""
    global SCALE_RATIO_DENOMINATOR, MM_PER_METER
    
    if os.path.exists(SCALE_CONFIG_FILE):
        try:
            with open(SCALE_CONFIG_FILE, 'r') as f:
                content = f.read().strip()
                denominator = int(content)
                if denominator > 0:
                    SCALE_RATIO_DENOMINATOR = denominator
                    print(f"Read scale ratio denominator: 1:{SCALE_RATIO_DENOMINATOR}")
                else:
                    raise ValueError("Denominator must be positive.")
        except Exception as e:
            print(f"Error reading scale config: {e}. Using default 1:1000.")

    MM_PER_METER = 1000.0 / SCALE_RATIO_DENOMINATOR


def calculate_and_set_projection(min_lat, max_lat, min_lon, max_lon):
    """
    Calculates and sets all global projection factors and dimensions based on bounds and scale.
    """
    global MIN_LAT, MAX_LAT, MIN_LON, MAX_LON
    global SVG_WIDTH_MM, SVG_HEIGHT_MM
    global METERS_PER_DEGREE_LON_FACTOR, METERS_PER_DEGREE_LAT_FACTOR

    MIN_LAT, MAX_LAT = min_lat, max_lat
    MIN_LON, MAX_LON = min_lon, max_lon
    
    avg_lat_rad = (min_lat + max_lat) / 2.0 * (math.pi / 180.0)
    
    METERS_PER_DEGREE_LON_FACTOR = METERS_PER_DEGREE_LAT * math.cos(avg_lat_rad)
    METERS_PER_DEGREE_LAT_FACTOR = METERS_PER_DEGREE_LAT 

    lon_range_deg = max_lon - min_lon
    lat_range_deg = max_lat - min_lat

    map_width_m = lon_range_deg * METERS_PER_DEGREE_LON_FACTOR
    map_height_m = lat_range_deg * METERS_PER_DEGREE_LAT_FACTOR
    
    SVG_WIDTH_MM = map_width_m * MM_PER_METER
    SVG_HEIGHT_MM = map_height_m * MM_PER_METER
    
    print(f"Calculated Map Dimensions (using 1:{SCALE_RATIO_DENOMINATOR} scale):")
    print(f"  Real Width: {map_width_m:.2f} m")
    print(f"  Real Height: {map_height_m:.2f} m")
    print(f"  SVG Width: {SVG_WIDTH_MM:.2f} mm")
    print(f"  SVG Height: {SVG_HEIGHT_MM:.2f} mm")


def generate_svg_header_from_bounds(min_lat, max_lat, min_lon, max_lon):
    """
    Calculates the final SVG dimensions based on the bounds and scale, 
    and generates the complete SVG root tag.
    """
    # NOTE: This must be called first to populate SVG_WIDTH_MM and SVG_HEIGHT_MM
    calculate_and_set_projection(min_lat, max_lat, min_lon, max_lon)
    
    width_val = round(SVG_WIDTH_MM, 4) 
    height_val = round(SVG_HEIGHT_MM, 4)
    viewbox = f"0 0 {width_val} {height_val}"
    
    header = f'''<svg xmlns="http://www.w3.org/2000/svg" 
     xmlns:xlink="http://www.w3.org/1999/xlink" 
     width="{width_val}mm" 
     height="{height_val}mm" 
     viewBox="{viewbox}" 
     version="1.1">
<!--
  Projection Details:
  Scale: 1:{SCALE_RATIO_DENOMINATOR} (1 real meter = {MM_PER_METER:.4f} drawing millimeters)
  SVG Origin (0, 0) in Geo: {MIN_LON}, {MAX_LAT} (Top-Left Corner)
-->'''
    return header


def lon_lat_to_svg_xy(lon, lat):
    """
    Transforms a single (lon, lat) coordinate into SVG (x, y) coordinates (in mm).
    """
    m_per_deg_lon = METERS_PER_DEGREE_LON_FACTOR
    m_per_deg_lat = METERS_PER_DEGREE_LAT_FACTOR

    lon_offset_deg = lon - MIN_LON
    lat_offset_deg = lat - MIN_LAT
    
    x_offset_m = lon_offset_deg * m_per_deg_lon
    y_offset_m = lat_offset_deg * m_per_deg_lat
    
    x = x_offset_m * MM_PER_METER

    lat_range_deg = MAX_LAT - MIN_LAT
    total_height_m = lat_range_deg * m_per_deg_lat
    
    y_dist_from_top_m = total_height_m - y_offset_m
    
    y = y_dist_from_top_m * MM_PER_METER

    return x, y


# ----------------------------------------------------------------------
# --- The Main Execution Function ---
# ----------------------------------------------------------------------
def main():
    """The main function to execute the conversion process."""

    global nodes, ways 

    read_scale_config()
    
    # --- Load Style File ---
    try:
        clean_json_lines = []
        with open(styleFile, 'r') as f:
            for line in f:
                stripped_line = line.strip()
                # Simple check for JSON comments (// or # at start of line)
                if stripped_line.startswith('//') or stripped_line.startswith('#'):
                    continue
                if stripped_line.startswith('"COMMENT"'):
                    continue
                
                if stripped_line:
                    clean_json_lines.append(line)
            
        json_string = "".join(clean_json_lines)
        styleDef = json.loads(json_string)
    
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error loading style file '{styleFile}': {e}")
        sys.exit(1)

    # --- Parse Styles ---
    styles = {}
    for tag, attrs in styleDef.items():
        # Pop and process custom keys
        stroke_to_path = attrs.pop('stroke_to_path', False)
        if isinstance(stroke_to_path, str):
            stroke_to_path = (stroke_to_path.lower() == 'true')

        corner_radius = float(attrs.pop('corner_radius', 0))

        try:
            z_order = int(attrs.pop('z-order', 0))
        except ValueError:
            print(f"Warning: Invalid z-order value for {tag}. Using 0.")
            z_order = 0
            
        # Create the SVG style string from remaining standard attributes
        svg_style_string = ' '.join([f'{k}="{v}"' for k, v in attrs.items()])
        
        styles[tag] = {
            'svg_style': svg_style_string,
            'stroke_to_path': stroke_to_path,
            'corner_radius': corner_radius,
            'z-order': z_order,
            'attrs': attrs # Store original attributes for access
        }

    # --- Load OSM File ---
    try:
        tree = ET.parse(inputFile) 
        document = tree.getroot() 
    except (FileNotFoundError, ET.ParseError) as e:
        print(f"Error loading input file '{inputFile}': {e}")
        sys.exit(1)

    # --- Get Bounds and Calculate Projection ---
    boundsElems = document.findall('bounds')
    if len(boundsElems) != 1:
        print("Expected exactly one <bounds/> element. Something is weird.")
        sys.exit(1)

    b = boundsElems[0]
    minlat = float(b.get('minlat'))
    maxlat = float(b.get('maxlat'))
    minlon = float(b.get('minlon')) 
    maxlon = float(b.get('maxlon'))
    
    # This call sets the global SVG_WIDTH_MM and SVG_HEIGHT_MM variables
    svg_header = generate_svg_header_from_bounds(minlat, maxlat, minlon, maxlon)
    
    # Define the bounding box for clipping
    CLIP_BBOX = (0, 0, SVG_WIDTH_MM, SVG_HEIGHT_MM)

    # --- Load nodes and ways into memory ---
    nodes = {node.get('id'): (float(node.get('lon')), float(node.get('lat')))
             for node in document.findall('node')}

    ways = {}
    for way in document.findall('way'):
        ways[way.get('id')] = [noderef.get('ref') for noderef in way.findall('nd')]
        
    svg_features = []
            
    # --- PART 2: Feature Collection (Simple Ways) ---
    for way in document.findall('way'):
        style_data = None
        feature_tag = None 
        
        # 1. Find the appropriate style data for the way
        for tag in way.findall('tag'):
            searchtag = f"{tag.get('k')}.{tag.get('v')}"
            if searchtag in styles:
                style_data = styles[searchtag]
                feature_tag = searchtag
                break
            elif tag.get('k') in styles:
                style_data = styles[tag.get('k')]
                feature_tag = tag.get('k')
                break

        if style_data is None:
            continue

        # 2. Convert node references to SVG coordinates
        way_coords_unclipped = get_way_coordinates(way.get('id'))

        if len(way_coords_unclipped) < 2:
            continue
            
        # 3. CRITICAL: Clip the projected coordinates to the SVG viewbox
        way_coords = clip_polyline_to_bounds(way_coords_unclipped, *CLIP_BBOX)

        if len(way_coords) < 2:
            continue

        # 4. Generate SVG element string
        svg_element = ""

        if style_data.get('stroke_to_path', False): 
            # --- STROKE-TO-PATH LOGIC (results in a FILLED shape/path) ---
            stroke_width = float(style_data['attrs'].get('stroke-width', 4.0))
            radius = float(style_data.get('corner_radius', 0.0)) 

            path_d = convert_stroke_to_path(way_coords, stroke_width, style_data['attrs'], radius) 

            # Apply the full, original SVG style string to the path
            svg_element = (
                f'<path d="{path_d}" '
                f'{style_data["svg_style"]} '
                f'id="way_{way.get("id")}_path_{feature_tag}"/>\n' 
            )

        else:
            # --- STANDARD POLYLINE LOGIC (results in an unfilled line) ---
            polyline_points = ' '.join([f"{x:.4f} {y:.4f}" for x, y in way_coords])
            
            # Use the pre-calculated style string
            svg_element = f'<polyline points="{polyline_points}" {style_data["svg_style"]} id="way_{way.get("id")}_{feature_tag}"/>\n'


        # 5. STORE the feature and its z-order
        if svg_element:
            svg_features.append({
                'z': style_data['z-order'],
                'svg': svg_element
            })

    # --- PART 3: Feature Collection (Relations/MultiPolygons) ---
    for relation in document.findall('relation'):
        
        is_multipolygon = False
        # Check for the type=multipolygon tag
        for tag in relation.findall('tag'):
            if tag.get('k') == 'type' and tag.get('v') == 'multipolygon':
                is_multipolygon = True
                break
        
        if is_multipolygon:
            # Clipping logic is now embedded inside process_multipolygon_relation
            feature = process_multipolygon_relation(relation, styles) 
            if feature:
                svg_features.append(feature)

    # --------------------------------------------------------
    # --- FINAL STEP: Sort and Write to File ---
    # --------------------------------------------------------
    
    # Sort features by Z-order before writing
    svg_features.sort(key=lambda x: x['z'])

    try:
        with open(outputFile, 'w') as out:
            out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            out.write(svg_header)

            for feature in svg_features:
                out.write(feature['svg'])

            out.write('</svg>')

        print(f"\n✅ Successfully generated SVG file: {outputFile}")

    except IOError as e:
        print(f"Error writing to output file: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
    
