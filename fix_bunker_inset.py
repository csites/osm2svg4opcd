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
import pyclipper

# --- Global Configuration ---
BUNKER_TAG = "golf.bunker"
# The distance for the geometric outset (this clears tiny self-intersections)
# pyclipper works best with integers, so we'll use a high scaling factor.

INPUT_FILENAME="smoothed_out.svg"
OUTPUT_FILENAME="final_smoothed_out.svg"

CLIPPER_SCALE_FACTOR = 1000.0
BOUNDARY_OUTSET = 1.00  # 1.5 clears the 'Clender' but appears excessive

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


def simplify_and_offset_path(d_string, offset_value):
    """
    Performs a TRUE uniform geometric offset (outset/inset) using pyclipper.
    This replaces the unreliable proportional scaling method.
    """
    try:
        path_obj = parse_path(d_string)
    except Exception as e:
        print(f"Warning: Failed to parse SVG path: {e}")
        return d_string

    # 1. NEW ROBUST SAMPLING: Convert SVG path to a high-precision polygon
    #    Iterate over the entire path object using its 'point(t)' method.
    num_samples = 100 # Number of points to approximate the Bezier curve
    t_values = np.linspace(0, 1, num_samples, endpoint=True)
    sampled_points = []

    for t in t_values:
        point = path_obj.point(t) # Get the complex number (x + iy)
        
        # Convert complex numbers to (x, y) tuples and scale for pyclipper
        scaled_x = int(point.real * CLIPPER_SCALE_FACTOR)
        scaled_y = int(point.imag * CLIPPER_SCALE_FACTOR)
        
        # Avoid adding duplicate points if the Bezier segments meet perfectly
        if not sampled_points or (scaled_x, scaled_y) != sampled_points[-1]:
            sampled_points.append((scaled_x, scaled_y))


    # 2. Configure pyclipper and apply the uniform offset
    #    We must use a list of lists for pyclipper to define the polygon input
    pyclipper_input = [sampled_points]

    pco = pyclipper.PyclipperOffset()
    # Offset is scaled to integer space
    scaled_offset = int(offset_value * CLIPPER_SCALE_FACTOR)
    
    # Add the path, marking it as a closed loop
    pco.AddPath(pyclipper_input[0], pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    
    # Execute the offset
    offset_polygons = pco.Execute(scaled_offset)

    # 3. Convert the resulting polygons back to an SVG path string (M...L...Z)
    new_d_string = ""
    # We assume the largest resulting polygon is the correct offset shape
    if offset_polygons:
        # Find the largest polygon by area/length
        largest_poly = max(offset_polygons, key=len)
        
        # Convert the first polygon back to a basic SVG path string
        start_x = largest_poly[0][0] / CLIPPER_SCALE_FACTOR
        start_y = largest_poly[0][1] / CLIPPER_SCALE_FACTOR
        new_d_string += f"M {start_x:.3f},{start_y:.3f} "
        
        # Follow with Line commands for the rest of the points
        for x, y in largest_poly[1:]:
            unscaled_x = x / CLIPPER_SCALE_FACTOR
            unscaled_y = y / CLIPPER_SCALE_FACTOR
            new_d_string += f"L {unscaled_x:.3f},{unscaled_y:.3f} "
            
        new_d_string += "Z" # Close the path
    else:
        # If offsetting failed to produce a result, return the original
        return d_string

    return new_d_string


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
        
        print(f"Starting analysis of {input_svg} (Outset Distance: {BOUNDARY_OUTSET})...")
        
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
                    new_d = simplify_and_offset_path(d_attribute, BOUNDARY_OUTSET)                    
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
