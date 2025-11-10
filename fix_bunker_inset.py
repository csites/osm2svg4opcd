#!/usr/bin/env python3
"""
Fix_bunker_inset.py
This program is the third program to run in the chain.  1st run osm2svg_v4.py to convert the OpenStreetMap export 'map.osm' into out.svg.
2nd.  Run svg_points2path.py to create the smoothed_out.svg (the smoothed version of all shapes.   The run this program last, which will
use the smoothed_out.svg to locate all the bunkers and apply and 0.5 outset and smoothing.  The output file will be and SVG with the adjusted
bunker shapes called fixed_bunkers.svg
 """

import sys
import xml.etree.ElementTree as ET
# You will need to install a geometry library for the actual offset/outset:
# import pyclipper # or shapely, etc.

# --- Global Configuration ---
# The target feature to fix (from the SVG path ID)
BUNKER_TAG = "golf.bunker"
# The distance for the geometric outset (this clears tiny self-intersections)
OUTSET_DISTANCE = 0.5 

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
        
        print(f"Starting analysis of {input_svg}...")

        # 2. Iterate through all <path> elements
        for path_elem in root.findall('.//svg:path', NS):
            path_id = path_elem.get('id', '')

            # 3. Identify Bunker Paths using the ID
            if BUNKER_TAG in path_id:
                d_attribute = path_elem.get('d')
                
                if d_attribute:
                    print(f"Found and processing bunker: {path_id}")
                    
                    # 4. Apply Geometric Outset (Placeholder)
                    # This step is the most complex and relies on a geometry library.
                    # It involves:
                    #   a. Converting the SVG path string (d_attribute) into a usable polygon object.
                    #   b. Applying an offset/outset of OUTSET_DISTANCE to this object.
                    #   c. Converting the resulting polygon(s) back into a simplified SVG path string (new_d).
                    
                    # For now, we'll simulate the successful path, 
                    # but you must replace this with actual geometry code.
                    new_d = apply_outset_to_d_attribute(d_attribute, OUTSET_DISTANCE)
                    
                    # 5. Update the SVG element
                    path_elem.set('d', new_d)
                    
                    # Optional: Mark the path to show it was processed
                    path_elem.set('data-fixed', 'outset')

        # 6. Save the modified tree to the new file
        tree.write(output_svg)
        print(f"Successfully wrote fixed SVG to {output_svg}")

    except FileNotFoundError:
        print(f"Error: Input file {input_svg} not found.")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)


def apply_outset_to_d_attribute(d_string, offset_distance):
    """
    Placeholder function for the geometric operation. 
    Requires a geometry library (e.g., pyclipper, shapely).
    """
    # --- THIS PART REQUIRES AN EXTERNAL LIBRARY ---
    # Example logic using pyclipper concept:
    # 1. Parse d_string to a list of coordinates (polygons)
    # 2. Scale coordinates up (pyclipper works best with integers)
    # 3. Use pyclipper.Clipper.OffsetPolygons()
    # 4. Scale coordinates down
    # 5. Convert resultant coordinates back to SVG path 'd' string
    
    # For now, return the original string or a placeholder path
    # NOTE: You MUST implement the actual geometry logic here.
    return d_string 


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <input_svg_file> <output_svg_file>")
        sys.exit(1)
        
    input_file = sys.argv[1] # e.g., smoothed_out.svg
    output_file = sys.argv[2] # e.g., final_smoothed_out.svg
    
    # The current command line step will be:
    # python fix_bunker_inset.py smoothed_out.svg final_smoothed_out.svg
    
    fix_bunker_paths(input_file, output_file)
    
