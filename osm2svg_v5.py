#!/usr/bin/env python3

# This script reads OpenStreetMaps (OSM) data and converts it to SVG suitable for OPCD Tools.
# http://openstreetmap.org (select your course then export to map.osm the golf course data.
# The stles.json contains the OSM keys and tags we search for and provides the OPCD compatible colors
# for Strokes and fills, stroke (line) width and if Json boolean 'stroke-to-path' is set to true
# it will convert the strokes to filled paths.  It also rounds corners of the resulting paths
# to the 'corner-radius' specified in the style json structure.  

from lxml import etree
import math
import json
import sys

# Conceptual function demonstrating the clipping process
from shapely.geometry import Polygon
from shapely.ops import unary_union

# Setup - edit these configuration variables as necessary
inputFile = 'map.osm'
styleFile = 'styles.json'
outputFile = 'out.svg'
outWidth = 1000  # Width of the output, in pixels
# The height will be calculated automatically based on the aspect ratio of the
# downloaded chunk of OSM data.

# --- Global Variables for Projection/Scaling (set in main) ---
nodes = {}
ways = {}
minlon, minlat, maxlon, maxlat = 0, 0, 0, 0
xscale, yscale, outHeight = 0, 0, 0

def get_normal(p1, p2):
    """Calculates the normalized perpendicular vector (normal) for a segment."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    length = math.sqrt(dx*dx + dy*dy)
    if length == 0: return (0, 0, 0) # length, nx, ny
    # nx and ny are the normalized (length 1) perpendicular vector components
    nx = -dy / length
    ny = dx / length
    return (length, nx, ny)

def intersect_lines(p1, p2, p3, p4):
    """Finds the intersection point of two lines defined by (p1, p2) and (p3, p4)."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4

    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if den == 0:
        return None  # Parallel or collinear

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den

    # Intersection point (Px, Py)
    px = x1 + t * (x2 - x1)
    py = y1 + t * (y2 - y1)
    
    return (px, py)

# Utility function needed for cross product calculation:
def vector_from_points(p1, p2):
    """Calculates the 2D vector from point p1 to p2."""
    return (p2[0] - p1[0], p2[1] - p1[1])

def cross_product(v1, v2):
    """Calculates the 2D cross-product (wedge product) for two vectors."""
    # This determines the turn direction (winding order)
    return v1[0] * v2[1] - v1[1] * v2[0]

def point_on_line(p_center, p_end, dist):
    """
    Returns a point on the line passing through p_center and p_end, 
    located at 'dist' distance from p_center towards p_end.
    """
    vec = (p_end[0] - p_center[0], p_end[1] - p_center[1])
    length = math.sqrt(vec[0]**2 + vec[1]**2)
    
    if length == 0:
        return p_center
    
    # Normalized vector
    unit = (vec[0] / length, vec[1] / length)
    
    # Point at distance 'dist' from p_center
    return (p_center[0] + unit[0] * dist, p_center[1] + unit[1] * dist)

def convert_way_to_svg_path(way_id):
    """
    Converts a Way ID into an SVG path data string (e.g., "M x1 y1 L x2 y2 ... Z").
    
    Returns an empty string if the way_id is not found or has no nodes.
    """
    if way_id not in ways:
        print(f"Warning: Way ID {way_id} referenced in relation but not found.")
        return ""
    
    node_refs = ways[way_id]
    points = []
    
    # 1. Get coordinates for each node in the way
    for nodeid in node_refs:
        if nodeid in nodes:
            lon, lat = nodes[nodeid]
            
            # Convert lat/lon to SVG coordinates (0,0 is top-left)
            x = (lon - minlon) * xscale
            y = outHeight - (lat - minlat) * yscale
            
            points.append(f"{x:.4f} {y:.4f}") # Using .4f for precision
        else:
            print(f"Error finding node {nodeid} for way {way_id}")
            return "" # Fail this path if a node is missing

    if not points:
        return ""

    # 2. Format as an SVG path string (M = MoveTo, L = LineTo, Z = ClosePath)
    # The first point is M, the rest are L. Add Z to close the polygon.
    return f"M {points[0]} L {' L '.join(points[1:])} Z"


