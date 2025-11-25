#!/usr/bin/env python3
"""
svg_clipper.py
1. Reads smoothed SVG and styles.json to determine geometric hierarchy (z-order).
2. Uses Shapely difference operations to clip higher z-order features 
   from lower z-order features, eliminating overlaps.
3. Applies a configurable spacing gap (buffer) for linear features like cartpaths.
"""
import xml.etree.ElementTree as ET
import json
from svgpathtools import parse_path, Line
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
import numpy as np

# --- CONFIGURATION (Separation Distances) ---
CARTPATH_GAP_M = 0.10  # 10cm separation from underlying feature
ROAD_GAP_M = 0.05      # 5cm separation for roads/highways

STYLES_FILE = 'styles.json'
INPUT_FILE = 'smoothed_out.svg'
OUTPUT_FILE = 'clipped_final.svg'

# Helper function imports from svg_points2path_v4.py (for consistency)
# We need svg_path_to_shapely and shapely_to_svg_d, but for simplicity here,
# we'll redefine the core logic for conversion directly.

def load_styles(filepath):
    """Loads and preprocesses the styles.json file."""
    try:
        with open(filepath, 'r') as f:
            styles = json.load(f)
            # Add feature key to each style entry
            for key, data in styles.items():
                data['feature'] = key
            return styles
    except Exception as e:
        print(f"❌ Error loading {filepath}: {e}")
        return {}

def svg_path_to_shapely_polygon(d_string):
    """Converts a path string to a closed Shapely Polygon."""
    try:
        path_obj = parse_path(d_string)
        if len(path_obj) == 0: return None

        # Sample points only from the exterior boundary
        points = []
        for segment in path_obj:
            num_samples = max(2, min(50, int(segment.length())))
            for i in range(num_samples):
                t = i / (num_samples - 1)
                pt = segment.point(t)
                points.append((pt.real, pt.imag))
        
        if len(points) < 3: return None
        
        # If the path has interior rings (holes), Polygon constructor handles it
        # However, for simplicity and stability in this clipper, we assume 
        # the input path is a single exterior ring.
        poly = Polygon(points)

        return poly if poly.is_valid else poly.buffer(0)
    except Exception as e:
        # print(f"⚠ Warning: Failed to parse path segment: {e}")
        return None

def shapely_to_svg_d(poly):
    """Converts a Shapely Polygon back to an SVG 'd' string."""
    if poly.is_empty: return ""
    
    # Handle Polygon and MultiPolygon
    if isinstance(poly, MultiPolygon):
        polygons = poly.geoms
    else:
        polygons = [poly]

    d_strings = []
    for p in polygons:
        if p.is_empty: continue
        
        def ring_to_d(coords):
            if len(coords) < 3: return ""
            parts = [f"M {coords[0][0]:.4f},{coords[0][1]:.4f}"]
            for x, y in coords[1:]:
                parts.append(f"L {x:.4f},{y:.4f}")
            return " ".join(parts) + " Z"

        d_string = ring_to_d(list(p.exterior.coords))
        
        # Handle Holes (Interiors)
        for interior in p.interiors:
            d_string += " " + ring_to_d(list(interior.coords))
            
        d_strings.append(d_string)
        
    return " ".join(d_strings)


def get_clipping_buffer(feature_style):
    """Determines the clipping margin based on feature type and stroke width."""
    feature = feature_style.get('feature', '')
    
    # 1. Determine the width of the shape being cut out
    stroke_width = float(feature_style.get('stroke-width', 0))
    if stroke_width == 0:
        # If it's a fill area (Green, Bunker, Fairway), the width is 0
        cutout_width = 0
    else:
        # If it's a path (Cartpath, Road), the cutout width is stroke/2 (radius)
        cutout_width = stroke_width / 2.0

    # 2. Add the required separation gap
    if 'cartpath' in feature:
        gap = CARTPATH_GAP_M
    elif 'highway' in feature or 'road' in feature:
        gap = ROAD_GAP_M
    else:
        gap = 0.0 

    return cutout_width + gap

