#!/usr/bin/env python3
"""
svg_points2path.py takes out.svg and locates all polylines and converts them to linesegments and points.
It cleans attributes for Clender (removing strokes, enforcing fills) and applies Inkscape-style smoothing.
"""
import xml.etree.ElementTree as ET
from svgpathtools import parse_path, Path, Line, CubicBezier
import numpy as np

SMOOTH_TIGHTNESS_FACTOR = 0.375
# Attributes that Clender does not like:
FORBIDDEN_ATTRIBUTES = ['stroke', 'stroke-width', 'stroke-cap', 'stroke-linejoin', 'stroke-opacity', 'stroke-dasharray']

def sanitize_attributes_for_clender(element, original_attribs):
    """
    Removes stroke attributes and ensures a fill attribute exists.
    If fill is missing or 'none', it tries to use the stroke color.
    """
    stroke_color = original_attribs.get('stroke')
    fill_color = original_attribs.get('fill')

    # 1. Determine the Fill Color
    # If there is no fill (or it is 'none'), use the stroke color.
    # If there is a fill, keep it.
    if fill_color and fill_color.lower() != 'none':
        final_fill = fill_color
    elif stroke_color and stroke_color.lower() != 'none':
        final_fill = stroke_color
    else:
        # Fallback if neither exist (Blender might need something, defaulting to black)
        final_fill = "#000000"

    # 2. Set the mandatory Fill
    element.set('fill', final_fill)

    # 3. Copy other attributes, skipping the forbidden ones and the ones we just handled
    for name, value in original_attribs.items():
        if name not in FORBIDDEN_ATTRIBUTES and name != 'fill' and name != 'points' and name != 'd':
            element.set(name, value)

def get_auto_smooth_controls(P_prev, P_i, P_next, tightness_factor=SMOOTH_TIGHTNESS_FACTOR):
    """
    Calculates the control points C1 (back handle) and C2 (front handle) 
    for an Auto-Smooth node P_i, based on Inkscape's algorithm.
    """
    
    # 1. Calculate Segment Vectors and Lengths
    V_prev = P_i - P_prev
    V_next = P_next - P_i
    L_prev = abs(V_prev)
    L_next = abs(V_next)

    # Handle case where nodes are coincident (retract handles)
    if L_prev == 0 or L_next == 0:
        return P_i, P_i

    # 2. Calculate Direction Vector D
    D = (L_prev / L_next) * V_next - V_prev
    
    # Handle D being zero (Collinear, equi-spaced case) 
    if abs(D) == 0:
        return P_i, P_i 
    
    # 3. Calculate Unit Tangent Vector T
    signed_angle_z = V_prev.real * V_next.imag - V_prev.imag * V_next.real
    if signed_angle_z < 0: 
        T_unit = (D / abs(D)) * (1j) 
    else:
        T_unit = (D / abs(D)) * (-1j)
    
    # 4. Calculate Control Points (C1 and C2)
    L_min = min(L_prev, L_next)
    
    # Calculate a single, constrained handle length
    handle_len = (L_min / 3.0) * tightness_factor
    
    C1 = P_i - T_unit * handle_len # Back handle 
    C2 = P_i + T_unit * handle_len # Front handle 

    return C1, C2

def smooth_path_segments(original_path_segments, is_closed, tightness_factor=SMOOTH_TIGHTNESS_FACTOR):
    """
    Applies Inkscape like "Make Segments Curves" and "Auto-Smooth" logic.
    """
    if not original_path_segments:
        return []

    # 1. Conversion to CubicBezier
    cubic_segments = []
    for seg in original_path_segments:
        if isinstance(seg, Line):
            P1 = seg.start
            P2 = seg.end
            V = P2 - P1
            C1 = P1 + V / 3.0
            C2 = P2 - V / 3.0
            cubic_segments.append(CubicBezier(P1, C1, C2, P2))
        else:
            cubic_segments.append(seg)
    
    # 2. Node Extraction
    nodes = [seg.start for seg in cubic_segments]
    nodes.append(cubic_segments[-1].end)
    
    final_segments = []
    num_nodes = len(nodes)
    
    if num_nodes <= 2:
        return cubic_segments
        
    # 3. Smoothing Loop
    for j in range(num_nodes - 1):
        P_i = nodes[j]         
        P_next = nodes[j+1]    
        
        # Determine P_prev
        if j == 0:
            P_prev = nodes[-2] if is_closed else P_i
        else:
            P_prev = nodes[j-1]
            
        # Determine P_next_next
        if j == num_nodes - 2: 
            P_next_next = nodes[1] if is_closed else P_next
        else:
            P_next_next = nodes[j+2]

        # --- C2_i: Outgoing handle from P_i ---
        if is_closed or j > 0:
            _, C2_i = get_auto_smooth_controls(P_prev, P_i, P_next, tightness_factor)  
        else:
            C2_i = P_i + (P_next - P_i) * (tightness_factor / 3.0)  

        # --- C1_next: Incoming handle to P_next ---
        if is_closed or j < num_nodes - 2:
            C1_next, _ = get_auto_smooth_controls(P_i, P_next, P_next_next, tightness_factor)
        else:
            C1_next = P_next - (P_next - P_i) * (tightness_factor / 3.0)
            
        new_seg = CubicBezier(P_i, C2_i, C1_next, P_next)
        final_segments.append(new_seg)

    return final_segments

