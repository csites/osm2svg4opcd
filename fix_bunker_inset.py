#!/usr/bin/env python3
"""
This is the third stage for processing map.osm into an SVG form suitable to pass the OPCD cloud tool 'Clender'.
Stage 1) run osm2svg_v4.py to create out.svg from the OpenStreetMap export 'map.osm'.
Stage 2) run svg_points2path.py to convert all stroke lines/and vevtor objects into paths and auto-smooth.
Stage 3) run fix_bunker_inset.py to tame the bunker mesh step in the 'Clender'
"""

import sys
import xml.etree.ElementTree as ET
from svgpathtools import parse_path, Path, Line, Arc, CubicBezier
import numpy as np

# --- Global Configuration ---
BUNKER_TAG = "golf.bunker"
# The distance for the geometric outset (this clears tiny self-intersections)
# pyclipper works best with integers, so we'll use a high scaling factor.

INPUT_FILENAME="smoothed_out.svg"
OUTPUT_FILENAME="final_smoothed_out.svg"
OUTSET_DISTANCE_UNSCALED=1.5


def transform_point(point, center_x, center_y, scale_factor):
    """
    Applies centering, scaling, and recentering to a single point.
    Support function for 'simplify_and_outset_path function
    """
    centered_x = point.real - center_x
    centered_y = point.imag - center_y
    scaled_x = centered_x * scale_factor
    scaled_y = centered_y * scale_factor
    new_x = scaled_x + center_x
    new_y = scaled_y + center_y
    return new_x + 1j * new_y

def simplify_and_outset_path(d_string, offset_distance):
    """
    Simplifies the path geometry using successive shortcuts and applies 
    an approximate outset by scaling the simplified control points, 
    maintaining Bezier curves.
    """
    try:
        path = parse_path(d_string)
    except Exception as e:
        print(f"Warning: Failed to parse SVG path for smoothing/outset: {e}")
        return d_string

    # 1. Simplify the path using successive shortcuts (Strong Smoothing)
    # The tolerance (0.5) and max_loops (30) enforce strong geometric simplification.
    # simplified_path = path.approximate_successive_shortcuts(0.5, 30) 

    # 2. Get the path's bounding box center (for scaling reference)
    min_x, max_x, min_y, max_y = path.bbox()
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    
    # Calculate scale factor for the approximate 0.5 unit outset.
    # An empirical factor (1.10) is a good starting point for a small, uniform increase.
    # NOTE: This factor may require tuning based on the average size of your bunkers.
    scale_factor = 1.10 

    # 3. Apply the scaling transformation
    # We apply the transformation to every point of every element in the simplified path.
    for element in path:
        # Check if the element has start and end points (covers Line, Arc, CubicBezier, etc.)
        if hasattr(element, 'start') and hasattr(element, 'end'):

            # Transform start and end points
            element.start = transform_point(element.start, center_x, center_y, scale_factor)
            element.end = transform_point(element.end, center_x, center_y, scale_factor)

            # Transform specific control points if they exist (e.g., CubicBezier)
            if hasattr(element, 'control1'):
                element.control1 = transform_point(element.control1, center_x, center_y, scale_factor)
            if hasattr(element, 'control2'):
                element.control2 = transform_point(element.control2, center_x, center_y, scale_factor)

    # 4. Return the new SVG path string using the Path.d() method
    return path.d()


def fix_bunker_paths(input_svg, output_svg):
    """
    Reads an SVG, identifies bunker paths, applies a geometric outset, 
    and writes the corrected paths to a new SVG.
    """
    try:
        # 1. Parse the SVG file
        tree = ET.parse(input_svg)
        root = tree.getroot()
        
        # Define SVG namespace
        NS = {'svg': 'http://www.w3.org/2000/svg'}
        
        print(f"Starting analysis of {input_svg} (Outset Distance: {OUTSET_DISTANCE_UNSCALED})...")
        
        fixed_count = 0

        # 2. Iterate through all <path> elements
        for path_elem in root.findall('.//svg:path', NS):
            path_id = path_elem.get('id', '')

            # 3. Identify Bunker Paths using the corrected ID format
            if BUNKER_TAG in path_id:
                d_attribute = path_elem.get('d')
                
                if d_attribute:
                    print(f" - Found and processing bunker: {path_id}")
                    # 4. Apply Geometric Outset
                    new_d = simplify_and_outset_path(d_attribute, OUTSET_DISTANCE_UNSCALED)                    
                    # 5. Update the SVG element
                    path_elem.set('d', new_d)
                    # Optional: Mark the path to show it was processed
                    path_elem.set('data-fixed', 'outset')
                    fixed_count += 1
        
        print(f"Processed and fixed {fixed_count} paths.")

        # 6. Save the modified tree to the new file
        tree.write(output_svg)
        print(f"Successfully wrote fixed SVG to {output_svg}")

    except FileNotFoundError:
        print(f"Error: Input file {input_svg} not found.")
        sys.exit(1)
    except ET.ParseError as e:
        print(f"Error parsing SVG XML: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Using default filenames; input={INPUT_FILENAME} output={OUTPUT_FILENAME}")
        print(f"  You can also use: python {sys.argv[0]} <input_svg_file> <output_svg_file>")
        input_file = INPUT_FILENAME
        output_file = OUTPUT_FILENAME
    #   sys.exit(1)
    else:    
        input_file = sys.argv[1] # Expected: smoothed_out.svg
        output_file = sys.argv[2] # Expected: final_smoothed_out.svg
    
    # Run the fix
    fix_bunker_paths(input_file, output_file)