def process_multipolygon_relation(relation, styles):
    # 1. Check if the relation has a drawable style (unchanged lookup)
    style = None
    for tag in relation.findall('tag'):
        # ... (lookup logic to assign style) ...
        searchtag = f"{tag.get('k')}.{tag.get('v')}"
        if searchtag in styles:
            style = styles[searchtag]
            break
        elif tag.get('k') in styles:
            style = styles[tag.get('k')]
            break
            
    if style is None:
        return None # Return None if no style is found

    # Get Z-order
    z_order = style['z-order'] # <--- NEW

    # Get clean style string (unchanged)
    svg_style_string = style['svg_style']
    if 'fill-rule' not in svg_style_string:
        svg_style_string = f'{svg_style_string} fill-rule="evenodd"'

    # 2. Extract member Ways (outer and inner)
    outer_paths = []
    inner_paths = []
    
    for member in relation.findall('member'):
        if member.get('type') == 'way':
            way_id = member.get('ref')
            role = member.get('role')
            
            path_d = convert_way_to_svg_path(way_id)
            
            if path_d:
                if role == 'outer':
                    outer_paths.append(path_d)
                elif role == 'inner':
                    inner_paths.append(path_d)

    # 3. Combine paths and GENERATE the SVG string
    if outer_paths:
        combined_d = ' '.join(outer_paths + inner_paths)
        
        # Return the feature dictionary instead of writing to 'out'
        svg_string = f'<path d="{combined_d}" {svg_style_string} id="rel_{relation.get("id")}"/>\n'
        
        return {
            'z': z_order,
            'svg': svg_string
        }
    
    return None

def convert_stroke_to_path(points, stroke_width, style_attrs, corner_radius=0, straight_threshold=0.999):
    """
    Converts a polyline into a closed path using sharp miter joints,
    defaulting to 'butt' endcaps. Corner rounding logic has been removed.
    """
    if len(points) < 2:
        return ""

    half_width = stroke_width / 2.0
    # Set default endcap to 'butt'
    cap_style = style_attrs.get('stroke-linecap', 'butt').lower() 
    
    # 1. Pre-calculate offset segments and Normals
    segments_data = []
    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i+1]
        # get_normal must be defined elsewhere
        length, nx, ny = get_normal(p1, p2) 
        
        segments_data.append({
            'len': length,
            'nx': nx, 
            'ny': ny, 
            'f_line': ( (p1[0] + nx * half_width, p1[1] + ny * half_width), 
                        (p2[0] + nx * half_width, p2[1] + ny * half_width) ), 
            'r_line': ( (p1[0] - nx * half_width, p1[1] - ny * half_width), 
                        (p2[0] - nx * half_width, p2[1] - ny * half_width) ) 
        })

    forward_path = []
    reverse_path = []

    # 2. START CAP (p[0])
    seg0 = segments_data[0]
    start_f = seg0['f_line'][0]
    start_r = seg0['r_line'][0]
    
    if cap_style == 'square':
        # Extend the path for a square cap
        _, nx, ny = get_normal(points[1], points[0])
        start_f = (start_f[0] + nx * half_width, start_f[1] + ny * half_width)
        start_r = (start_r[0] + nx * half_width, start_r[1] + ny * half_width)

    forward_path.append(f"M {start_f[0]:.4f} {start_f[1]:.4f}")
    reverse_path.append(f"L {start_r[0]:.4f} {start_r[1]:.4f}") 
    
    # 3. Corner Joints (points 1 to n-2)
    for i in range(len(points) - 2):
        seg_in = segments_data[i]
        seg_out = segments_data[i+1]
        
        # --- STRAIGHTNESS CHECK ---
        dot = (seg_in['nx'] * seg_out['nx']) + (seg_in['ny'] * seg_out['ny'])
        
        if dot >= straight_threshold:
            # Simple line connection for straight segments
            current_f_point = seg_out['f_line'][0]
            current_r_point = seg_out['r_line'][0]
            
        else:
            # --- SHARP CORNER (MITER JOINT) ---
            # intersect_lines must be defined elsewhere
            I_f = intersect_lines(seg_in['f_line'][0], seg_in['f_line'][1], 
                                  seg_out['f_line'][0], seg_out['f_line'][1])
            I_r = intersect_lines(seg_in['r_line'][0], seg_in['r_line'][1], 
                                  seg_out['r_line'][0], seg_out['r_line'][1])

            if I_f is None or I_r is None:
                # Fallback for parallel segments
                current_f_point = seg_out['f_line'][0]
                current_r_point = seg_out['r_line'][0]
            else:
                # Use the miter intersection for a sharp corner
                current_f_point = I_f
                current_r_point = I_r
            
        # Append the line segment (Miter or straight connection)
        forward_path.append(f"L {current_f_point[0]:.4f} {current_f_point[1]:.4f}")
        reverse_path.insert(0, f"L {current_r_point[0]:.4f} {current_r_point[1]:.4f}")
        
    # 4. END CAP (p[n-1])
    seg_last = segments_data[-1]
    end_f = seg_last['f_line'][1]
    end_r = seg_last['r_line'][1]
    
    if cap_style == 'square':
        # Extend the path for a square cap
        _, nx, ny = get_normal(points[-2], points[-1])
        end_f = (end_f[0] + nx * half_width, end_f[1] + ny * half_width)
        end_r = (end_r[0] + nx * half_width, end_r[1] + ny * half_width)

    forward_path.append(f"L {end_f[0]:.4f} {end_f[1]:.4f}")
    reverse_path.insert(0, f"L {end_r[0]:.4f} {end_r[1]:.4f}")
    
    # 5. Combine all parts and close the path
    path_d = [forward_path[0]] + forward_path[1:] + reverse_path
    
    return ' '.join(path_d) + ' Z'


