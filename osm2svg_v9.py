#!/usr/bin/env python3
"""
osm2svg_v9.py takes map.osm and coverts it to out.svg with parameters set at the command-line and specified in styles.json.
This version was updated to handle the the map.osm from the Overpass API as well as export directly from OpenStreetMap.org.
The out.svg is designed for the new beta-Clender program.  This version also scales and clips map.osm features to the the
coordinates specified in the map.osm download.  Additionally, it sets the Default SVG scale to be 1 meter = 1 mm of SVG units
per OPCD recomendations.  
"""

import xml.etree.ElementTree as ET
import math
import sys
import os
import argparse
import json
import rasterio
import rasterio.warp
from rasterio.warp import reproject, Resampling
import numpy as np
import shapely.geometry as sg
from shapely.geometry import Point, LineString, Polygon, MultiPolygon
from shapely.ops import unary_union, split
import traceback

# --- Global Configuration and Assumed Inputs ---
inputFile = "map.osm"
outputFile = "out.svg" 
styleFile = "styles.json"

# Default Scale Configuration
SCALE_RATIO_DENOMINATOR = 1000 

# Constants
EARTH_RADIUS_M = 6371000  # Mean Earth radius in meters
METERS_PER_DEGREE_LAT = 111320

MM_PER_METER = 1000       # 1 meter = 1.0 meters
METER_LENGTH = 1.0        # SVG Units are in mm.
METERS_PER_YARD = 0.9144     # 1 yard = 0.9144 meters
YARDS_PER_METER = 1.0 / METERS_PER_YARD
METERS_PER_FOOT = 0.3048     # 1 foot = 0.3048 meters
FEET_PER_METER = 1.0 / METERS_PER_FOOT
METERS_PER_INCH = 0.0254     # 1 inch = 0.0254 meters
INCHES_PER_METER = 1.0 / METERS_PER_INCH

# Inkscape uses mm/pc/or px for scale bar units.  We assume 'mm' is used as the scale unit with 1mm = (1 SVG unit) = 1 meter.
# For yards, feet, and inches, replace METER_LENGHT with METERS_PER_YARD, METERS_PER_FOOT or METERS_PER_INCH here.  Then 1mm = (1 SVG unit) = 1 Yard (or Foot or Inch).
# SVG_MM_EQUIVALENCE = MM_PER_METER * METERS_PER_YARD
SVG_MM_EQUIVALENCE = MM_PER_METER * METER_LENGTH

SIMPLIFY_TOLERANCE = 0.1

FILLET_RADIUS = 3.0       # radius used at corners of paths.
MAX_STROKE_WIDTH_MM = 3.0 # You tune MAX_STROKE_WIDTH_MM to match the widest line in your styles.json
KERF_SEPARATION_MM = 0.05 
UNBREAKABLE_KERF_MM = 0.5 # seperation to

# Globals
METERS_PER_DEGREE_LON_FACTOR = 0.0
METERS_PER_DEGREE_LAT_FACTOR = 0.0 
SVG_WIDTH_MM = 0.0
SVG_HEIGHT_MM = 0.0
MIN_LAT = 0.0
MAX_LAT = 0.0
MIN_LON = 0.0
MAX_LON = 0.0
SAFETY_INSET_MM = 8.0 
CLIP_DISTANCE = 0.0  # Will hold the clip radius in METERS
CLIP_MARGIN_MM = 0.1 # Will hold the kerf separation in MM (0.05)
CLIP_BBOX = None      # CLIP_BBOX holds the inset boundry of our SVG
MAP_MIN_X = 0.0      # Projected X-coordinate of the map's left edge (in meters)
MAP_MAX_Y = 0.0      # Projected Y-coordinate of the map's top edge (in meters)
REAL_TO_SVG_SCALE = 0.0 # The final calculated scale factor (mm/meter)
MAP_WIDTH_M = 0.0    # Projected Width in Meters
MAP_HEIGHT_M = 0.0   # Projected Height in Meters

# Global containers for parsed data (used in main and helper functions)
nodes = {}
ways = {}
map_features = {}

# ----------------------------------------------------------------
# --- Setup and Configuration FUNCTIONS: A1. parse_arguments() ---
# ----------------------------------------------------------------
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Converts OpenStreetMap data (in projected meters) to an SVG file, with optional GeoTIFF background layers."
    )

    # Core File Arguments
    parser.add_argument(
        '--infile', 
        type=str, 
        default='map.osm', 
        help='Input OSM XML file (default: map.osm)'
    )
    parser.add_argument(
        '--outfile', 
        type=str, 
        default='out.svg', 
        help='Output SVG file (default: out.svg)'
    )
    parser.add_argument(
        '--styles', 
        type=str, 
        default='styles.json', 
        help='JSON style definition file (default: styles.json)'
    )
    
    # Background Image Arguments
    # Note: These use action='append' to allow multiple, or you can use type=str and split later.
    # Given your request, a simple string for each is fine for now.
    parser.add_argument(
        '--background1', 
        type=str, 
        default=None, 
        help='Path to the first GeoTIFF image file for the background.'
    )
    parser.add_argument(
        '--background2', 
        type=str, 
        default=None, 
        help='Path to the second GeoTIFF image file for the background.'
    )
    parser.add_argument(
        '--background3', 
        type=str, 
        default=None, 
        help='Path to the third GeoTIFF image file for the background.'
    )
    parser.add_argument(
        '--background4', 
        type=str, 
        default=None, 
        help='Path to the fourth GeoTIFF image file for the background.'
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------------------------------------------
# --- Setup and Configuration FUNCTIONS: A2. calculate_and_set_projection(min_lat, max_lat, min_lon, max_lon) ---
# ---------------------------------------------------------------------------------------------------------------
def calculate_and_set_projection(min_lat, max_lat, min_lon, max_lon):
    """
    Calculates and sets all global projection factors and dimensions based on bounds and scale.
    """
    global MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, REAL_TO_SVG_SCALE    
    global SVG_WIDTH_MM, SVG_HEIGHT_MM, MAP_WIDTH_M, MAP_HEIGHT_M
    global METERS_PER_DEGREE_LON_FACTOR, METERS_PER_DEGREE_LAT_FACTOR
    global MM_PER_METER, SCALE_RATIO_DENOMINATOR # Added for clarity in calculation

    # 1. Set map bounds globally
    MIN_LAT, MAX_LAT = min_lat, max_lat
    MIN_LON, MAX_LON = min_lon, max_lon
    
    # Calculate average latitude for projection accuracy (Plate Carree)
    avg_lat_rad = (min_lat + max_lat) / 2.0 * (math.pi / 180.0)
    
    # 2. Calculate meter-per-degree factors
    METERS_PER_DEGREE_LON_FACTOR = METERS_PER_DEGREE_LAT * math.cos(avg_lat_rad)
    METERS_PER_DEGREE_LAT_FACTOR = METERS_PER_DEGREE_LAT 

    # 3. Calculate Real-World Dimensions in Meters
    lon_range_deg = max_lon - min_lon
    lat_range_deg = max_lat - min_lat
    MAP_WIDTH_M = lon_range_deg * METERS_PER_DEGREE_LON_FACTOR
    MAP_HEIGHT_M = lat_range_deg * METERS_PER_DEGREE_LAT_FACTOR
    
    # 4. Get Scale Factor and apply.
    REAL_TO_SVG_SCALE = MM_PER_METER / SVG_MM_EQUIVALENCE
    SVG_WIDTH_MM = MAP_WIDTH_M * REAL_TO_SVG_SCALE
    SVG_HEIGHT_MM = MAP_HEIGHT_M * REAL_TO_SVG_SCALE
    
    # -------------------------------------------------------------------
    print(f"Calculated Map Dimensions (using 1:{SCALE_RATIO_DENOMINATOR} scale):")
    print(f"  Real Width: {MAP_WIDTH_M:.2f} m")
    print(f"  Real Height: {MAP_HEIGHT_M:.2f} m")
    print(f"  Real-to-SVG Scale Factor: {REAL_TO_SVG_SCALE:.2f} m/mm") 
    print(f"  SVG Width: {SVG_WIDTH_MM:.2f} mm")
    print(f"  SVG Height: {SVG_HEIGHT_MM:.2f} mm")
    print(f"\n")

    return MAP_WIDTH_M, MAP_HEIGHT_M, REAL_TO_SVG_SCALE


# ------------------------------------------------------------------------------------------------------------------
# --- Setup and Configuration FUNCTIONS: A3. generate_svg_header_from_bounds(min_lat, max_lat, min_lon, max_lon) ---
# ------------------------------------------------------------------------------------------------------------------
def generate_svg_header_from_bounds(min_lat, max_lat, min_lon, max_lon):
    """
    Calculates the final SVG dimensions and generates the complete SVG root tag.
    """
    global SVG_WIDTH_MM, SVG_HEIGHT_MM
    
    # 1. Get all global projection/scale factors.
    map_width_m, map_height_m, REAL_TO_SVG_SCALE = calculate_and_set_projection(min_lat, max_lat, min_lon, max_lon)
    
    # 2. Define the SVG's physical size (still massive in mm for scale accuracy)
    width_mm_val = round(SVG_WIDTH_MM, 4)   # SVG Units.
    height_mm_val = round(SVG_HEIGHT_MM, 4) 
    map_width_m = round(SVG_WIDTH_MM / REAL_TO_SVG_SCALE, 4) 
    map_height_m = round(SVG_HEIGHT_MM / REAL_TO_SVG_SCALE, 4) 

    # 3. Set viewBox coordinates (in Meters).
    viewbox = f"0 0 {width_mm_val} {height_mm_val}"
    svg_header = (
        f'<svg width="{SVG_WIDTH_MM}mm" height="{SVG_HEIGHT_MM}mm" '
        f'viewBox="0 0 {SVG_WIDTH_MM} {SVG_HEIGHT_MM}" '
        'xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" ' 
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">\n'
    )
    
    # Many of necessary globals are already set by A2.
    return header, map_width_m, map_height_m, REAL_TO_SVG_SCALE    


# --------------------------------------------------------------------------------------
# --- B1. generate_svg_header_from_bounds(minlat, maxlat, minlon, maxlon) ---
# --------------------------------------------------------------------------------------
def generate_svg_header_from_bounds(minlat, maxlat, minlon, maxlon):
    """
    Calculates the projection factors and creates the SVG XML header.
    Sets the global coordinate system to Millimeters (1 unit = 1mm).
    """
    global MIN_LAT, MAX_LAT, MIN_LON, MAX_LON
    global METERS_PER_DEGREE_LAT_FACTOR, METERS_PER_DEGREE_LON_FACTOR
    global SVG_WIDTH_MM, SVG_HEIGHT_MM, MAP_MIN_X, MAP_MAX_Y, REAL_TO_SVG_SCALE

    # Store bounds globally for use in other B-series functions
    MIN_LAT, MAX_LAT = minlat, maxlat
    MIN_LON, MAX_LON = minlon, maxlon

    # 1. Calculate the 'Flat Earth' projection factors at this specific latitude
    # 1 degree of latitude is roughly 111,320 meters
    METERS_PER_DEGREE_LAT_FACTOR = 111320.0
    # Longitude length shrinks as you move toward the poles
    avg_lat_rad = math.radians((minlat + maxlat) / 2.0)
    METERS_PER_DEGREE_LON_FACTOR = METERS_PER_DEGREE_LAT_FACTOR * math.cos(avg_lat_rad)

    # 2. Determine Real World Dimensions in Meters
    map_width_m = (maxlon - minlon) * METERS_PER_DEGREE_LON_FACTOR
    map_height_m = (maxlat - minlat) * METERS_PER_DEGREE_LAT_FACTOR

    # 3. Scale Factor: 1.0 means 1 meter = 1mm (1:1000 scale)
    REAL_TO_SVG_SCALE = 1.0 
    
    # Calculate Final SVG Paper Size
    SVG_WIDTH_MM = map_width_m * REAL_TO_SVG_SCALE
    SVG_HEIGHT_MM = map_height_m * REAL_TO_SVG_SCALE

    # 4. Establish the 'Origin' for the coordinate math
    # Map Min X is the leftmost Longitude; Map Max Y is the topmost Latitude (SVG 0 is top)
    MAP_MIN_X = MIN_LON
    MAP_MAX_Y = MAX_LAT

    header = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        f'<svg width="{SVG_WIDTH_MM}mm" height="{SVG_HEIGHT_MM}mm" '
        f'viewBox="0 0 {SVG_WIDTH_MM} {SVG_HEIGHT_MM}"\n'
        f'  xmlns="http://www.w3.org/2000/svg"\n'
        f'  xmlns:xlink="http://www.w3.org/1999/xlink"\n'
        f'  xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"\n'
        f'  xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd">\n'
        f'\n'
    )
    
    print(f"INFO: SVG Dimensions: {SVG_WIDTH_MM:.2f}mm x {SVG_HEIGHT_MM:.2f}mm")
    return header, map_width_m, map_height_m, REAL_TO_SVG_SCALE


