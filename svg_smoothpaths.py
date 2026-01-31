#!/usr/bin/env python3
"""
svg_points2path.py takes out.svg and locates all polylines and converts them to linesegments and points.
the output file is called paths_out.svg.  The next step is that we apply smoothing technique to the line segments
and points.  This basically mimics 3 Inkscape steps.   Select the paths, convert line segments to curves, and
the apply auto-smooth.  The output file is called smoothed_out.svg.
"""
import xml.etree.ElementTree as ET
from svgpathtools import parse_path, Path, Line, CubicBezier
import numpy as np
import re

SMOOTH_TIGHTNESS_FACTOR = 0.23 # Should be =< 0.33. Was 0.375.

def get_auto_smooth_controls(P_prev, P_i, P_next, tightness_factor=SMOOTH_TIGHTNESS_FACTOR):
    """
    Calculates the control points C1 (back handle) and C2 (front handle) 
    for an Auto-Smooth node P_i, based on Inkscape's algorithm.

    :param P_prev: The preceding node (complex number).
    :param P_i: The current node (complex number).
    :param P_next: The succeeding node (complex number).
    :return: A tuple (C1, C2) of the two new control points.
    NOTE: svgpathtools uses complex numbers for points (x + y*j)

    """
    
    # 1. Calculate Segment Vectors and Lengths
    V_prev = P_i - P_prev
    V_next = P_next - P_i
    L_prev = abs(V_prev)
    L_next = abs(V_next)

    # Handle case where nodes are coincident (retract handles)
    if L_prev == 0 or L_next == 0:
        return P_i, P_i

    # 2. Calculate Direction Vector D (related to the angle bisector)
    # D = (L_prev / L_next) * V_next - V_prev
    D = (L_prev / L_next) * V_next - V_prev
    
    # Handle D being zero (Collinear, equi-spaced case) 
    if abs(D) == 0:
        # If D is the zero vector, P_prev, P_i, and P_next are perfectly collinear 
        # and proportional (often L_prev == L_next). No smoothing should happen.
        return P_i, P_i 
    
    # 3. Calculate Unit Tangent Vector T
    # Tangent T is 90 degrees to D. In complex numbers, multiply by +/- 1j
    # We use -1j for one direction (as per Inkscape's internal logic for one handle)
    # The unit vector is D / abs(D)
    
    # Let's try flipping the T_unit direction only if the angle is reflex ( > 180 degrees)
    # The angle is reflex if the Z-component of (V_prev x V_next) has the wrong sign.
    
    signed_angle_z = V_prev.real * V_next.imag - V_prev.imag * V_next.real
    if signed_angle_z < 0: # This is the reflex angle condition
        # D must point towards the interior of the shape.
        # We must ensure T_unit is rotated in the correct direction.
        T_unit = (D / abs(D)) * (1j) # Use +1j instead of -1j
    else:
        T_unit = (D / abs(D)) * (-1j)
    
    # 4. Calculate Control Points (C1 and C2)
    #   Constrain handle length by the minimum adjacent segment length (for long straight roads, and paths)
    # This prevents long segments from making the handle too long for tight corners.
    L_min = min(L_prev, L_next)
    
    # Calculate a single, constrained handle length
    handle_len = (L_min / 3.0) * tightness_factor
    
    # Both handles use the same constrained length
    handle_len_prev = handle_len 
    handle_len_next = handle_len

    # Scale the handle length by the tightness_factor (e.g., 0.5 for half length)
    # handle_len_prev = (L_prev / 3) * tightness_factor
    # handle_len_next = (L_next / 3) * tightness_factor
    
    C1 = P_i - T_unit * handle_len_prev # Back handle (control point for the previous segment)
    C2 = P_i + T_unit * handle_len_next # Front handle (control point for the current segment)

    return C1, C2