def apply_path_smoothing(svg_filepath):
    """
    Finds all <path> elements, applies auto-smoothing, and cleans attributes for Clender.
    """
    ET.register_namespace('', "http://www.w3.org/2000/svg")
    
    tree = ET.parse(svg_filepath)
    root = tree.getroot()
    
    for path_element in root.iter('{http://www.w3.org/2000/svg}path'):
        # 1. Sanitize Attributes (Remove stroke, ensure Fill)
        # We must clone the attribs dict because we are modifying the element
        current_attribs = dict(path_element.attrib)
        # Clear existing attribs to rebuild them cleanly
        path_element.attrib.clear()
        sanitize_attributes_for_clender(path_element, current_attribs)

        # 2. Smooth Geometry
        path_d_string = current_attribs.get('d')
        if path_d_string:
            try:
                original_path = parse_path(path_d_string)
                
                # Check closed status. 
                # CRITICAL: If converting lines to filled polys, we often need to force close them.
                is_closed = (original_path.start == original_path.end)
                
                smoothed_segments = smooth_path_segments(original_path, is_closed)
                
                smoothed_path = Path(*smoothed_segments)
                new_d_string = smoothed_path.d()

                # 3. FORCE CLOSE PATH ('Z')
                # Clender needs a closed loop to create a mesh face. 
                # If the path isn't closed, the fill might look weird or fail.
                if new_d_string[-1].upper() != 'Z':
                    new_d_string += ' Z' 
                
                path_element.set('d', new_d_string)
                
            except Exception as e:
                print(f"⚠ Warning: Could not smooth path element. Error: {e}")
                # If smoothing fails, ensure we still put back the 'd' attrib
                path_element.set('d', path_d_string)

    return ET.tostring(root, encoding='utf-8').decode('utf-8')

def convert_polylines_to_paths(svg_filepath):
    """
    Converts <polyline> to <path>, stripping strokes and enforcing fills.
    """
    ET.register_namespace('', "http://www.w3.org/2000/svg")
    tree = ET.parse(svg_filepath)
    root = tree.getroot()
    
    replacements = []  

    for parent in root.iter():
        for i, child in enumerate(list(parent)): 
            if child.tag == '{http://www.w3.org/2000/svg}polyline':
                
                polyline = child
                points_data = polyline.get('points')
                
                if points_data:
                    # Construct basic path data
                    path_d = f"M {points_data.strip()}"
                    
                    # Create new path element
                    path = ET.Element('path')
                    
                    # --- SANITIZE ATTRIBUTES HERE ---
                    sanitize_attributes_for_clender(path, polyline.attrib)
                    
                    # Set the 'd' attribute
                    path.set('d', path_d)
                    
                    replacements.append((parent, i, path))

    # Perform replacements
    for parent, index, new_path in reversed(replacements):
        parent.insert(index, new_path)
        del parent[index + 1]  

    return ET.tostring(root, encoding='utf-8').decode('utf-8')


# --- MAIN ---
INPUT_FILE = 'out.svg'
TEMP_PATH_FILE = 'paths_out.svg'
FINAL_SMOOTH_FILE = 'smoothed_out.svg'

try:
    # 1. Convert polylines to paths (and strip strokes/add fills)
    modified_svg_content = convert_polylines_to_paths(INPUT_FILE)
    with open(TEMP_PATH_FILE, 'w') as f:
        f.write(modified_svg_content)
    print(f"\n✅ Step 1: Polylines converted to Paths (Strokes removed, Fills added). Saved to '{TEMP_PATH_FILE}'.")
    
    # 2. Apply smoothing and ensure final cleanup/closing
    final_svg_content = apply_path_smoothing(TEMP_PATH_FILE)

    with open(FINAL_SMOOTH_FILE, 'w') as f:
        f.write(final_svg_content)
        
    print(f"✅ Step 2: Paths smoothed & finalized for Clender. Saved to '{FINAL_SMOOTH_FILE}'.")

except FileNotFoundError:
    print(f"\n❌ Error: The file '{INPUT_FILE}' was not found.")
except Exception as e:
    print(f"\n❌ An error occurred: {e}")
    