# --------------------------------------------------------------------------------------
# --- B2. project_lon_lat(lon, lat) ---
# --------------------------------------------------------------------------------------
def project_lon_lat(lon, lat):
    """
    The core transformer. Converts a single Lon/Lat point into SVG X/Y.
    X increases to the right. Y increases downward.
    """
    x = (lon - MAP_MIN_X) * METERS_PER_DEGREE_LON_FACTOR * REAL_TO_SVG_SCALE
    # In SVG, Y=0 is the top. So we subtract current lat from the MAX (top) latitude.
    y = (MAP_MAX_Y - lat) * METERS_PER_DEGREE_LAT_FACTOR * REAL_TO_SVG_SCALE
    return x, y


# --------------------------------------------------------------------------------------
# --- B3. get_way_coordinates(way_id) ---
# --------------------------------------------------------------------------------------
def get_way_coordinates(way_id):
    """
    Retrieves the raw nodes for a way and projects them into the SVG coordinate space.
    """
    if way_id not in ways:
        return []
    
    coords = []
    for node_id in ways[way_id]['refs']:
        if node_id in nodes:
            lon, lat = nodes[node_id]
            x, y = project_lon_lat(lon, lat)
            coords.append((x, y))
            
    return coords


# --------------------------------------------------------------------------------
# --- OSM Data & Geometry Processing FUNCTIONS C1. get_way_coordinates(way_id) ---
# --------------------------------------------------------------------------------
def get_way_coordinates(way_id):
    """Retrieves and projects the SVG coordinates for a single way ID."""
    coords = []
    global nodes, ways
    
    if way_id in ways:
        node_refs = ways[way_id].get('refs')
        if node_refs:
            for nodeid in node_refs:
                if nodeid in nodes:
                    lon, lat = nodes[nodeid]
                    x, y = project_lon_lat(lon, lat)
                    coords.append((x, y))
    return coords


# -------------------------------------------------------------------------------------------
# --- OSM Data & Geometry Processing FUNCTIONS C2. join_ways_into_path_d(way_coords_list) ---
# -------------------------------------------------------------------------------------------
def join_ways_into_path_d(way_coords_list, return_coords_only=False):
    """
    Joins coordinate segments (lists of (x, y) tuples) into a single 
    SVG path 'd' string segment (M...L...Z). Assumes segments are ordered 
    and closed by the MultiPolygon definition.
    """
    all_coords = []
    for segment in way_coords_list:
        all_coords.extend(segment)
    
    if not all_coords: return ""
    if return_coords_only: return all_coords

    # Use E2 (format_coords) to ensure the 4-decimal standard is kept
    path_d = f"M {format_coords([all_coords[0]])}"
    if len(all_coords) > 1:
        path_d += f" L {format_coords(all_coords[1:])}"
        
    if math.isclose(all_coords[0][0], all_coords[-1][0], abs_tol=0.0001) and \
       math.isclose(all_coords[0][1], all_coords[-1][1], abs_tol=0.0001):
        path_d += " Z"
    
    return path_d


# ----------------------------------------------------------------------------------------------------
# --- OSM Data & Geometry Processing FUNCTIONS C3. process_multipolygon_relation(relation, styles) ---
# ----------------------------------------------------------------------------------------------------
def process_multipolygon_relation(relation, styles):
    """
    Processes OSM relations tagged as type=multipolygon into a single 
    SVG path element, handling outer and inner rings (holes).
    """
    # 1. Identify style from relation tags
    style_data = None
    feature_tag = None
    
    for tag in relation.iter('tag'):
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
    
    for member in relation.iter('member'):
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
    
    # Call join_ways_into_path_d which will return a joined list of coordinates for Shapely
    outer_d = join_ways_into_path_d(outer_way_coords_list, return_coords_only=True)
        
    # 3.1 Convert inner rings to a list of coordinate tuples (or whatever Shapely expects)
    inner_rings = []
    for inner_coords_list in inner_way_coords_list:
        inner_rings.append(join_ways_into_path_d([inner_coords_list], return_coords_only=True))

    final_shapely_polygon = None
    try:
        if outer_d:
            # Create Shapely Polygon with outer shell and inner holes (rings)
            final_shapely_polygon = sg.Polygon(outer_d, inner_rings)
            # Ensure the polygon is valid
            if not final_shapely_polygon.is_valid:
                final_shapely_polygon = final_shapely_polygon.buffer(0) # Attempt to fix invalid geometry
                
    except Exception as e:
        print(f"Warning: Could not create Multipolygon for relation {relation.get('id')}: {e}")
        return [] # Return empty list on failure

    if final_shapely_polygon is None or final_shapely_polygon.is_empty:
        return []

    # 4. Combine outer and inner path data (Path with holes)
    relation_tags = {tag.get('k'): tag.get('v') for tag in relation.iter('tag')}
    final_osm_tag = feature_tag.split('.')[0] if '.' in feature_tag else feature_tag
    # build a desctiptive id.
    relation_id = relation.get('id')
    safe_tag = feature_tag.replace('.', '-').replace('=', '-')
    descriptive_base_id = f"{safe_tag}-{relation_id}"  # Add description to the id
    
    if relation_tags.get('building') is not None:
        final_osm_tag = 'building'

    # 5. Generate SVG element
    return [{
        'shape': final_shapely_polygon,  # The Shapely object
        'tag': feature_tag,
        'id': relation_id,
        'base_id': descriptive_base_id, 
        'osm_tag': final_osm_tag,
        'style_data': style_data,
        'requires_line_clip': False,     
        'requires_stroke_to_path': False,
        'z-order': style_data['z-order']
    }]


# --------------------------------------------------------------------------------------------------------
# --- OSM Data & Geometry Processing FUNCTIONS C4. smooth_corners_by_buffer(geometry, radius, osm_tag) ---
# --------------------------------------------------------------------------------------------------------
def smooth_corners_by_buffer(geometry, radius, osm_tag=None):
    """
    Applies a small negative buffer followed by a positive buffer to round
    corners and simplify geometry while maintaining a tight boundary.
    This acts as a fillet operation.
    """
    if geometry.is_empty or osm_tag == 'building':
        return geometry
    try:
        # Join_Style 1 = ROUND. 
        smoothed = geometry.buffer(radius, join_style=1, quad_segs=8)
        return smoothed.buffer(-radius, join_style=1, quad_segs=8)

    except Exception as e:
        print(f"Warning: Corner smoothing failed: {e}")
        return geometry
    