def smooth_path_segments(original_path_segments, is_closed, tightness_factor=SMOOTH_TIGHTNESS_FACTOR):
    """
    Applies an Inkscape like "Make Segments Curves" and "Auto-Smooth" logic 
    to a sequence of path segments, while respecting rings and holes.

    """
    """
    Applies Auto-Smooth logic while respecting sub-paths (rings/holes).
    """
    if not original_path_segments:
        return []

    # --- STEP 1: Split segments into independent rings ---
    rings = []
    current_ring = []
    
    for seg in original_path_segments:
        current_ring.append(seg)
        # If the end of this segment meets the start of the current ring, it's a closed loop
        # We use a tiny tolerance for float math (1e-4)
        if abs(seg.end - current_ring[0].start) < 1e-4:
            rings.append(current_ring)
            current_ring = []
    
    # Catch any remaining segments (for open paths)
    if current_ring:
        rings.append(current_ring)

    final_output_segments = []

    # --- STEP 2: Smooth each ring independently ---
    for ring in rings:
        # Convert segments to basic CubicBezier placeholders
        cubic_segments = []
        for seg in ring:
            if isinstance(seg, Line):
                P1, P2 = seg.start, seg.end
                V = P2 - P1
                cubic_segments.append(CubicBezier(P1, P1 + V/3, P2 - V/3, P2))
            else:
                cubic_segments.append(seg)

        # Extract nodes for this specific ring
        nodes = [seg.start for seg in cubic_segments]
        nodes.append(cubic_segments[-1].end)
        
        num_nodes = len(nodes)
        # Check if the ring is actually closed (important for smoothing logic)
        ring_closed = abs(nodes[0] - nodes[-1]) < 1e-4

        if num_nodes <= 2:
            final_output_segments.extend(cubic_segments)
            continue

        # Smoothing Loop for this specific ring
        for j in range(num_nodes - 1):
            P_i = nodes[j]
            P_next = nodes[j+1]

            # P_prev logic restricted to THIS ring
            if j == 0:
                P_prev = nodes[-2] if ring_closed else P_i
            else:
                P_prev = nodes[j-1]

            # P_next_next logic restricted to THIS ring
            if j == num_nodes - 2:
                P_next_next = nodes[1] if ring_closed else P_next
            else:
                P_next_next = nodes[j+2]

            # --- Calculate Handles ---
            if ring_closed or j > 0:
                _, C2_i = get_auto_smooth_controls(P_prev, P_i, P_next, tightness_factor)
            else:
                C2_i = P_i + (P_next - P_i) * (tightness_factor / 3.0)

            if ring_closed or j < num_nodes - 2:
                C1_next, _ = get_auto_smooth_controls(P_i, P_next, P_next_next, tightness_factor)
            else:
                C1_next = P_next - (P_next - P_i) * (tightness_factor / 3.0)

            final_output_segments.append(CubicBezier(P_i, C2_i, C1_next, P_next))

    return final_output_segments


def apply_path_smoothing(svg_filepath):
    """
    Finds all <path> elements, applies auto-smoothing, and updates the SVG content.
    This function should be called AFTER convert_polylines_to_paths.

    :param svg_filepath: The path to the SVG file.
    :return: The modified SVG content as a string.
    """
    # 1. Parse the SVG content (assuming polylines are already converted in memory)
    ET.register_namespace('', "http://www.w3.org/2000/svg")
    
    # Read the file content and parse it
    tree = ET.parse(svg_filepath)
    root = tree.getroot()
    
    # Find all path elements
    # We use the full namespaced tag for accuracy: {http://www.w3.org/2000/svg}path
    for path_element in root.iter('{http://www.w3.org/2000/svg}path'):
        path_d_string = path_element.get('d')
        if path_d_string:
            try:
                original_path = parse_path(path_d_string)
                
                # Determine if the path is closed (used for loop logic)
                is_closed = (original_path.start == original_path.end)
                
                # Apply the smoothing logic
                smoothed_segments = smooth_path_segments(original_path, is_closed)
                
                # Re-assemble the path and generate the new 'd' string
                smoothed_path = Path(*smoothed_segments)
                new_d_string = smoothed_path.d()
                new_d_string = re.sub(
                    r"(\d+\.\d{4,})", 
                    lambda m: f"{float(m.group(1)):.3f}", 
                    new_d_string
                )
                
                if is_closed:
                    # svgpathtools.d() only adds 'z' if the last segment is a Line/Cubic 
                    # ending at the start point. Explicitly appending ' Z' is safest.
                    if new_d_string[-1].upper() != 'Z':
                        new_d_string += ' Z' 
                
                # Update the 'd' attribute of the XML element
                path_element.set('d', new_d_string)
                
            except Exception as e:
                print(f"⚠️ Warning: Could not smooth path element. Error: {e}")
    # Write the modified content back to a string
    return ET.tostring(root, encoding='utf-8').decode('utf-8')

