#!/usr/bin/env python3
"""
osm2svg_v7.py takes map.osm and coverts it to out.svg with parameters set at the command-line and specified in styles.json.
This version was updated to handle the the map.osm from the Overpass API as well as export from OpenStreetMap.org  This version also scales and clips map.osm features to the the coordinates specified in the map.osm download.
Additionally, it sets the Default SVG scale to be 1 meter = 1 mm of SVG units per OPCD recomendations.
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
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import unary_union
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

FILLET_RADIUS = 0.05  # radius used at corners of paths.
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
CLIP_MARGIN_MM = 0.0 # Will hold the kerf separation in MM (0.05)
MAP_MIN_X = 0.0      # Projected X-coordinate of the map's left edge (in meters)
MAP_MAX_Y = 0.0      # Projected Y-coordinate of the map's top edge (in meters)
REAL_TO_SVG_SCALE = 0.0 # The final calculated scale factor (mm/meter)
MAP_WIDTH_M = 0.0    # Projected Width in Meters
MAP_HEIGHT_M = 0.0   # Projected Height in Meters

# Global containers for parsed data (used in main and helper functions)
nodes = {}
ways = {}


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
    
    header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" \n'
        f'    xmlns:xlink="http://www.w3.org/1999/xlink" \n'
        f'    width="{width_mm_val}mm" \n'
        f'    height="{height_mm_val}mm" \n'
        f'    viewBox="{viewbox}" \n' # This is the critical change
        f'    version="1.1">\n'
    )
    
    # Many of necessary globals are already set by A2.
    return header, map_width_m, map_height_m, REAL_TO_SVG_SCALE    


# ---------------------------------------------------------------------------------
# --- Coordinate & Geometry Transform FUNCTIONS B1. lon_lat_to_svg_xy(lon, lat) ---
# ---------------------------------------------------------------------------------
def lon_lat_to_svg_xy(lon, lat):
    """
    Transforms a single (lon, lat) coordinate into SVG (x, y) coordinates (in mm).
    """
    global MIN_LON, MIN_LAT, METERS_PER_DEGREE_LON_FACTOR, METERS_PER_DEGREE_LAT_FACTOR
    global MAP_HEIGHT_M, REAL_TO_SVG_SCALE # Need MAP_HEIGHT_M for the Y-flip, and REAL_TO_SVG_SCALE for the mm conversion.

    # 1. Calculate meter offsets (from MIN_LON, MIN_LAT)
    lon_offset_deg = lon - MIN_LON
    lat_offset_deg = lat - MIN_LAT
    
    x_offset_m = lon_offset_deg * METERS_PER_DEGREE_LON_FACTOR
    y_offset_m = lat_offset_deg * METERS_PER_DEGREE_LAT_FACTOR

    # 2. Perform the Y-Flip (y_dist_from_top_m)
    # The map height in meters is MAP_HEIGHT_M (Global, from A2).
    # The Y-flip is total map height in meters MINUS the offset from the bottom in meters.
    y_dist_from_top_m = MAP_HEIGHT_M - y_offset_m
    
    # 3. Final Conversion to SVG units (MM) using the map scale factor.
    x_mm = x_offset_m * REAL_TO_SVG_SCALE
    y_mm = y_dist_from_top_m * REAL_TO_SVG_SCALE # Use REAL_TO_SVG_SCALE

    return x_mm, y_mm


# -----------------------------------------------------------------------------------
# --- Coordinate & Geometry Transform FUNCTIONS B2. format_coords_for_svg(coords) ---
# -----------------------------------------------------------------------------------
def format_coords_for_svg(coords):
    """
    Converts Shapely coordinates (in projected meters) to SVG coordinates (in millimeters).
    """
    global REAL_TO_SVG_SCALE, MAP_HEIGHT_M # Need MAP_HEIGHT_M for Y-flip!

    if not REAL_TO_SVG_SCALE:
        raise ValueError("REAL_TO_SVG_SCALE is not set. Cannot convert coordinates to SVG units.")
        
    formatted_coords = []
    
    for x_m, y_m in coords:
        # 1. Perform the Y-Flip (Meter to Meter)
        # y_m_flipped = MAP_HEIGHT_M - y_m  (FLIP Y here).
        
        # 2. Convert to MM (The scale fix)
        x_mm = x_m * REAL_TO_SVG_SCALE
        y_mm = y_m * REAL_TO_SVG_SCALE 

        # Format the coordinates
        formatted_coords.append(f"{x_mm:.4f},{y_mm:.4f}")
            
    return formatted_coords


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
                    x, y = lon_lat_to_svg_xy(lon, lat)
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
    
    if not all_coords:
        return ""

    if return_coords_only:
        return all_coords

    path_d = f"M {all_coords[0][0]:.4f},{all_coords[0][1]:.4f}"
    
    for x, y in all_coords[1:]:
        path_d += f" L {x:.4f},{y:.4f}"
        
    # If the first and last point are the same, close the path (crucial for valid polygons)
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
    
    # Call C2. join_ways_into_path_d which will return a joined list of coordinates for Shapely
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


# -----------------------------------------------------------------------------------------------
# --- OSM Data & Geometry Processing FUNCTIONS C4. smooth_corners_by_buffer(geometry, radius) ---
# -----------------------------------------------------------------------------------------------
def smooth_corners_by_buffer(geometry, radius):
    """
    Applies a small negative buffer followed by a positive buffer to round
    corners and simplify geometry while maintaining a tight boundary.
    This acts as a fillet operation.
    """
    if geometry.is_empty:
        return geometry
    try:
        # Join_Style 1 = ROUND. This is the magic ingredient for filleting.
        # We buffer OUT first to create the rounded "shoulders"
        smoothed = geometry.buffer(radius, join_style=1, quad_segs=8)
        smoothed = smoothed.buffer(-radius, join_style=1, quad_segs=8)
        # Then buffer IN to bring the edges back to their original position
        return smoothed

    except Exception as e:
        print(f"Warning: Corner smoothing failed: {e}")
        return geometry # Return original geometry if operation fails


# -----------------------------------------------------------------------------------------------------------------
# -- OSM Data & Geometry Processing FUNCTIONS C5. convert_stroke_to_path(way_coords, stroke_width, attrs, radius) -
# -----------------------------------------------------------------------------------------------------------------
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
                        
                        # Use the cut method from Shapely to slice the loop
                        try:
                            # The result of cut is a MultiLineString if the cut succeeds
                            cut_result = cut(segment, cutter)
                            if cut_result and not cut_result.is_empty:
                                # We want the segment that represents the rest of the loop
                                # after the cut. We take the largest resulting LineString.
                                if cut_result.geom_type == 'MultiLineString':
                                    final_segment = max(cut_result.geoms, key=lambda g: g.length)
                                else:
                                    final_segment = cut_result
                                
                        except Exception as e:
                            # print(f"Warning: Failed to cut loop for Way {feature_id}: {e}")
                            pass # Keep original segment if cut fails
                        
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


# ---------------------------------------------------------------------------------------
# --- Clipping and Optimization FUNCTIONS D5. z_order_clip_and_finalize(all_features) ---
# ---------------------------------------------------------------------------------------
def z_order_clip_and_finalize(all_features):
    final_features_to_draw = []
    polygon_features_for_zorder = []
    
    # 1. PROCESS: Buffering (Lines to Polygons)
    for feature in all_features:
        shape = feature['shape']
        style_data = feature['style_data']
        
        if isinstance(shape, sg.LineString) and feature.get('requires_stroke_to_path'):
            stroke_width = float(style_data['attrs'].get('stroke-width', 1.0))
            # Buffering happens here - this creates the 'pill' shape
            current_poly = convert_stroke_to_path(
                shape.coords,
                stroke_width,
                style_data['attrs'],
                0.0 
            )
            if current_poly and not current_poly.is_empty:
                feature['shape'] = current_poly  
                polygon_features_for_zorder.append(feature)
        elif isinstance(shape, (sg.Polygon, sg.MultiPolygon)):
            polygon_features_for_zorder.append(feature)
        elif isinstance(shape, sg.LineString):
            final_features_to_draw.append(feature)

    # 2. PROCESS: Z-Order Precedence Clipping
    # (This ensures higher-Z objects cut holes in lower-Z objects)
    sorted_polygons = sorted(polygon_features_for_zorder, key=lambda f: f['style_data']['z-order'])
    clip_margin = CLIP_MARGIN_MM / MM_PER_METER 
    
    processed_polygons = []
    for current_feature in sorted_polygons:
        current_poly = current_feature['shape']
        current_z = current_feature['style_data']['z-order']
        
        for prev_feature in processed_polygons:
            prev_z = prev_feature['style_data']['z-order']
            if prev_z > current_z:
                clip_shape = prev_feature['shape']
                if not clip_shape.is_empty and current_poly.intersects(clip_shape):
                    try:
                        buffered_clip_shape = clip_shape.buffer(clip_margin, join_style=2)
                        current_poly = current_poly.difference(buffered_clip_shape)
                    except:
                        current_poly = current_poly.difference(clip_shape)
        
        # 3. PROCESS: Smoothing (Filleting)
        # CRITICAL: We smooth the corners AFTER z-clipping but BEFORE the boundary cut
        if not current_poly.is_empty:
            if FILLET_RADIUS > 0:
                current_poly = smooth_corners_by_buffer(current_poly, FILLET_RADIUS)
            if SIMPLIFY_TOLERANCE > 0:
                current_poly = current_poly.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
            
            current_feature['shape'] = current_poly
            processed_polygons.append(current_feature)

    # 4. FINAL STEP: The "Guillotine" (Boundary Clip)
    # We combine everything and chop it at the 8mm line
    final_features_to_draw.extend(processed_polygons)
    safe_zone = sg.box(*CLIP_BBOX)
    
    final_output = []
    for feature in final_features_to_draw:
        if feature['shape'] and not feature['shape'].is_empty:
            # This 'shaves' the rounded ends exactly at the safety line
            feature['shape'] = feature['shape'].intersection(safe_zone)
            if not feature['shape'].is_empty:
                final_output.append(feature)
            
    return final_output


# -----------------------------------------------------------------------------------------------------------------
# -- Clipping and Optimization FUNCTIONS D6. filter_features_by_spatial_condition(all_features, target_polygons) --
# -----------------------------------------------------------------------------------------------------------------
def filter_features_by_spatial_condition(all_features, target_polygons):
    filtered_features = []
    if not target_polygons: return all_features
    union_target = unary_union(target_polygons)
    
    for feature in all_features:
        style_data = feature['style_data']
        shape = feature.get('shape')
        osm_tag = feature.get('osm_tag', '').lower()
        distance_str = style_data['attrs'].get('distance-from')
        
        if distance_str is None or shape is None:
            filtered_features.append(feature)
            continue

        dist = float(distance_str)
        inclusion_zone = union_target if dist == 0 else union_target.buffer(dist)
        
        # CLIP ROADS, FILTER BUILDINGS
        if any(k in osm_tag for k in ['highway', 'road', 'street', 'path']):
            if shape.intersects(inclusion_zone):
                clipped = shape.intersection(inclusion_zone)
                if not clipped.is_empty:
                    feature['shape'] = clipped
                    filtered_features.append(feature)
        elif osm_tag == 'building':
            if shape.intersects(inclusion_zone):
                filtered_features.append(feature)
        else:
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
        d_attr = convert_to_svg_d(shape) 
        
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


# -----------------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E2. convert_to_svg_d(shape)  ---
# -----------------------------------------------------------------------
def convert_to_svg_d(shape):
    """
    Converts a Shapely LineString, Polygon, MultiLineString, or MultiPolygon
    into a standardized SVG Path 'd' attribute string.
    """
    d_parts = []

    # ... (Main logic remains the same) ...
    if shape.is_empty:
        return ""

    if shape.geom_type in ('LineString', 'Polygon'):
        if shape.geom_type == 'LineString':
            d_parts.append(process_linestring(shape))
        elif shape.geom_type == 'Polygon':
            d_parts.append(process_polygon(shape))
            
    elif shape.geom_type in ('MultiLineString', 'MultiPolygon'):
        # Iterate over all components in the collection
        for geom in shape.geoms:
            if geom.geom_type == 'LineString':
                d_parts.append(process_linestring(geom))
            elif geom.geom_type == 'Polygon':
                d_parts.append(process_polygon(geom))

    return " ".join(d_parts)

# -----------------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E3. process_linestring(line) ---
# -----------------------------------------------------------------------
def process_linestring(line):
    coords_list = list(line.coords) # Convert to a mutable list

    # --- FIX 3: ENFORCE LOOP BREAK ---
    # If the LineString is a closed loop, drop the final coordinate to create a gap
    if line.is_closed and len(coords_list) > 1 and coords_list[0] == coords_list[-1]:
         coords_list = coords_list[:-1] # Drop the last coordinate

    coords = format_coords_for_svg(coords_list)
    if not coords: return ""

    # Start with MoveTo (M) to the first point, then LineTo (L) for the rest
    return "M " + " L ".join(coords) 


# -----------------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E4. process_polygon(polygon) --- 
# -----------------------------------------------------------------------
def process_polygon(polygon):
    parts = []

    # 1. Exterior Ring (The main shape boundary)
    exterior_coords = format_coords_for_svg(polygon.exterior.coords)
    if not exterior_coords: return ""
    # Polygon MUST be closed with 'Z'
    parts.append("M " + " L ".join(exterior_coords) + " Z") 

    # 2. Interior Rings (Holes)
    for interior in polygon.interiors:
        interior_coords = format_coords_for_svg(interior.coords)
        # Holes MUST be closed with 'Z'
        parts.append("M " + " L ".join(interior_coords) + " Z")

    return " ".join(parts)


# ---------------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E5. coords_to_svg_d(coord) ---
# ---------------------------------------------------------------------
def coords_to_svg_d(coords):
    """Helper to convert a list of (x,y) tuples to 'M x,y L x,y ... Z' """
    if len(coords) < 3: return ""
    
    start_pt = coords[0]
    
    # M (move to start)
    d_str = f"M {start_pt[0]:.4f},{start_pt[1]:.4f}" 
    
    # L (line to rest of points)
    d_str += " L " + " ".join([f"{x:.4f},{y:.4f}" for x, y in coords[1:]])
    
    # Z (close path)
    d_str += " Z" 
    
    return d_str


# ----------------------------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E6. shapely_polygon_to_svg_d(poly_geom) --- 
# ----------------------------------------------------------------------------------
def shapely_polygon_to_svg_d(poly_geom):
    """
    Converts a Shapely Polygon or MultiPolygon to an SVG 'd' string.
    Includes both Exterior and Interior rings (holes).
    """
    if poly_geom.is_empty:
        return ""

    parts = []
    
    # Import inside to keep main imports clean if user preferred it that way
    from shapely.ops import unary_union
    from shapely.geometry import Polygon, MultiPolygon

    # Ensure we are iterating over individual Polygon objects
    if isinstance(poly_geom, Polygon):
        geoms = [poly_geom]
    elif isinstance(poly_geom, MultiPolygon):
        geoms = unary_union(poly_geom).geoms
    else:
        # Handle GeometryCollection or other types by attempting a union
        geoms = unary_union(poly_geom).geoms if poly_geom.geom_type == 'GeometryCollection' else [poly_geom]

    for p in geoms:
        if p.geom_type != 'Polygon': continue
        
        # 1. Exterior Ring (The Outside)
        if p.exterior:
            x, y = p.exterior.coords.xy
            coords = list(zip(x, y))
            parts.append(coords_to_svg_d(coords))
        
        # 2. Interior Rings (The Holes)
        for interior in p.interiors:
            x, y = interior.coords.xy
            coords = list(zip(x, y))
            parts.append(coords_to_svg_d(coords))

    return " ".join(parts)


# -----------------------------------------------------------------------------------------
# --- SVG Rendering and Output FUNCTIONS E7. write_svg_file(header, elements, filename) ---
# -----------------------------------------------------------------------------------------
def write_svg_file(header, background_elements, foreground_elements, filename):
    """
    Combines the SVG header and elements, and writes the complete XML 
    to the defined outfile
    """
    svg_content = [header]
    
    # 1. Add Background Elements (OUTSIDE the map-features group, written first)
    svg_content.extend(background_elements)
    
    svg_content.append('<g id="map-features">')
    svg_content.extend(foreground_elements)
    svg_content.append('</g>')
    svg_content.append('</svg>')
    
    with open(filename, 'w') as f:
        f.write('\n'.join(svg_content))
    
    print(f"File written successfully to: {filename}") # Already printed in main
    

# -----------------------------------------------------------------------------------------------
# --- SVG Rendering background and Output FUNCTIONS E8. generate_background_svg_elements(...) ---
# -----------------------------------------------------------------------------------------------
def generate_background_svg_elements(background_files, svg_width, svg_height, 
                                     MAP_MIN_X, MAP_MAX_Y, REAL_TO_SVG_SCALE,
                                     MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 
                                     METERS_PER_DEGREE_LON_FACTOR, METERS_PER_DEGREE_LAT_FACTOR):
    """
    Reprojects and clips the GeoTIFF to the exact degree-bounds of the map.
    Places the resulting PNG at SVG origin (0,0) without units to match vector scaling.
    """
    svg_elements = []
    dst_crs = 'EPSG:4326' # Standardizing on WGS84 for the 'flat' degree grid
    
    for filename in background_files:
        try:
            with rasterio.open(filename) as src:
                temp_filename = filename.replace(".tif", "_aligned.png")
                
                # We use a high enough resolution for the PNG crop
                out_width = 2000 
                out_height = int(out_width * (svg_height / svg_width))
                
                # Transform defined by the map's degree boundaries
                dst_transform = rasterio.transform.from_bounds(
                    MIN_LON, MIN_LAT, MAX_LON, MAX_LAT, out_width, out_height
                )
                
                # Reproject/Warp the image data
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

                # Save the aligned PNG
                out_meta = src.meta.copy()
                out_meta.update({
                    "driver": "PNG", 
                    "height": out_height, 
                    "width": out_width,
                    "transform": dst_transform, 
                    "crs": dst_crs, 
                    "count": src.count
                })

                with rasterio.open(temp_filename, 'w', **out_meta) as dst:
                    dst.write(destination)

                # --- ALIGNMENT FIX ---
                # Place at (0,0) and use UNITLESS width/height.
                # This ensures 1 unit in the image = 1 unit in the vector paths.
                x_pos = 0.0
                y_pos = 0.0

                svg_elements.append(
                    f'<image x="{x_pos:.3f}" y="{y_pos:.3f}" '
                    f'width="{svg_width:.3f}" height="{svg_height:.3f}" '
                    f'xlink:href="file:///{os.path.abspath(temp_filename)}" />'
                )
                
                print(f"Successfully aligned: {temp_filename} placed at SVG origin (0,0)")

        except Exception as e:
            print(f"Error processing {filename}: {e}")

    return svg_elements


# ------------------------------------
# --- The MAIN FUNCTION F1. main() ---
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
         
    all_shapely_features = []
    golf_course_polygons = [] # For handling buildings
    
    # --------------------------------------------------------------------------------
    # --- PASS P1. Feature Collection (Ways & Relations) - Collect Shapely Objects ---
    # --------------------------------------------------------------------------------
    try:
        tree_pass2 = ET.parse(inputFile)
        document_pass2 = tree_pass2.getroot()
    except Exception as e:
        print(f"Error re-parsing input file '{inputFile}' for feature collection: {e}")
        # If re-parsing fails, fall back to the potentially exhausted document
        document_pass2 = document
        
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
            elif tag_key in styles:
                style_data = styles[tag_key]
                feature_tag = tag_key
                break
            
        if style_data is None:
            continue

        # 2. Geometry Retrieval & Initial Visibility Check
        way_coords_unclipped = get_way_coordinates(way_id)
        if len(way_coords_unclipped) < 2:
            continue
        
        current_line_string = sg.LineString(way_coords_unclipped)
        # We check against the full SVG viewbox first to see if it's even relevant
        full_viewbox = sg.box(0, 0, SVG_WIDTH_MM, SVG_HEIGHT_MM)
        if not current_line_string.intersects(full_viewbox):
            continue

        # 3. Determine Feature Type
        is_stroke_to_path = style_data.get('stroke_to_path', False)
        stroke_width = float(style_data['attrs'].get('stroke-width', 0.0))
        is_line_feature = is_stroke_to_path or (stroke_width > 0.0)
        is_closed_way = (way_coords_unclipped[0] == way_coords_unclipped[-1])

        current_shape = None
        requires_line_clip = False

        # --- CASE A: LINE FEATURES (Roads, Paths, Fences) ---
        if is_line_feature:
            # IMPORTANT: Keep the UNCLIPPED version. 
            # This allows C5 to 'smooth' the corners even at the map edges.
            current_shape = current_line_string 
            requires_line_clip = True

        # --- CASE B: AREA FEATURES (Buildings, Bunkers, Greens) ---
        elif is_closed_way and ('fill' in style_data['attrs'] and style_data['attrs']['fill'] not in ('none', 'transparent')):
            try:
                # Polygons are clipped to the 8mm safety box immediately 
                # because they don't need 'extra length' for end-caps.
                way_coords_clipped = clip_polyline_to_bounds(way_coords_unclipped, *CLIP_BBOX)
                if len(way_coords_clipped) >= 3:
                    current_shape = sg.Polygon(way_coords_clipped)
                    requires_line_clip = False
            except Exception as e:
                print(f"Warning: Could not create Polygon for way {way_id}: {e}")
                continue
        
        # 4. Final Feature Packaging
        if current_shape and not current_shape.is_empty:
            # Build descriptive ID
            safe_tag = feature_tag.replace('.', '-').replace('=', '-')
            descriptive_base_id = f"{safe_tag}-{way_id}"
            
            # Determine OSM Category for D6 Filtering
            final_osm_tag = feature_tag.split('.')[0] if '.' in feature_tag else feature_tag
            if way_tags.get('building') is not None:
                final_osm_tag = 'building'

            # Collect Golf Courses for the Building Clipper (D6)
            if final_osm_tag == 'leisure.golf_course' or way_tags.get('leisure') == 'golf_course':
                golf_course_polygons.append(current_shape)

            all_shapely_features.append({
                'shape': current_shape,
                'tag': feature_tag,
                'id': way_id,
                'base_id': descriptive_base_id,  
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
    for relation in document_pass2.iter('relation'):
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
        
    # ----------------------------------------------------------------------
    # --- PASS 2 P2: Line Clipping, Z-Order Cleaning, and Final Geometry Prep ---
    # ----------------------------------------------------------------------
    
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
    final_features_to_draw = z_order_clip_and_finalize(all_features_after_line_clip)

    print(f"INFO: Final feature count after Z-order processing: {len(final_features_to_draw)}")
    
    # ----------------------------------------------------------------------
    # --- PASS 3: Generate SVG, Sort and Write to File (New, Streamlined) ---
    # ----------------------------------------------------------------------

    background_svg_elements = generate_background_svg_elements(
        background_files, 
        SVG_WIDTH_MM, 
        SVG_HEIGHT_MM,
        MAP_MIN_X, 
        MAP_MAX_Y,
        REAL_TO_SVG_SCALE,
        MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, METERS_PER_DEGREE_LON_FACTOR, METERS_PER_DEGREE_LAT_FACTOR
    )

    background_svg_elements.reverse()
    svg_features = generate_svg_elements(final_features_to_draw)
    all_svg_elements = background_svg_elements + svg_features

    try:
        write_svg_file(svg_header, background_svg_elements, svg_features, outputFile)
        print(f"\n✅ SUCCESS: SVG file generated successfully: {outputFile} with {len(all_svg_elements)} elements.")

    except Exception as e:
        print(f"❌ ERROR: Failed to write SVG file: {e}")
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
        
if __name__ == "__main__":
    main()
    