# -------------------------------------------------------------------------------------------------------------------
# --- OSM Data & Geometry Processing FUNCTIONS C5. convert_stroke_to_path(way_coords, stroke_width, attrs, radius) --
# -------------------------------------------------------------------------------------------------------------------
def convert_stroke_to_path(way_coords, stroke_width, attrs, radius):
    """
    Calculates the filled polygon geometry from the line segments using Shapely buffer.
    """
    if len(way_coords) < 2:
        return None
    
    line = LineString(way_coords)
    radius = stroke_width / 2.0
    linecap = attrs.get('stroke-linecap', 'butt')

    if linecap == 'smooth':
        # 1. Use ROUND cap style. This makes the road a "pill" shape.
        # quad_segs=8 ensures the curve has enough points for Blender.
        poly = line.buffer(
            radius, 
            cap_style=sg.CAP_STYLE.round, 
            join_style=sg.JOIN_STYLE.round,
            quad_segs=8
        )
        
        # 2. Optional: The "Fillet Trick"
        # If you want extra-smooth internal corners, we do the buffer dance.
        # Using a radius slightly larger than 0.1 (like 0.5) makes it more visible.
        smooth_factor = 0.5 
        return poly.buffer(smooth_factor, join_style=sg.JOIN_STYLE.round).buffer(-smooth_factor)
    
    # 3. Default behavior for non-smooth features
    # Square Cap (2) and Bevel Join (3) keep things sharp and geometric.
    poly = line.buffer(
        radius,  
        cap_style=2,  
        join_style=3  
    )
    
    return poly


# -----------------------------------------------------------------------------------------------------------------
# --- OSM Data & Geometry Processing FUNCTIONS C6. get_auto_smooth_controls(p_prev, p_curr, p_next, tightness)  ---
# -----------------------------------------------------------------------------------------------------------------
def get_auto_smooth_controls(p_prev, p_curr, p_next, tightness=0.33):
    """
    C6: Calculates Bezier control points for a node.
    A tightness of 0.33 is standard; 0.55 is 'relaxed' for golf grass.
    """
    
    # Vector from previous to next
    dx = p_next[0] - p_prev[0]
    dy = p_next[1] - p_prev[1]

    # Handle length based on distance to neighbors and tightness
    d_prev = math.sqrt((p_curr[0] - p_prev[0])**2 + (p_curr[1] - p_prev[1])**2)
    d_next = math.sqrt((p_next[0] - p_curr[0])**2 + (p_next[1] - p_curr[1])**2)
    
    # The 'Relaxation' Math: Length of handles
    l_prev = d_prev * tightness
    l_next = d_next * tightness

    # Control point 1 (incoming)
    cp1 = (p_curr[0] - (dx * (l_prev / (d_prev + d_next + 1e-6))),
           p_curr[1] - (dy * (l_prev / (d_prev + d_next + 1e-6))))
           
    # Control point 2 (outgoing)
    cp2 = (p_curr[0] + (dx * (l_next / (d_prev + d_next + 1e-6))),
           p_curr[1] + (dy * (l_next / (d_prev + d_next + 1e-6))))

    return cp1, cp2


# -------------------------------------------------------------------------------------------------------
# --- OSM Data & Geometry Processing FUNCTIONS C7. ring_to_bezier_d(coords, is_closed=True, tightness ---
# -------------------------------------------------------------------------------------------------------
def ring_to_bezier_d(coords, is_closed=True, tightness=0.33):
    """
    C7: Converts a list of coordinates into a smooth SVG Bezier path string.
    """
    if len(coords) < 3:
        return "M " + " L ".join([f"{x:.4f},{y:.4f}" for x, y in coords])

    path_parts = [f"M {coords[0][0]:.4f},{coords[0][1]:.4f}"]
    
    for i in range(len(coords) - (0 if is_closed else 1)):
        p0 = coords[i-1]
        p1 = coords[i]
        p2 = coords[(i+1) % len(coords)]
        p3 = coords[(i+2) % len(coords)]
        
        # Get handles for the current segment (p1 to p2)
        _, cp1 = get_auto_smooth_controls(p0, p1, p2, tightness)
        cp2, _ = get_auto_smooth_controls(p1, p2, p3, tightness)
        
        path_parts.append(f"C {cp1[0]:.4f},{cp1[1]:.4f} {cp2[0]:.4f},{cp2[1]:.4f} {p2[0]:.4f},{p2[1]:.4f}")
        
    if is_closed: path_parts.append("Z")
    
    return " ".join(path_parts)


# --------------------------------------------------------------------------------------------------
# --- Clipping and Optimization FUNCTIONS D1. calculate_derived_clipping_constants(all_features) ---
# --------------------------------------------------------------------------------------------------
def calculate_derived_clipping_constants(all_features):
    """
    Calculates the meter-based CLIP_DISTANCE dynamically based on the max stroke width 
    of all features that require stroke-to-path conversion.
    """
    global CLIP_DISTANCE, CLIP_MARGIN_MM, KERF_SEPARATION_MM
    
    # 1. Dynamically find the maximum stroke width among features needing path conversion
    max_stroke_width = KERF_SEPARATION_MM # Initialize with a minimum value
    
    for feature in all_features:
        if feature.get('requires_stroke_to_path', False):
            style_data = feature['style_data']
            # Get the stroke-width for this specific path, convert to float.
            width = float(style_data['attrs'].get('stroke-width', 1.0))
            if width > max_stroke_width:
                max_stroke_width = width
    
    # Use 1.0 mm if no stroke-to-path features were found
    if max_stroke_width == KERF_SEPARATION_MM:
         max_stroke_width = 1.0

    # 2. Calculate the required line clip radius (in MM)
    # R_clip (mm) = (Max_Stroke_Width / 2) + Kerf_Separation
    CLIP_RADIUS_MM = (max_stroke_width / 2.0) + KERF_SEPARATION_MM

    # 3. Convert the clip radius from MM back to METERS
    CLIP_DISTANCE = CLIP_RADIUS_MM / MM_PER_METER

    # 4. Set the polygon clipping margin (in MM)
    CLIP_MARGIN_MM = KERF_SEPARATION_MM

    print(f"INFO: Max Stroke Width found: {max_stroke_width:.2f} mm")
    print(f"INFO: Calculated Line Clip Radius: {CLIP_RADIUS_MM:.4f} mm ({CLIP_DISTANCE:.6f} m)")
    print(f"INFO: Calculated Polygon Kerf Margin: {CLIP_MARGIN_MM:.4f} mm")

    
# --------------------------------------------------------------------------------------
# --- Clipping and Optimization FUNCTIONS D2. clip_intersecting_lines(line_features) ---
# --------------------------------------------------------------------------------------
def clip_intersecting_lines(line_features):
    """
    Clip intersecting lines.  This is a utility function that can be used to clip intersecting lines
    (T intersections and Crosses) such that when they are converted to a path (stroke-to-path) there
    will be no overlap.     Finds intersections between LineStrings, creates a union of buffers around them,
    and clips the original lines to create non-overlapping segments.
    """

    # 1. Collect all lines
    all_lines = [f['shape'] for f in line_features if isinstance(f['shape'], sg.LineString)]
    if not all_lines:
        return line_features
    
    # 2. Find all intersection points
    intersection_geoms = []
    
    for i in range(len(all_lines)):
        for j in range(i + 1, len(all_lines)):
            # Check for intersection
            if all_lines[i].intersects(all_lines[j]):
                intersect = all_lines[i].intersection(all_lines[j])
                
                # Separate Point/MultiPoint geometries and add to our list
                if intersect.geom_type == 'Point':
                    intersection_geoms.append(intersect)
                elif intersect.geom_type == 'MultiPoint':
                    intersection_geoms.extend(list(intersect.geoms))
                # Skip line-segment overlaps (these are harder and usually rare in OSM)
    
    if not intersection_geoms:
        return line_features
        
    # 3. Create a single, unified clip buffer geometry (a Multipolygon)
    # Create a buffer around every intersection point and take the union.
    clip_buffers = [point.buffer(CLIP_DISTANCE) for point in intersection_geoms]
    clip_union = unary_union(clip_buffers)
    
    # 4. Process and clip each line using the unified clip geometry
    new_features = []
    
    for feature in line_features:
        original_line = feature['shape']
        if not isinstance(original_line, sg.LineString):
            new_features.append(feature)
            continue
        
        # Clip the original line by subtracting the unified buffer area
        clipped_geom = original_line.difference(clip_union)
        
        # 5. Extract resulting segments and create new features
        
        # Convert the result into a list of LineStrings (could be a LineString, MultiLineString, or Empty)
        segments = []
        if clipped_geom.geom_type == 'LineString':
            segments.append(clipped_geom)
        elif clipped_geom.geom_type == 'MultiLineString':
            segments.extend(list(clipped_geom.geoms))
        # Ignore empty or other geometry types
        
        if not segments:
            # If the line was completely inside a clip buffer or disappeared, skip it
            continue
        
        # Create new feature dictionaries for each resulting segment
        for k, segment in enumerate(segments):
            # Only add segments longer than a small tolerance (prevents tiny artifacts)
            if segment.length > CLIP_DISTANCE / 2:
                base_id_for_segment = feature.get('base_id', feature['id'])
                final_segment_id = f"{base_id_for_segment}-{chr(ord('A') + k)}"
                new_features.append({
                    'shape': segment,
                    'tag': feature['tag'],
                    # Modify the ID (e.g., 'path236-A', 'path236-B')
                    'id': final_segment_id,
                    'base_id': final_segment_id,
                    # Preserve any other metadata from the original feature dict
                    **{k: v for k, v in feature.items() if k not in ('shape', 'id', 'base_id')}
                })

    return new_features