def process_clipping(input_file, output_file, styles_file):
    print(f"Loading styles from: {styles_file}")
    styles = load_styles(styles_file)
    if not styles: return

    print(f"Loading geometry from: {input_file}")
    ET.register_namespace('', "http://www.w3.org/2000/svg")
    tree = ET.parse(input_file)
    root = tree.getroot()

    # --- 1. EXTRACT, GROUP, AND SORT GEOMETRY ---
    
    # Group shapes by their determined feature type (color) and z-order
    feature_map = {} # Maps fill color to feature style
    
    # Invert the styles for easy lookup by fill color
    for key, data in styles.items():
        if data.get('fill'):
            feature_map[data['fill'].upper()] = data
        elif data.get('stroke'):
            feature_map[data['stroke'].upper()] = data # Use stroke for line features
    
    # Dictionary to hold Shapely geometry, grouped by z-order for processing
    # Structure: {z_order: [(shapely_geom, fill_color, original_attributes), ...]}
    z_order_groups = {} 
    
    for path_element in root.iter('{http://www.w3.org/2000/svg}path'):
        attribs = dict(path_element.attrib)
        d_string = attribs.get('d')
        fill_color = attribs.get('fill', '#000000').upper()
        
        # Look up the style based on the current fill color
        style = feature_map.get(fill_color) or feature_map.get(attribs.get('stroke', '#000000').upper())
        if not style: continue
        
        z_order = style.get('z-order', 0)
        
        # Convert SVG path to Shapely geometry
        geom = svg_path_to_shapely_polygon(d_string)
        if geom:
            if z_order not in z_order_groups:
                z_order_groups[z_order] = []
            z_order_groups[z_order].append((geom, fill_color, attribs, style))


    # --- 2. PERFORM CLIPPING (LOW Z-ORDER -> HIGH Z-ORDER) ---

    sorted_z_orders = sorted(z_order_groups.keys())
    
    # Store the cumulative shapes of features that have been processed and need to cut others
    processed_cutouts = {} # {z_order: combined_shapely_cutout}

    for current_z in sorted_z_orders:
        current_group = z_order_groups[current_z]
        print(f"   Processing Z-order {current_z} with {len(current_group)} shapes...")
        
        # Combine all shapes in the current group for clipping against lower groups
        current_geoms = [item[0] for item in current_group]
        
        # Determine the necessary buffer for this feature's *cutout*
        # (We use the style of the first element as all should be the same type)
        style = current_group[0][3]
        clipping_buffer = get_clipping_buffer(style)
        
        # 2a. Cut the current geometry group from any lower-Z cutouts
        # This prevents, e.g., a green (Z=90) from being cut by an overlapping fairway (Z=60)
        # We only want higher Z to cut lower Z.
        current_combined_geom = unary_union(current_geoms)
        
        for lower_z in processed_cutouts:
            # Clip the current (higher Z) geometry against the buffer/cutout of the lower Z feature.
            # Only do this if the lower Z feature is the type that needs a cutout.
            # Example: Cartpath (98) is cut *by* Fairway (60) if the Cartpath was under the Fairway.
            
            # Since the initial merge already handled self-overlaps within the file,
            # this primary loop focuses only on ensuring the *lower* Z-order features
            # do not intersect with the *current* Z-order feature.
            
            # For this simple hierarchy, we assume the input (smoothed_out.svg) 
            # already represents the area polygons as intended.
            pass


        # 2b. Clip lower-Z geometries using the current combined shape.
        # This is the primary clipping action.
        
        # Create the buffered cutout shape for the current group
        if clipping_buffer > 0:
            current_buffered_cutout = current_combined_geom.buffer(clipping_buffer, join_style=2)
        else:
            current_buffered_cutout = current_combined_geom # Use the shape itself as the cutout
        
        # Iterate over all *lower* Z-order groups that have already been processed
        for lower_z in [z for z in processed_cutouts.keys() if z < current_z]:
            
            # The lower-Z feature's geometry (A) is reduced by the current feature's buffered cutout (B)
            lower_geom_to_clip = processed_cutouts[lower_z]
            clipped_geom = lower_geom_to_clip.difference(current_buffered_cutout)
            processed_cutouts[lower_z] = clipped_geom

        # Add the current (potentially modified) group to the processed cutouts list
        processed_cutouts[current_z] = current_combined_geom # We save the *unclipped* geometry for export later

    # --- 3. RE-GENERATE SVG ---

    final_paths = []
    
    # Clear old SVG contents (except the root, which holds namespace/viewbox)
    for child in list(root):
        root.remove(child)
        
    # We must iterate over z_order_groups again to get the original attributes/color
    for z_order in sorted_z_orders:
        
        if z_order not in processed_cutouts: continue
        
        # The final, clipped geometry for this Z-order
        final_geom = processed_cutouts[z_order]
        
        # Convert back to SVG paths
        raw_d_string = shapely_to_svg_d(final_geom)

        if not raw_d_string: continue
        
        # Use a representative style from the original group for color
        style = z_order_groups[z_order][0][3]
        fill_color = z_order_groups[z_order][0][1]

        # Add the resulting paths to the new root
        for d_part in raw_d_string.split(" Z"):
            if not d_part.strip(): continue
            
            # Re-add the closing Z since shapely_to_svg_d removes it for splitting
            final_d = d_part + " Z"
            
            path_elem = ET.SubElement(root, 'path')
            path_elem.set('d', final_d)
            path_elem.set('fill', fill_color)
            path_elem.set('stroke', 'none') # Final sanitization

    # Final writing
    tree = ET.ElementTree(root)
    tree.write(output_file, encoding='utf-8', xml_declaration=True)
    print(f"✅ Success: All geometry clipped and saved to {output_file}.")


# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # Ensure the required files are present and run the clipper
    try:
        process_clipping(INPUT_FILE, OUTPUT_FILE, STYLES_FILE)
    except FileNotFoundError as e:
        print(f"\n❌ Required file not found. Ensure {e.filename} exists.")
    except Exception as e:
        print(f"\n❌ An unhandled error occurred during clipping: {e}")
        