CLIPPING_GAP_SIZE = 0.05 
def clip_overlapping_paths(paths_list_2d):
    """
    Performs iterative Difference operations on a list of Shapely Polygons 
    to remove overlaps between objects of the same type, ensuring a small gap.
    
    Args:
        paths_list_2d (list): List of Shapely Polygon objects, ordered from 
                              highest-priority (top) to lowest-priority (bottom).
                              
    Returns:
        list: List of clipped Shapely Polygon/MultiPolygon objects (or None if entirely clipped).
    """
    
    clipped_polygons = []
    
    # Iterate from the lowest priority path (at the end of the list) backwards.
    # We clip the current path against all paths that precede it (higher priority).
    for i in range(len(paths_list_2d) - 1, -1, -1):
        current_poly = paths_list_2d[i]
        
        # 1. Define the 'cutters': all higher-priority paths (indices 0 up to i-1)
        higher_priority_polygons = paths_list_2d[:i]
        
        if not higher_priority_polygons:
            # This is the highest priority path; nothing clips it.
            result = current_poly
        else:
            # 2. Combine all higher-priority cutters into a single geometry
            #    (Uses unary_union for Shapely 2.0+ compatibility)
            cutter_union = unary_union(higher_priority_polygons)

            # 3. Buffer the cutter to create the desired clipping gap
            buffered_cutter = cutter_union.buffer(CLIPPING_GAP_SIZE)

            # 4. Perform the Difference operation
            result = current_poly.difference(buffered_cutter)
        
        # 5. Check the result and store it
        if not result.is_empty and result.area > 0:
            # Simplify the geometry slightly to reduce vertex count before storage/conversion
            clipped_polygons.insert(0, result.simplify(0.001)) 
        else:
            # The path was entirely clipped away
            clipped_polygons.insert(0, None)
            
    return clipped_polygons
    