# -----------------------------------------------------------------------------------------------------------
# --- Clipping and Optimization FUNCTIONS D3. clip_polyline_to_bounds(coords, min_x, min_y, max_x, max_y) ---
# -----------------------------------------------------------------------------------------------------------
def clip_polyline_to_bounds(coords, min_x, min_y, max_x, max_y):
    """
    Map.osm has many features that extend beyond the boundries we specified on download.
    (example roads, highways, creeks) can extend for a longer distance that we want.
    So here is a Utility for Line Segment Clipping (Liang-Barsky-inspired)
    
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



# -------------------------------------------------------------------------------------------
# --- Clipping and Optimization FUNCTIONS D4. clip_conditional_intersections(all_features ---
# -------------------------------------------------------------------------------------------
def clip_conditional_intersections(all_features):
    """
    Clips intersecting line features based on Z-order/ID precedence, 
    respecting the 'unbreakable' clipper_mode and applying conditional kerf.
    
    Returns a new list of features containing original polygons and new clipped line segments.
    """
    
    # 1. Separate features
    all_line_features = [f for f in all_features if isinstance(f['shape'], sg.LineString)]
    other_features = [f for f in all_features if not isinstance(f['shape'], sg.LineString)]
    
    # Dictionary to map line feature IDs to a list of geometric buffers that must clip them
    clipper_geometries_by_id = {}
    
    # 2. Find Intersections and Determine Clipper/Clippee
    for i in range(len(all_line_features)):
        line_i = all_line_features[i]
        
        for j in range(i + 1, len(all_line_features)):
            line_j = all_line_features[j]
            
            # Check for intersection
            if line_i['shape'].intersects(line_j['shape']):
                
                # Retrieve modes
                mode_i = line_i['style_data']['clipper_mode']
                mode_j = line_j['style_data']['clipper_mode']
                
                # A. Determine Clipper and Clippee based on modes/precedence
                
                # If both are unbreakable, neither clips the other (they cross)
                if mode_i == 'unbreakable' and mode_j == 'unbreakable':
                    continue
                
                # If only one is unbreakable, it is the CLIPPER
                elif mode_i == 'unbreakable':
                    clipper, clippee = line_i, line_j
                elif mode_j == 'unbreakable':
                    clipper, clippee = line_j, line_i
                    
                # If neither is unbreakable, use Z-order/ID precedence (Higher Z/ID wins)
                else:
                    z_i = line_i['style_data']['z-order']
                    id_i = int(line_i['id'].replace('way', '').replace('rel', ''))
                    z_j = line_j['style_data']['z-order']
                    id_j = int(line_j['id'].replace('way', '').replace('rel', ''))
                    
                    is_i_higher = (z_i > z_j) or (z_i == z_j and id_i > id_j)
                    
                    if is_i_higher:
                        clipper, clippee = line_i, line_j
                    else: # j is higher or equal (equal results in j clipping i conservatively)
                        clipper, clippee = line_j, line_i

                # B. Calculate the conditional clipping buffer for the CLIPPER
                
                # Get the CLIPPER's stroke width
                stroke_w_clipper = float(clipper['style_data']['attrs'].get('stroke-width', 1.0))
                
                # Determine the kerf distance based on the clipper's mode
                kerf_mm = KERF_SEPARATION_MM
                if clipper['style_data']['clipper_mode'] == 'unbreakable':
                    # Apply the requested 0.5 mm separation for unbreakable lines
                    kerf_mm = UNBREAKABLE_KERF_MM 
                
                # R_clip (meters) = (Stroke_W / 2) + Kerf (converted to METERS)
                R_clip_m = ((stroke_w_clipper / 2.0) + kerf_mm) / MM_PER_METER
                
                # Create the CLIPPER's buffer (the geometry that cuts the clippee)
                # Use round caps/joins (cap_style=2, join_style=2) for better geometry
                clipper_buffer = clipper['shape'].buffer(R_clip_m, cap_style=2, join_style=2)
                
                # C. Store the buffer against the CLIPPEE's ID
                clippee_id = clippee['id']
                if clippee_id not in clipper_geometries_by_id:
                    clipper_geometries_by_id[clippee_id] = []
                
                clipper_geometries_by_id[clippee_id].append(clipper_buffer)

    # 3. Apply the Clipping (Difference operation)
    processed_line_features = []
    
    for feature in all_line_features:
        original_line = feature['shape']
        feature_id = feature['id']
        
        # --- A. Apply Intersection Clipping ---
        if feature_id in clipper_geometries_by_id:
            # Union all buffers that need to clip this specific line
            clip_union = unary_union(clipper_geometries_by_id[feature_id])
            
            # Apply the difference operation
            clipped_geom = original_line.difference(clip_union)
        else:
            # Line was never a CLIPPEE (it was a CLIPPER or never intersected anything)
            clipped_geom = original_line

        # --- B. Extract Segments and Check for Unbroken Loops (New Logic) ---
        segments = []
        if clipped_geom.geom_type == 'LineString':
            segments.append(clipped_geom)
        elif clipped_geom.geom_type == 'MultiLineString':
            segments.extend(list(clipped_geom.geoms))
        # Note: Polygons are not handled here; they are passed through to Pass 2

        if segments:
            # Min length threshold used to filter out tiny remnants
            min_length = (UNBREAKABLE_KERF_MM * 2) / MM_PER_METER
            
            for k, segment in enumerate(segments):
                if segment.length > min_length: 
                    
                    final_segment = segment
                    
                    # --- Loop Breaking Check:  Break any line segments that create loops  ---
                    # Check if the segment is closed (first point equals last point)
                    is_loop = segment.is_closed
                    
                    if is_loop:
                        # Introduce a tiny cut near the start/end to break the loop.
                        # We use a small, non-zero distance (e.g., 100 microns)
                        CUT_GAP_M = 0.1 / MM_PER_METER 
                        
                        # Create the cutter line (a very short line segment)
                        # Start by finding the first point and a point slightly down the line
                        p1 = segment.interpolate(0.0)
                        p2 = segment.interpolate(CUT_GAP_M)
                        cutter = sg.LineString([p1, p2])
                        
                        # loop breaking
                        try:
                            split_result = split(segment, cutter)
                            # The split will produce a collection; the longest piece is our broken loop
                            if not split_result.is_empty:
                                final_segment = max(spit_result.geoms, key=lambda g: g.length)
                        except Exception as e:
                            pass 
                        
                    # Store the final segment (or the cut segment)
                    segment_id = f"{feature.get('id', feature['tag'])}-{k}"
                    new_feature = {
                        'shape': final_segment,
                        'tag': feature['tag'],
                        'id': segment_id,
                        'base_id': segment_id,
                        **{key: value for key, value in feature.items() if key not in ('shape', 'id', 'base_id')}
                    }
                    processed_line_features.append(new_feature)

        # Lines that were converted to a Polygon during the intersection analysis
        # (e.g., if a line buffer was involved in the difference op, sometimes
        # a Polygon fragment can result, but for pure LineString difference,
        # we expect LineStrings, so this is mainly a safety catch).
        elif clipped_geom.geom_type in ('Polygon', 'MultiPolygon'):
            # These are rare result types from LineString.difference(Polygon),
            # but if they exist, they should be passed to the final polygon processor.
             feature['shape'] = clipped_geom
             processed_line_features.append(feature)
             
        # Line was never a CLIPPEE (it was a CLIPPER or never intersected anything)
        # We need to handle features that were skipped in the loop-breaking logic if they
        # were MultiLineStrings, but the current structure handles all LineString/MultiLineString 
        # results via the segment extraction above. If the original geom was passed through 
        # (meaning it was not in clipper_geometries_by_id), it hits the segment extraction
        # block and is processed.
        # Ensure we only append the non-line features to 'other_features'
        # and handle all line features here.
        # Since we use `clipped_geom` as the basis for segment extraction, all line features 
        # (clipped or not) should be accounted for in `processed_line_features`.
        # This section is removed as it's handled above:
        # else: processed_line_features.append(feature)
        
    # Return all original non-line features + the new line segments
    return other_features + processed_line_features


# -----------------------------------------------------------------------------------------------
# --- Clipping and Optimization FUNCTIONS D5. z_order_clip_and_finalize(all_features, styles) ---
# -----------------------------------------------------------------------------------------------
def z_order_clip_and_finalize(all_features, styles):
    """
    The Final Geometry Manager.
    1. Converts Stroke-lines to Polygons (Handling Multi-part lines).
    2. Calls process_intersections to Union and Guillotines features.
    3. Heals geometry and clips to the map boundary.
    """
    polygon_features = []
    final_geometry_list = []
    safe_zone = sg.box(*CLIP_BBOX)
    
    # --- 1a. BUFFERING: Expand lines into 2D 'Pill' Polygons ---
    for feature in all_features:
        shape = feature['shape']
        tag = feature.get('tag')
        style_info = styles.get(tag, {})
        
        if feature.get('requires_stroke_to_path'):
            stroke_width = float(style_info.get('attrs', {}).get('stroke-width', 1.0))
            
            # Handle Multi-part geometries (MultiLineStrings) from prior clipping
            parts = shape.geoms if hasattr(shape, 'geoms') else [shape]
            buffered_parts = []
            
            for part in parts:
                if part.is_empty: continue
                # Convert specific segment to polygon
                poly_part = convert_stroke_to_path(
                    part.coords,
                    stroke_width,
                    style_info.get('attrs', {}),
                    0.0  # No corner radius here; F3 handles final smoothing
                )
                if poly_part and not poly_part.is_empty:
                    buffered_parts.append(poly_part)
            
            if buffered_parts:
                # Store as single Polygon or MultiPolygon
                feature['shape'] = sg.MultiPolygon(buffered_parts) if len(buffered_parts) > 1 else buffered_parts[0]
                feature['requires_stroke_to_path'] = False
                polygon_features.append(feature)
        else:
            # Already a polygon (Building, Water, etc.)
            polygon_features.append(feature)
            
    # -- 1b. PROCEDURAL GENERATOR for Fairway around greens and semi-rough around fairways --
    # -- This is not defined by map.osm but should be defined in the styles.json on a course by course basis.
    # -- Regardless we need to fairway to fall under green to avoid GAP Errors in Clindar.
    procedural_additions = []
    for feat in polygon_features:
        tag = feat.get('tag')
        style_info = styles.get(tag)
        
        if not style_info:
            continue
            
        # --- THE FIX: Look inside the 'attrs' dictionary ---
        border_m = 0.0
        target_tag = None
        attributes = style_info.get('attrs', {})
        raw_border = attributes.get('grass_border_m')
        target_tag = attributes.get('grass_border_style')
        
        if raw_border is not None:
            try:
                border_m = float(raw_border)
            except ValueError:    
                border_m = 0.0

        if border_m > 0 and target_tag:
            # The Fairway is our reference 'Zero'
            fairway_shape = feat['shape']
            
            # 1. THE OUTER EDGE (Outset)
            # This is the visible part of the semi-rough
            outer_edge = fairway_shape.buffer(border_m * 2, join_style=1)
            
            # 2. THE INNER EDGE (Inset)
            # We move INSIDE the fairway by the same border_m 
            # to create a deep overlap 'tuck'.
#            inner_edge = fairway_shape.buffer(-border_m * 4, join_style=1)
#            inner_edge = fairway_shape.buffer( -15.0, join_style=1)
            
            # 3. THE PICTURE FRAME
            # Subtract the inner hole from the outer shape
            # picture_frame = outer_edge.difference(inner_edge)
            picture_frame = outer_edge
            
            if not picture_frame.is_empty:
                new_feat = {
                    'shape': picture_frame,
                    'tag': target_tag, # golf.semi-rough
                    'id': f"{feat.get('id', 'gen')}_frame",
                    'requires_stroke_to_path': False
                }
                procedural_additions.append(new_feat)
                
    # Add the generated borders to the pool before sorting
    polygon_features.extend(procedural_additions)

    # --- 2. SORTING: Process by Z-Order ---
    sorted_features = sorted(polygon_features, key=lambda f: styles.get(f['tag'], {}).get('z-order', 0))
    
    # --- 3. INTERSECTION ENGINE ---
    print(f"INFO: Calling' process_intersections' on {len(sorted_features)} features for Unions and Guillotines...")
    
    for current_feat in sorted_features:
        tag = current_feat.get('tag', '')
        if tag == "golf.semi-rough":
            new_geom = current_feat['shape'] # Skip clipping, keep the 'Slab'
        else:
            # Resolve peer unions and water-outset guillotines
            new_geom = process_intersections(current_feat, sorted_features, styles)
        
        # --- 4. THE HEALER: Fix topological conflicts (The 'Boss' Fix) ---
        if new_geom and not new_geom.is_empty:
            if not new_geom.is_valid:
                new_geom = new_geom.buffer(0)
            
            # --- 5. BOUNDARY CLIP: Ensure fit within SVG area ---
            try:
                final_shape = new_geom.intersection(safe_zone)
            except Exception:
                # Emergency fallback if intersection still complains
                final_shape = new_geom.buffer(0).intersection(safe_zone)
                
            if final_shape and not final_shape.is_empty:
                current_feat['shape'] = final_shape
                final_geometry_list.append(current_feat)
            
    return final_geometry_list


# -------------------------------------------------------------------------------------------------------------------
# --- Clipping and Optimization FUNCTIONS D6. filter_features_by_spatial_condition(all_features, target_polygons) ---
# -------------------------------------------------------------------------------------------------------------------
def filter_features_by_spatial_condition(all_features, target_polygons):
    """
    Filters or clips features based on their proximity to target zones (e.g., Fairways).
    Uses centroid logic for buildings to prevent partial clipping of structures.
    """
    filtered_features = []
    if not target_polygons: 
        return all_features

    # Create a single unified area of all target polygons (e.g., all fairways/greens)
    union_target = unary_union(target_polygons)
    
    for feature in all_features:
        style_data = feature['style_data']
        shape = feature.get('shape')
        osm_tag = feature.get('osm_tag', '').lower()
        
        # 'distance-from' in styles.json defines the buffer (in meters) around targets
        distance_str = style_data['attrs'].get('distance-from')
        
        # If no spatial rule is defined, pass the feature through normally
        if distance_str is None or shape is None:
            filtered_features.append(feature)
            continue

        dist = float(distance_str)
        # 0 distance = exact overlap; > 0 = creates a surrounding search buffer
        inclusion_zone = union_target if dist == 0 else union_target.buffer(dist)
        
        # CATEGORY 1: Infrastructure (Roads, Water, Paths)
        # These are "Clipped" - we keep the parts that are inside the zone.
        if any(k in osm_tag for k in ['highway', 'aeroway', 'water', 'path']):
            if shape.intersects(inclusion_zone):
                clipped = shape.intersection(inclusion_zone)
                if not clipped.is_empty:
                    feature['shape'] = clipped
                    filtered_features.append(feature)

        # CATEGORY 2: Buildings
        # We use the Centroid check to keep the building "whole" if its center is in range.
        elif 'building' in osm_tag:
            if shape.centroid.intersects(inclusion_zone):
                filtered_features.append(feature)

        # CATEGORY 3: Everything Else (Trees, Bunkers, etc.)
        else:
            if shape.intersects(inclusion_zone):
                filtered_features.append(feature)

    return filtered_features

# --------------------------------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E1. generate_svg_elements(features_to_draw) ---
# --------------------------------------------------------------------------------------
def generate_svg_elements(features_to_draw):
    """
    Converts the final list of Shapely features into SVG string elements, 
    sorted by Z-order for correct drawing order.
    """
    svg_elements = []
    
    # Sort ALL features by Z-order before drawing
    sorted_features = sorted(features_to_draw, key=lambda f: f['style_data']['z-order'])
    
    for feature in sorted_features:
        shape = feature['shape']
        style_data = feature['style_data']
        
        # Use the unified converter for all Shapely geometry types
        d_attr = convert_to_svg_d(shape, precision=4) 
        
        if not d_attr:
            continue
            
        tag = 'path' 
        final_attrs = style_data['attrs'].copy()
        if final_attrs.get('stroke-linecap') == 'smooth':
            final_attrs.pop('stroke-linecap', None)
        
        is_buffered_road = feature.get('requires_stroke_to_path', False)
        # Check if the final geometry is a Polygon (including those created by stroke-to-path)
        if isinstance(shape, (Polygon, MultiPolygon)):
            if is_buffered_road:
                if 'stroke' in final_attrs:
                    final_attrs['fill'] = final_attrs.get('stroke')
                
                final_attrs['stroke'] = 'none'
                final_attrs['stroke-width'] = '0'
                
            if final_attrs.get('fill', 'none') not in ('none', 'transparent'):
                final_attrs['stroke'] = 'none'
                final_attrs['stroke-width'] = '0'

        # Convert the (potentially modified) attributes dictionary back to a string
        attrs = ' '.join(f'{k}="{v}"' for k, v in final_attrs.items())
        
        clean_tag = feature['tag'].replace(':', '-')
        unique_id = feature.get('id', feature.get('base_id', 'f-anon'))
        final_element_id = f"{clean_tag}-{unique_id}"        

        element = f'<{tag} id="{final_element_id}" d="{d_attr}" {attrs} />'
        svg_elements.append(element)
         
    return svg_elements


# ---------------------------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E2. format_coords(coords, precision=4) ---
# ---------------------------------------------------------------------------------
def format_coords(coords, precision=4):
    """
    E2: The Single Source of Truth. 
    Rounds to precision and removes consecutive duplicates.
    """
    clean_points = []
    last_pt = None
    
    for x, y in coords:
        # Rounding here prevents 'precision leaks'
        rx = round(x, precision)
        ry = round(y, precision)
        
        # Format as string, stripping trailing zeros
        curr_pt = f"{rx:g},{ry:g}"
        
        # Only add if it's different from the last point
        if curr_pt != last_pt:
            clean_points.append(curr_pt)
            last_pt = curr_pt
            
    return " ".join(clean_points)


# -----------------------------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E3. convert_to_svg_d(shape, precision=4) ---
# -----------------------------------------------------------------------------------
def convert_to_svg_d(shape, precision=4):
    """E3: Routes all geometry through the E2 filter."""
    if shape.is_empty: return ""

    if shape.geom_type == 'Polygon':
        return _poly_to_d(shape, precision)
    elif shape.geom_type == 'MultiPolygon':
        return " ".join([convert_to_svg_d(p, precision) for p in shape.geoms])
    elif shape.geom_type in ('LineString', 'LinearRing'):
        return _line_to_d(shape, precision)
    elif shape.geom_type == 'MultiLineString':
        return " ".join([_line_to_d(ls, precision) for ls in shape.geoms])
    return ""


# ---------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E4. _line_to_d(line) ---  
# ---------------------------------------------------------------
def _line_to_d(line, precision=4):
    path = f"M {format_coords(line.coords, precision)}"
    return path + " Z" if line.is_closed else path


# ---------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E5. _poly_to_d(poly) ---
# ---------------------------------------------------------------
def _poly_to_d(poly, precision=4):
    # We don't need to manually slice [:-1] anymore 
    # because format_coords will naturally de-duplicate the closing point!
    d = f"M {format_coords(poly.exterior.coords, precision)} Z"
    for interior in poly.interiors:
        d += f" M {format_coords(interior.coords, precision)} Z"
    return d


# --------------------------------------------------------------------------------
# --- SVG File Output FUNCTIONS F1. write_svg_file(header, elements, filename) ---
# --------------------------------------------------------------------------------
def write_svg_file(header, background_elements, foreground_elements, filename):
    """
    E7: Final assembly. 
    Maintains strict layering: Backgrounds first (bottom), then Foregrounds (top).
    """
    svg_content = [header]
    svg_content.append('  <g id="layer-map-features" inkscape:groupmode="layer" inkscape:label="map-features">')
    svg_content.append('    <g id="layer-background-images" inkscape:groupmode="layer" inkscape:label="background.images" inkscape:color="10">')
    if background_elements:
        svg_content.extend(background_elements)
    svg_content.append('  </g>')
    
    if foreground_elements:
        svg_content.extend(foreground_elements)
    svg_content.append('  </g>')
    svg_content.append('</svg>')
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write('\n'.join(svg_content))
    
    print(f"✅ Success: File written with clean hierarchy to: {filename}")    

    
# ---------------------------------------------------------------------------------------------
# --- SVG File Output FUNCTIONS F2. generate_background_svg_elements(background_files, ...) ---
# ---------------------------------------------------------------------------------------------
def generate_background_svg_elements(background_files, svg_width, svg_height, 
                                     MAP_MIN_X, MAP_MAX_Y, REAL_TO_SVG_SCALE,
                                     MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 
                                     METERS_PER_DEGREE_LON_FACTOR, METERS_PER_DEGREE_LAT_FACTOR):
    """
    Reprojects and clips the GeoTIFF to the exact degree-bounds of the map.
    Places the resulting PNG at SVG origin (0,0) without units to match vector scaling.
    Wraps background images names in a group as a named Inkscape Layer.    
    """
    # Start the Group/Layer tag
    svg_elements = []
    
    dst_crs = 'EPSG:4326'
    
    for filename in background_files:
        try:
            # 1. Clean the ID: /path/to/Seneca_hillshade.tif -> Seneca_hillshade
            base_name = os.path.basename(filename)
            clean_id = os.path.splitext(base_name)[0]

            with rasterio.open(filename) as src:
                temp_filename = filename.replace(".tif", "_aligned.png")
                out_width = 2000  
                out_height = int(out_width * (svg_height / svg_width))
                
                dst_transform = rasterio.transform.from_bounds(
                    MIN_LON, MIN_LAT, MAX_LON, MAX_LAT, out_width, out_height
                )
                
                destination = np.zeros((src.count, out_height, out_width), dtype=src.meta['dtype'])
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=destination[i-1],
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=dst_transform,
                        dst_crs=dst_crs,
                        resampling=Resampling.bilinear
                    )

                out_meta = src.meta.copy()
                out_meta.update({
                    "driver": "PNG", "height": out_height, "width": out_width,
                    "transform": dst_transform, "crs": dst_crs, "count": src.count
                })

                with rasterio.open(temp_filename, 'w', **out_meta) as dst:
                    dst.write(destination)

                # --- ALIGNMENT & IDENTITY FIX ---
                # Added the unique ID here
                svg_elements.append(
                    f'    <image id="{clean_id}" x="0.0000" y="0.0000" '
                    f'width="{svg_width:.4f}" height="{svg_height:.4f}" '
                    f'xlink:href="file:///{os.path.abspath(temp_filename)}" '
                    f'preserveAspectRatio="none" />'
                )
                
                print(f"Successfully aligned: {temp_filename} as ID: {clean_id}")

        except Exception as e:
            print(f"Error processing {filename}: {e}")

    return svg_elements

# ---------------------------------------------------------------------------------
# --- SVG File Output FUNCTIONS F3. process_and_write_logic(output_svg, styles) ---
# ---------------------------------------------------------------------------------
def process_and_write_logic(grouped_for_union, styles):
    final_svg_output = []
    # It's kind of odd having UI colors in the .svg but this is where the Inkscape folders get color.
    inkscape_ui_colors = ["#ff0000", "#ff7f00", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#8b00ff", "#ff00ff", "#ffffff"]
    
    sorted_style_keys = sorted(
        grouped_for_union.keys(),
        key=lambda k: styles.get(k, {}).get('z-order', 0)
    )

    WELD_TOLERANCE = 0.0001  # 0.01mm snap for Clindar boundaries

    for i, style_key in enumerate(sorted_style_keys):
        features = grouped_for_union[style_key]
        if not features: continue
        
        processed_shapes = []
        for feat in features:
            shape = feat.get('shape')
            if shape is None or shape.is_empty: continue
            
            # --- BRANCH 1: BUILDINGS (Strict Geometry) ---
            if 'building' in style_key:
                # No smoothing, no fillets. Just a weld to snap to ground.
                shape = shape.buffer(WELD_TOLERANCE)
            
            # --- BRANCH 2: GOLF (Organic Geometry) ---
            elif style_key.startswith("golf."):
                # 1. Simplify to remove 'pointy' vertex clusters (the 'Sanitizer')
                shape = shape.simplify(0.1, preserve_topology=True)
                
                # 2. Apply Chaikin to the simplified base for a smooth lozenge look
                parts = shape.geoms if hasattr(shape, 'geoms') else [shape]
                smoothed_parts = []
                for part in parts:
                    if not part.is_empty and hasattr(part, 'exterior'):
                        ext = chaikin_smooth(list(part.exterior.coords), iterations=3)
                        ints = [chaikin_smooth(list(i.coords), iterations=3) for i in part.interiors]
                        smoothed_parts.append(Polygon(ext, ints))
                
                shape = unary_union(smoothed_parts) if smoothed_parts else shape
                # 3. Final deduplication (Simplify 0) so Clindar doesn't see twin vertices
                shape = shape.simplify(0.001)

            # --- BRANCH 3: ROADS/AIRPORTS (Engineered Fillets) ---
            else:
                # 1. Apply uniform fillet to heal guillotine cuts
                r = styles.get(style_key, {}).get('fillet_radius', FILLET_RADIUS)
                if r > 0:
                    shape = shape.buffer(r, join_style=1).buffer(-r, join_style=1)
                
                # 2. Standard road smoothing
                shape = smooth_geometry(shape, r)
                
                # 3. Weld for watertight intersections
                shape = shape.buffer(WELD_TOLERANCE)
            
            # Final validation pass
            if not shape.is_valid:
                shape = shape.buffer(0)
            processed_shapes.append(shape)

        # --- THE BIG MERGE ---
        try:
            unified_geom = unary_union(processed_shapes)
            unified_geom = unified_geom.buffer(-WELD_TOLERANCE)
            safe_zone = sg.box(*CLIP_BBOX) 
            unified_geom = unified_geom.intersection(safe_zone)
        except Exception as e:
            print(f"DEBUG: Union failed for {style_key}, healing...")
            unified_geom = unary_union([s.buffer(0) for s in processed_shapes])
          
        if unified_geom.is_empty: continue

        # --- SVG OUTPUT ---
        hex_color = inkscape_ui_colors[i % len(inkscape_ui_colors)]
        style_info = styles.get(style_key, {})
        safe_style_name = style_key.replace(".", "-")
        
        final_svg_output.append(
            f'  <g id="layer-{safe_style_name}" '
            f'inkscape:groupmode="layer" inkscape:label="{style_key}" '
            f'inkscape:highlight-color="{hex_color}">'
        )

        parts = unified_geom.geoms if hasattr(unified_geom, 'geoms') else [unified_geom]
        attrs = dict(style_info.get('attrs', {}))
        if style_info.get('stroke_to_path', False):
            attrs['stroke-width'] = "0"
            if attrs.get('fill') == 'none' or 'fill' not in attrs:
                attrs['fill'] = attrs.get('stroke', '#000000')
        
        attr_str = ' '.join([f'{k}="{v}"' for k, v in attrs.items()])

        for idx, part in enumerate(parts):
            # Precision Snap to 5 decimals to kill the last of the micro-noise
            d_path = convert_to_svg_d(part, precision=4)
            if d_path:
                path_id = f"{safe_style_name}-island-{idx}"
                final_svg_output.append(f'    <path id="{path_id}" {attr_str} d="{d_path}" />')

        final_svg_output.append('  </g>')

    return final_svg_output

# --------------------------------------------------------------------------------------
# --- Geometry Series: FUNCTIONS G1. process_multipolygon_relation(relation, styles) ---
# --------------------------------------------------------------------------------------
def process_multipolygon_relation(relation, styles):
    """
    Handles OSM Multipolygons (Relations) and converts them into 
    Shapely MultiPolygons with 'inner' (holes) and 'outer' rings.
    """
    inner_ways = []
    outer_ways = []
    rel_tags = {tag.get('k'): tag.get('v') for tag in relation.findall('tag')}
    
    # 1. Style Match for the Relation
    style_data = None
    feature_tag = None
    for k, v in rel_tags.items():
        searchtag = f"{k}.{v}"
        if searchtag in styles:
            style_data, feature_tag = styles[searchtag], searchtag
            break
            
    if not style_data: return []

    # 2. Sort Members
    for member in relation.findall('member'):
        ref = member.get('ref')
        role = member.get('role')
        if ref in ways:
            coords = get_way_coordinates(ref)
            if len(coords) < 3: continue
            if role == 'inner': inner_ways.append(Polygon(coords))
            else: outer_ways.append(Polygon(coords))

    if not outer_ways: return []

    # 3. Create the Shell and subtract the Holes
    try:
        combined_outer = unary_union(outer_ways)
        combined_inner = unary_union(inner_ways)
        final_geom = combined_outer.difference(combined_inner)
        
        # Clip to safety box immediately (Area feature)
        final_geom = final_geom.intersection(sg.box(*CLIP_BBOX))
        
        if final_geom.is_empty: return []

        return [{
            'shape': final_geom,
            'tag': feature_tag,
            'id': relation.get('id'),
            'osm_tag': feature_tag.split('.')[0],
            'style_data': style_data,
            'requires_line_clip': False,
            'requires_stroke_to_path': style_data.get('stroke_to_path', False)
        }]
    except Exception as e:
        print(f"Error processing relation {relation.get('id')}: {e}")
        return []

# ------------------------------------------------------------------------------------------------------------
# --- Geometry Series: FUNCTIONS G2. guillotine_with_outset(target_poly, unbreakable_poly, outset_mm=1.0)  ---
# ------------------------------------------------------------------------------------------------------------
def guillotine_with_outset(target_poly, unbreakable_poly, outset_mm=1.0):
    """
    Cuts the target_poly using the unbreakable_poly, 
    but adds a safety gap (outset) first.
    """
    guillotine_cutter = unbreakable_poly.buffer(outset_mm, join_style=2)
    result = target_poly.difference(guillotine_cutter)
    return result


# --------------------------------------------------------------------------------------------------------------
# --- Geometry Series: FUNCTIONS G3.  get_safe_radius(point_prev, point_curr, point_next, requested_radius)  ---
# --------------------------------------------------------------------------------------------------------------
def get_safe_radius(point_prev, point_curr, point_next, requested_radius):
    """
    Calculates a 'clamped' radius to prevent geometry spikes.
    """
    # Calculate segment lengths
    dist1 = Point(point_curr).distance(Point(point_prev))
    dist2 = Point(point_curr).distance(Point(point_next))
    
    # The max safe radius is roughly half the shortest adjacent segment
    # We use 0.4 to leave a tiny bit of 'flat' space
    max_safe = min(dist1, dist2) * 0.4
    
    return min(requested_radius, max_safe)


# ----------------------------------------------------------------------------
# --- Geometry Series: FUNCTIONS G4. chaikin_smooth(points, iterations=2)  ---
# ----------------------------------------------------------------------------
def chaikin_smooth(points, iterations=2):
    """
    Applies Chaikin's corner-cutting algorithm to create organic,
    flowing shapes from rough polygons.
    """
    if len(points) < 3:
        return points
        
    for _ in range(iterations):
        new_points = []
        # Handle closed loops (polygons)
        is_closed = (points[0] == points[-1])
        
        for i in range(len(points) - 1):
            p0 = points[i]
            p1 = points[i+1]
            
            # Create two new points at 25% and 75% of each segment
            fa = [p0[0] * 0.75 + p1[0] * 0.25, p0[1] * 0.75 + p1[1] * 0.25]
            fb = [p0[0] * 0.25 + p1[0] * 0.75, p0[1] * 0.25 + p1[1] * 0.75]
            
            new_points.extend([tuple(fa), tuple(fb)])
            
        if is_closed:
            new_points.append(new_points[0])
        else:
            # For open paths, keep the original start and end points
            new_points = [points[0]] + new_points + [points[-1]]
            
        points = new_points
        
    return points


# --------------------------------------------------------------------------------------------------
# --- Geometry Series: FUNCTIONS G5. process_intersections(current_feat, other_features, styles) ---
# --------------------------------------------------------------------------------------------------
def process_intersections(current_feat, other_features, styles):
    """
    Handles Unioning and Guillotining based on 'Z-order' and 'clipper_mode'.
    Uses 'tag' key to match feature dictionaries.
    """
    geom = current_feat['shape']
    # Safety Check: Ensure the starting geometry is valid
    if not geom.is_valid:
        geom = geom.buffer(0)
        
    style_key = current_feat.get('tag')
    current_style = styles.get(style_key, {})
    current_z = current_style.get('z-order', 0)

    for other in other_features:
        if current_feat['id'] == other['id']: continue
        
        other_shape = other['shape']
        # Ensure 'other' is valid before comparing
        if not other_shape.is_valid:
            other_shape = other_shape.buffer(0)
            
        other_style_key = other.get('tag')
        other_style = styles.get(other_style_key, {})
        other_z = other_style.get('z-order', 0)
        other_mode = other_style.get('clipper_mode', 'default')

        # --- 1. PEER UNION ---
        is_aeroway = style_key.startswith('aeroway') and other_style_key.startswith('aeroway')
        is_highway = style_key.startswith('highway') and other_style_key.startswith('highway')
        
        if current_z == other_z and (style_key == other_style_key or is_aeroway or is_highway):
            if geom.intersects(other_shape):
                try:
                    geom = geom.union(other_shape)
                except Exception:
                    # Fallback for the TopologyException
                    geom = geom.buffer(0).union(other_shape.buffer(0))
                
                if "runway" in other_style_key:
                    current_feat['tag'] = "aeroway.runway"
                    style_key = "aeroway.runway"
                continue 

        # --- 2. THE GUILLOTINE (ie. Who's on top) ---
        # Trigger if: 'other' is higher Z OR 'other' is unbreakable
        if other_z > current_z or other_mode == "unbreakable":
            
            # Don't let roads cut the ground (leisure/golf areas), only other paths/roads
            if not style_key.startswith('leisure') and not style_key.startswith('golf.fairway'):
                
                is_water = 'water' in other_style_key
                is_unbreakable = (other_mode == "unbreakable")
                
                # --- THE A+ LOGIC UPDATE ---
                # 1. Is it water? (1.0mm gap)
                # 2. Is it unbreakable? (0.33mm gap)
                # 3. Is it just HIGHER than me? (0.33mm gap)
                is_higher_priority = (other_z > current_z)

                if (is_water or is_unbreakable or is_higher_priority) and geom.intersects(other_shape):
                    outset_val = 1.0 if is_water else 0.33
                    try:
                        # This will now catch Cartpaths (45) vs Residential (50)
                        geom = guillotine_with_outset(geom, other_shape, outset_mm=outset_val)
                    except Exception:
                        geom = geom.buffer(0)
                        geom = guillotine_with_outset(geom, other_shape.buffer(0), outset_mm=outset_val)
    return geom

# --------------------------------------------------------------------------------
# --- Geometry Series: FUNCTIONS G6. smooth_geometry(shape, requested_radius)  ---
# --------------------------------------------------------------------------------
def smooth_geometry(shape, requested_radius):
    """
    Intelligently smooths a polygon by calculating safe fillets 
    for every corner to prevent geometric spikes.
    """
    if shape is None or shape.is_empty:
        return shape

    # Handle MultiPolygons by processing each part
    if hasattr(shape, 'geoms'):
        return MultiPolygon([smooth_geometry(g, requested_radius) for g in shape.geoms])

    # 1. Extract the exterior points
    coords = list(shape.exterior.coords)
    if coords[0] == coords[-1]:
        coords = coords[:-1]  # Work with unique vertices
    
    new_points = []
    n = len(coords)
    
    for i in range(n):
        p_prev = coords[i - 1]
        p_curr = coords[i]
        p_next = coords[(i + 1) % n]
        
        # 2. Apply G3 Spike Insurance
        # This calculates the maximum possible radius for this specific corner
        safe_r = get_safe_radius(p_prev, p_curr, p_next, requested_radius)
        
        # 3. Generate the Fillet Arc
        # If safe_r is near zero, this just returns the corner point
        corner_arc = generate_fillet_arc(p_prev, p_curr, p_next, safe_r)
        new_points.extend(corner_arc)
        
    # 4. Close the polygon and handle potential interiors (holes)
    if len(new_points) < 3:
        return shape
        
    new_points.append(new_points[0])
    smoothed_poly = Polygon(new_points)
    
    # Process holes if they exist using the same logic
    if shape.interiors:
        new_interiors = []
        for interior in shape.interiors:
            # We treat interiors as individual linear rings
            int_coords = list(interior.coords)
            # (Simplified: apply smoothing or keep as-is)
            new_interiors.append(int_coords) 
        return Polygon(smoothed_poly.exterior, new_interiors)

    return smoothed_poly


# ------------------------------------------------------------------------------------------
# --- Geometry Series: FUNCTIONS G7. generate_fillet_arc(p1, p2, p3, radius, segments=5) ---
# ------------------------------------------------------------------------------------------
def generate_fillet_arc(p1, p2, p3, radius, segments=5):
    """
    Generates a list of points forming an arc to fillet the corner at p2.
    If radius is near zero, simply returns the point p2.
    """
    if radius < 0.0001:
        return [p2]

    # Convert to numpy vectors for easier math
    A = np.array(p1)
    B = np.array(p2) # The corner vertex
    C = np.array(p3)

    # 1. Vectors for the two legs
    v1 = A - B
    v2 = C - B
    
    v1_len = np.linalg.norm(v1)
    v2_len = np.linalg.norm(v2)
    
    # Normalize
    u1 = v1 / v1_len
    u2 = v2 / v2_len

    # 2. Find the angle between the legs
    # Angle at B = arccos(u1 dot u2)
    dot = np.clip(np.dot(u1, u2), -1.0, 1.0)
    angle = np.arccos(dot)
    
    # Half-angle for tangent calculation
    half_angle = angle / 2.0
    
    # 3. Distance from corner (B) to the start/end of the arc (tangency points)
    # dist = radius / tan(half_angle)
    # Note: If angle is very sharp, dist becomes large (G3 prevents this!)
    dist_to_tangent = radius / np.tan(half_angle)

    # 4. Locate tangent start and end points
    start_point = B + u1 * dist_to_tangent
    end_point = B + u2 * dist_to_tangent

    # 5. Find the Center of the Circle for the arc
    # The center is dist_to_center away from B along the angle bisector
    bisector = (u1 + u2)
    bisector_norm = np.linalg.norm(bisector)
    if bisector_norm < 1e-6:
        return [B]
    
    bisector = bisector / bisector_norm
    dist_to_center = radius / np.sin(half_angle)
    center = B + bisector * dist_to_center

    # 6. Generate arc points
    # Find start and end angles relative to the center
    v_start = start_point - center
    v_end = end_point - center
    
    start_angle = np.arctan2(v_start[1], v_start[0])
    end_angle = np.arctan2(v_end[1], v_end[0])

    # Ensure we take the shorter path around the circle
    if end_angle - start_angle > np.pi:
        end_angle -= 2 * np.pi
    elif end_angle - start_angle < -np.pi:
        end_angle += 2 * np.pi

    # Create the interpolated points
    arc_points = []
    for i in range(segments + 1):
        theta = start_angle + (end_angle - start_angle) * (i / segments)
        px = center[0] + radius * np.cos(theta)
        py = center[1] + radius * np.sin(theta)
        arc_points.append((float(px), float(py)))

    return arc_points


#-------------------------------------
# --- The MAIN FUNCTION M1. main() ---
# ------------------------------------
def main():
    """The main function to execute the conversion process."""

    global nodes, ways, outputFile, CLIP_BBOX, SVG_WIDTH_MM, SVG_HEIGHT_MM, SAFETY_INSET_MM
    
    args = parse_arguments()
    
    inputFile = args.infile
    outputFile = args.outfile
    styleFile = args.styles
    
    # Collect background image filenames
    background_files = [
        args.background1,
        args.background2,
        args.background3,
        args.background4
    ]
    
    # Filter out None values
    background_files = [f for f in background_files if f is not None]

    # Load and Parse Style File
    styleDef = {}
    try:
        clean_json_lines = []
        with open(styleFile, 'r') as f:
            for line in f:
                stripped_line = line.strip()
                if stripped_line.startswith('//') or stripped_line.startswith('#') or stripped_line.startswith('"COMMENT"'):
                    continue
                if stripped_line:
                    clean_json_lines.append(line)
        json_string = "".join(clean_json_lines)
        styleDef = json.loads(json_string)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error loading style file '{styleFile}': {e}")
        sys.exit(1)

    # Process Styles
    styles = {}
    for tag, attrs in styleDef.items():
        stroke_to_path = attrs.pop('stroke_to_path', False)
        if isinstance(stroke_to_path, str):
            stroke_to_path = (stroke_to_path.lower() == 'true')
            
        attrs.pop('corner_radius', None)
        try:
            z_order = int(attrs.pop('z-order', 0))
        except ValueError:
            print(f"Warning: Invalid z-order value for {tag}. Using 0.")
            z_order = 0
            
        clipper_mode = attrs.pop('clipper_mode', 'default').lower()    
          
        svg_style_string = ' '.join([f'{k}="{v}"' for k, v in attrs.items()])
        
        styles[tag] = {
            'svg_style': svg_style_string,
            'stroke_to_path': stroke_to_path,
            'z-order': z_order,
            'clipper_mode': clipper_mode,
            'attrs': attrs
        }
        
    # --- Load OSM File and Calculate Projection ---
    try:
        tree = ET.parse(inputFile)
        document = tree.getroot()
    except (FileNotFoundError, ET.ParseError) as e:
        print(f"Error loading input file '{inputFile}': {e}")
        sys.exit(1)

    # Get Bounds and Calculate SVG Header/Projection
    boundsElems = document.findall('bounds')
    if len(boundsElems) != 1:
        print("Expected exactly one <bounds/> element. Something is weird.")
        sys.exit(1)

    b = boundsElems[0]
    minlat, maxlat = float(b.get('minlat')), float(b.get('maxlat'))
    minlon, maxlon = float(b.get('minlon')), float(b.get('maxlon'))
        
    svg_header, map_width_m, map_height_m, REAL_TO_SVG_SCALE = generate_svg_header_from_bounds(minlat, maxlat, minlon, maxlon)
        
    # Define the bounding box for clipping (using calculated SVG size - SAFETY_INSET_MM) 
    CLIP_BBOX = (SAFETY_INSET_MM, SAFETY_INSET_MM, SVG_WIDTH_MM - SAFETY_INSET_MM, SVG_HEIGHT_MM - SAFETY_INSET_MM)

    # Load nodes and ways into memory
    nodes = {node.get('id'): (float(node.get('lon')), float(node.get('lat')))
             for node in document.iter('node')}

    ways = {}
    for way in document.findall('way'):
         way_id = way.get('id')
         node_refs = [noderef.get('ref') for noderef in way.iter('nd')]
         way_tags = {tag.get('k'): tag.get('v') for tag in way.iter('tag')}
    
         ways[way_id] = {
             'refs': node_refs,
             'tags': way_tags
         }
         
    # --------------------------------------------------------------------------------
    # --- PASS 1 P1: Feature Collection (Ways & Relations) - Collect Shapely Objects ---
    # --------------------------------------------------------------------------------
    all_shapely_features = []
    golf_course_polygons = [] # For handling buildings
    
    for way_id, way_data in ways.items():
        way_tags = way_data['tags']
        style_data = None
        feature_tag = None
        
        # 1. Style Matching
        for tag_key, tag_value in way_tags.items():
            searchtag = f"{tag_key}.{tag_value}"
            if searchtag in styles:
                style_data = styles[searchtag]
                feature_tag = searchtag
                break
            
        if not style_data:
            for tag_key in way_tags.keys():
                if tag_key in styles:
                    style_data = styles[tag_key]
                    feature_tag = tag_key
                    break
  
        if style_data is None: continue
        # 2. Geometry Retrieval
        way_coords_unclipped = get_way_coordinates(way_id)
        if len(way_coords_unclipped) < 2: continue
        
        current_line_string = sg.LineString(way_coords_unclipped)
        
        # Determine Feature Type
        is_stroke_to_path = style_data.get('stroke_to_path', False)
        stroke_width = float(style_data['attrs'].get('stroke-width', 0.0))
        is_line_feature = is_stroke_to_path or (stroke_width > 0.0)
        is_closed_way = (way_coords_unclipped[0] == way_coords_unclipped[-1])

        current_shape = None
        requires_line_clip = False

        # --- CASE A: LINE FEATURES ---
        if is_line_feature:
            current_shape = current_line_string  
            requires_line_clip = True

        # --- CASE B: AREA FEATURES ---
        elif is_closed_way:
            try:
                # Polygons get immediate safety clipping
                if len(way_coords_unclipped) >= 3:
                    current_shape = sg.Polygon(way_coords_unclipped)
            except Exception as e:
                print(f"DEBUG: Failed to create polygon for way {way_id}: {e}")
                continue
        
        if current_shape and not current_shape.is_empty:
            final_osm_tag = feature_tag.split('.')[0] # e.g. 'highway'
            
            # Identify Golf Boundaries for D6
            if 'golf_course' in feature_tag or way_tags.get('leisure') == 'golf_course':
                golf_course_polygons.append(current_shape)

            all_shapely_features.append({
                'shape': current_shape,
                'tag': feature_tag,
                'id': way_id,
                'osm_tag': final_osm_tag,
                'style_data': style_data,
                'requires_line_clip': requires_line_clip,
                'requires_stroke_to_path': is_stroke_to_path   
            })
            
    # Filter out any non-dictionary objects (like stray strings or comments)
    cleaned_shapely_features = [f for f in all_shapely_features if isinstance(f, dict)]
    
    # Calculate global constants based on max feature size
    calculate_derived_clipping_constants(cleaned_shapely_features) 
    
    # Re-assign or use the cleaned list for the rest of the script
    all_shapely_features = cleaned_shapely_features
 
    # Process Relations (MultiPolygons)
    for relation in document.iter('relation'):
        is_multipolygon = False
        for tag in relation.findall('tag'):
            if tag.get('k') == 'type' and tag.get('v') == 'multipolygon':
                is_multipolygon = True
                break
        
        if is_multipolygon:
            relation_features = process_multipolygon_relation(relation, styles)  
            if relation_features:
                for feature in relation_features:
                    if not isinstance(feature, dict):
                        print(f"Warning: Skipping non-dict feature in relation processing: {feature}")
                        continue
                    if feature.get('tag') == 'leisure.golf_course': 
                        if isinstance(feature.get('shape'), (sg.Polygon, sg.MultiPolygon)):
                            golf_course_polygons.append(feature['shape'])
                            
                    all_shapely_features.append(feature)
                    
    print(f"\nDEBUG: Total features collected in Pass 1: {len(all_shapely_features)}")
    all_shapely_features = [f for f in all_shapely_features if isinstance(f, dict)]
    print(f"DEBUG: Features remaining after cleanup: {len(all_shapely_features)}")
    calculate_derived_clipping_constants(all_shapely_features)
    
    # ------------------------------------------------
    # --- CONDITIONAL FEATURE FILTERING (New Step) ---
    # ------------------------------------------------
    if golf_course_polygons:
        # Count how many features D6 is *intended* to process
        building_count_before_filter = sum(1 for f in all_shapely_features if f.get('osm_tag') == 'building' )
        print(f"DEBUG: Found {building_count_before_filter} features explicitly tagged as 'building' for filtering.")

        print(f"\nINFO: Applying spatial filtering to buildings based on {len(golf_course_polygons)} golf course areas...")
        
        # Identify how many roads/highways have a distance-from style
        road_filter_count = sum(1 for f in all_shapely_features 
                            if ('highway' in f.get('osm_tag', '') or 'road' in f.get('osm_tag', '')) 
                            and 'distance-from' in f['style_data']['attrs'])
        print(f"DEBUG: Found {road_filter_count} road features with distance-from constraints.")
        # NOTE: This new function D6 needs to be defined
        all_shapely_features = filter_features_by_spatial_condition(
            all_shapely_features, 
            golf_course_polygons
        )
        print(f"DEBUG: Features remaining after filtering: {len(all_shapely_features)}")
        
    # ---------------------------------------------------------------------------
    # --- PASS 2 P2: Line Clipping, Z-Order Cleaning, and Final Geometry Prep ---
    # ---------------------------------------------------------------------------
    
    # 1. Line Intersection Clipping (Z-ORDER PRECEDENCE LOGIC)
    # This step breaks lower-Z lines where they intersect higher-Z lines.
    # It returns a list of all features, where clipped lines are split into segments.
    print(f"\nINFO: Applying Z-order precedence clipping to {len([f for f in all_shapely_features if f.get('requires_line_clip')])} linear features...")
   
    all_features_after_line_clip = clip_conditional_intersections(all_shapely_features)
    
    # 2. Final Geometry Preparation and Z-Order Polygon Clipping
    # This function handles the final steps:
    # a) Converts 'requires_stroke_to_path' lines into polygons (buffering).
    # b) Performs the Polygon-vs-Polygon Z-order clipping (higher-Z removing lower-Z).
    # c) Applies simplification/smoothing.
    
    print("INFO: Applying final Z-order polygon clipping and stroke-to-path conversion...")
    final_features_to_draw = z_order_clip_and_finalize(all_features_after_line_clip, styles)

    print(f"INFO: Final feature count after Z-order processing: {len(final_features_to_draw)}")
    
    # --------------------------------------------------------------------------
    # --- PASS 3 P3: Generate SVG, Sort and Write to File (New, Streamlined) ---
    # --------------------------------------------------------------------------
    background_svg_elements = generate_background_svg_elements(
        background_files, SVG_WIDTH_MM, SVG_HEIGHT_MM,
        MAP_MIN_X, MAP_MAX_Y, REAL_TO_SVG_SCALE,
        MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 
        METERS_PER_DEGREE_LON_FACTOR, METERS_PER_DEGREE_LAT_FACTOR
    )
    background_svg_elements.reverse()

    # 2. GROUPING FOR UNION (Standard logic)
    grouped_for_union = {}
    for feature in final_features_to_draw:
        tag = feature['tag']
        if tag not in grouped_for_union:
            grouped_for_union[tag] = []
        grouped_for_union[tag].append(feature)

    # 3. Generate the vector features (using F3)
    # These are the path groups (grass, roads, etc.)
    print("INFO: Unioning similar features and isolating individual paths...")
    svg_features = process_and_write_logic(grouped_for_union, styles)

    # 4. FINAL WRITE: Pass the two separate lists to F1
    try:
        write_svg_file(svg_header, background_svg_elements, svg_features, outputFile)
        # We use background_svg_elements here because F1 handles the grouping for us!
        
        print(f"\n✅ SUCCESS: SVG file generated successfully: {outputFile}")

    except Exception as e:
        print(f"❌ ERROR: Failed to write SVG file: {e}")
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
        
if __name__ == "__main__":
    main()
    