def convert_polylines_to_paths(svg_filepath):

    """
    Converts all <polyline> elements in an SVG file to <path> elements
    by manually tracking the parent element.

    :param svg_filepath: The path to the SVG file.
    :return: The modified SVG content as a string.
    """
    # 1. Parse the SVG file
    # Register the default namespace to ensure elements are found correctly
    ET.register_namespace('', "http://www.w3.org/2000/svg")
    tree = ET.parse(svg_filepath)
    root = tree.getroot()
    
    # Define the SVG namespace for searching
    ns = {'svg': 'http://www.w3.org/2000/svg'}

    # Use ET.iter() to iterate over all elements and find their parent
    # The parent of the root is None, so we handle that case.
    
    # We collect replacements to perform them AFTER iteration, 
    # as modifying the tree while iterating can cause issues.
    replacements = [] 

    # Find ALL elements in the tree (the parent of the root is None)
    for parent in root.iter():
        # Iterate over the parent's immediate children
        for i, child in enumerate(list(parent)): # Use list() copy to avoid iteration issues
            
            # Check if the child is a polyline element
            # We must use the full namespaced tag, e.g., {namespace}polyline
            if child.tag == '{http://www.w3.org/2000/svg}polyline':
                
                polyline = child
                points_data = polyline.get('points')
                
                if points_data:
                    # 2. Construct the path 'd' attribute string
                    # 'M' (MoveTo) followed by the rest of the coordinates
                    path_d = f"M {points_data.strip()}"
                    
                    # 3. Create the new <path> element
                    path = ET.Element('path')
                    
                    # 4. Transfer all attributes
                    for name, value in polyline.attrib.items():
                        if name != 'points': # Skip the old attribute
                            path.set(name, value)
                    
                    # 5. Set the new 'd' attribute
                    path.set('d', path_d)
                    
                    # 6. Store the replacement instruction (parent, old_index, new_element)
                    replacements.append((parent, i, path))

    # 7. Perform the replacements after the search is complete
    for parent, index, new_path in reversed(replacements):
        # Insert the new path at the original index
        parent.insert(index, new_path)
        # Remove the old polyline (which is now at index + 1)
        # We perform in reverse order of indices to prevent shifting issues.
        del parent[index + 1] 

    # 8. Write the modified content back to a string
    return ET.tostring(root, encoding='utf-8').decode('utf-8')


def smooth_svg_path_corrected(svg_path_d_string):
    """
    Applies an auto-smooth-like operation to a given SVG path string.
    This example correctly shows the path reassembly.
    """
    
    # 1. Parse the path string into a sequence of segments
    original_path_segments = parse_path(svg_path_d_string)
    
    # List to hold the newly calculated (smoothed) segments
    new_segments = []
    
    # The 'Auto-Smooth' logic primarily works on the nodes (end points of segments).
    # It calculates the new control points (C1 and C2) for the segment *leaving* the node
    # and the segment *entering* the node.
    
    # --- Simplified Loop Structure (Focus on Reassembly) ---
    for i in range(len(original_path_segments)):
        # In a real implementation, you would need P_i-1, P_i, and P_i+1 to calculate 
        # the new control points for the segment starting at P_i.
        
        current_segment = original_path_segments[i]
        
        # In a full implementation, the Auto-Smooth calculation would happen here:
        
        # 1. Get the current node P_i (which is current_segment.start)
        P_i = current_segment.start
        
        # 2. Call the smoothing function (conceptually):
        C1_new, C2_new = get_auto_smooth_controls(P_prev, P_i, P_next)
        
        # 3. Create a NEW CubicBezier segment using the new control points (C1_new, C2_new)
        # For simplicity in this example, we'll just convert everything to a basic CubicBezier 
        # to demonstrate the reassembly process.
        
        # --- PATH MODIFICATION (The "Make Segments Curves" + "Auto-Smooth" part) ---
        if isinstance(current_segment, Line):
            # If it's a straight line, convert it to a CubicBezier with placeholder controls
            # A real implementation would calculate smooth controls based on neighbors
            # P_start = current_segment.start
            # P_end = current_segment.end
            # Placeholder for *properly smoothed* segment:
            
            # For demonstration, we'll assume we have a new, smoothed segment:
            new_segment = current_segment.to_cubic_bezier() # Example conversion
            
        else:
            # If it's already a curve, modify its existing control points C1/C2 
            # using the Auto-Smooth geometric calculation.
            new_segment = current_segment
        
        # Add the modified (or new) segment to the list
        new_segments.append(new_segment)


    # 4. Re-assemble the path and return the new d string
    # The Path constructor takes the list of segments and handles the start/end points
    smoothed_path = Path(*new_segments)
    
    # The .d() method serializes the path object back into the SVG path data string
    return smoothed_path.d()



# --- MAIN:  Usage Example ---
INPUT_FILE = 'out.svg'
TEMP_PATH_FILE = 'paths_out.svg'
FINAL_SMOOTH_FILE = 'smoothed_out.svg'

try:
    # 1. Convert polylines to paths and save the result
    modified_svg_content = convert_polylines_to_paths(INPUT_FILE)
    with open(TEMP_PATH_FILE, 'w') as f:
        f.write(modified_svg_content)
    print(f"\n✅ Step 1: Polylines converted to Paths. Saved to '{TEMP_PATH_FILE}'.")
    
    # 2. Apply smoothing to the newly created path elements
    final_svg_content = apply_path_smoothing(TEMP_PATH_FILE)

    # 3. Save the final smoothed output
    with open(FINAL_SMOOTH_FILE, 'w') as f:
        f.write(final_svg_content)
        
    print(f"✅ Step 2: Paths smoothed using Inkscape's auto-smooth logic. Final output saved to '{FINAL_SMOOTH_FILE}'.")

except FileNotFoundError:
    print(f"\n❌ Error: The file '{INPUT_FILE}' was not found. Please make sure the file exists.")
except Exception as e:
    print(f"\n❌ An error occurred during processing: {e}")

    