def main():
    """The main function to execute the conversion process."""

    global nodes, ways, minlon, minlat, maxlon, maxlat, xscale, yscale, outHeight

    # Load the style.json file and remove all comments. that begin with // or #
    try:
        clean_json_lines = []
        with open(styleFile, 'r') as f:
            for line in f:
                stripped_line = line.strip()
                # Check for standard JSON comments (//, #)
                if stripped_line.startswith('//') or stripped_line.startswith('#'):
                    continue
                # Optionally filter out the old "COMMENT" key
                if stripped_line.startswith('"COMMENT"'):
                    continue
                
                if stripped_line:
                    clean_json_lines.append(line)
        
        # Join the clean lines into a single string and parse
        json_string = "".join(clean_json_lines)
        styleDef = json.loads(json_string)
    
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error loading style file '{styleFile}': {e}")
        sys.exit(1)

    # Parse style control entries
    styles = {}
    for tag, attrs in styleDef.items():
        # Handle stroke_to_path flag
        stroke_to_path = attrs.pop('stroke_to_path', False)
        if isinstance(stroke_to_path, str):
            stroke_to_path = (stroke_to_path.lower() == 'true')

        # Handle corner_radius
        corner_radius = float(attrs.pop('corner_radius', 0))

        # Handle z-order for stacking
        try:
            z_order = int(attrs.pop('z-order', 0))
        except ValueError:
            print(f"Warning: Invalid z-order value for {tag}. Using 0.")
            z_order = 0
        
        # Format *remaining* attributes into a single SVG style string
        svg_style_string = ' '.join([f'{k}="{v}"' for k, v in attrs.items()])
        
        # Store all necessary data
        styles[tag] = {
            'svg_style': svg_style_string,
            'stroke_to_path': stroke_to_path,
            'corner_radius': corner_radius,
            'z-order': z_order,
            'attrs': attrs # Keep attrs for geometry calculations
        }

    # Load the OSM file
    try:
        document = etree.parse(inputFile)
    except (FileNotFoundError, etree.XMLSyntaxError) as e:
        print(f"Error loading input file '{inputFile}': {e}")
        sys.exit(1)

    # Get bounds and calculate scaling factors
    boundsElems = document.findall('bounds')
    if len(boundsElems) != 1:
        print("Expected exactly one <bounds/> element. Something is weird.")
        sys.exit(1)

    b = boundsElems[0]
    minlat = float(b.get('minlat'))
    maxlat = float(b.get('maxlat'))
    minlon = float(b.get('minlon'))
    maxlon = float(b.get('maxlon'))

    xscale = outWidth / (maxlon - minlon)
    avg_lat = (minlat + maxlat) / 2
    yscale = xscale / math.cos(math.radians(avg_lat))
    outHeight = yscale * (maxlat - minlat)

    # Load nodes and ways into memory
    nodes = {node.get('id'): (float(node.get('lon')), float(node.get('lat')))
             for node in document.findall('node')}

    ways = {}
    for way in document.findall('way'):
        ways[way.get('id')] = [noderef.get('ref') for noderef in way.findall('nd')]
        
    # List to hold all generated SVG strings and their Z-order
    svg_features = []
        
    # ----------------------------------------------------
    # --- PART 2: Feature Collection (NO FILE WRITING) ---
    # ----------------------------------------------------

 # --- Draw Order: Simple Ways (Collect Features) ---
    for way in document.findall('way'):
        style_data = None
        feature_tag = None # Added for clarity in path ID
        
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
        way_coords = []
        way_id = way.get('id')
        if way_id in ways:
            # NOTE: Assuming your coordinate conversion block here is correct:
            for nodeid in ways[way_id]:
                if nodeid in nodes:
                    lon, lat = nodes[nodeid]
                    x = (lon - minlon) * xscale
                    y = outHeight - (lat - minlat) * yscale
                    way_coords.append((x, y))

        if len(way_coords) < 2:
            continue

        # 3. Generate SVG element string (path or polyline)
        svg_element = ""

        # NOTE: Using .get() for safety and ensuring the boolean/string conversion is done by the style loader.
        # Assuming style_data['stroke_to_path'] is a proper boolean True/False.
        if style_data.get('stroke_to_path', False): 
            # --- Handle Stroke-to-Path Elements ---
            
            # Cast to float for geometric functions
            stroke_width = float(style_data['attrs'].get('stroke-width', 1.0))
            radius = float(style_data.get('corner_radius', 0.0)) # Safely using .get()

            # Pass coordinates, width, attributes, and radius to path conversion function
            path_d = convert_stroke_to_path(way_coords, stroke_width, style_data['attrs'], radius) 

            # CRITICAL CLEANUP: Use the original stroke color as the new FILL color
            # We explicitly ignore the 'fill: none' from the JSON, but take the stroke color.
            fill_color = style_data['attrs'].get('stroke', 'black')

            # Build the final SVG path element with mandatory stroke cleanup
            svg_element = (
                f'<path d="{path_d}" '
                f'fill="{fill_color}" '
                f'stroke="none" '          # 💥 FIX: Eliminate stroke attribute
                f'stroke-width="0" '       # 💥 FIX: Ensure stroke-width is zero
                f'id="way_{way_id}_path_{feature_tag}"/>\n' # Use tag for unique ID
            )

        else:
            # --- Standard polyline ---
            # NOTE: If your JSON does not use the "svg_style" string, this line needs to be updated 
            # to manually inject the 'fill', 'stroke', and 'stroke-width' attributes from 'attrs'.
            
            # Since you confirmed the style loader creates style_data['attrs'], let's use it for the polyline too:
            polyline_points = ' '.join([f"{x:.4f} {y:.4f}" for x, y in way_coords])
            
            style_attrs = []
            for k, v in style_data['attrs'].items():
                 style_attrs.append(f'{k}="{v}"')

            # Example: <polyline points="..." fill="none" stroke="#FCA328" stroke-width="4"/>
            svg_element = f'<polyline points="{polyline_points}" {" ".join(style_attrs)} id="way_{way_id}"/>\n'


        # 4. STORE the feature and its z-order
        if svg_element:
            svg_features.append({
                'z': style_data['z-order'],
                'svg': svg_element
            })

    # --- Draw Order: Multipolygons (Collect Features) ---
    for relation in document.findall('relation'):
        if relation.find('tag[@k="type"][@v="multipolygon"]') is not None:
            # This uses the fixed function process_multipolygon_relation
            feature = process_multipolygon_relation(relation, styles) 
            if feature:
                svg_features.append(feature)


    # --------------------------------------------------------
    # --- FINAL STEP: Sort and Write to File (ONE BLOCK) ---
    # --------------------------------------------------------
    
    # Sort the list by 'z' value. Lower Z-order is drawn first (bottom).
    svg_features.sort(key=lambda x: x['z'])

    try:
        with open(outputFile, 'w') as out:
            # 1. Write the SVG header
            out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            svg_header = (
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'width="{outWidth}" height="{outHeight:.4f}" '
                f'viewBox="0 0 {outWidth} {outHeight:.4f}" version="1.1">\n'
            )
            out.write(svg_header)

            # 2. Write all sorted features
            for feature in svg_features:
                out.write(feature['svg'])

            # 3. Write the SVG footer
            out.write('</svg>')

    except IOError as e:
        print(f"Error writing to output file: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
        
