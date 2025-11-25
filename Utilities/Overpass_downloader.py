#!/usr/bin/env python3

"""
This program (Overpass_downloader.py) takes WGS 84 coordinates (like used in OpenStreetMap) as arguments and downloads a map.osm. You can then use that to run 'osm2svg_v4.py' to create an initial .SVG of the golf course (or what ever you desire). The Overpass Query Language (QL) query is now generated DYNAMICALLY by reading the tags from a provided styles.json file, making the process self-configuring and robust.
"""
import requests
import json
import sys
import time
import math

# Base URL for the Overpass API
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Output file name
OUTPUT_FILE = "map.osm"
# Styles file name
STYLES_FILE = "styles.json"

# --- Area Calculation Constants ---
# Maximum allowed area for the bounding box in square kilometers
MAX_AREA_KM2 = 8.0
# Mean Earth radius in kilometers (WGS-84)
EARTH_RADIUS_KM = 6371.0
# ----------------------------------


def degrees_to_radians(degrees):
    """Converts degrees to radians."""
    return degrees * math.pi / 180.0


def calculate_bbox_area(min_lat, min_lon, max_lat, max_lon):
    """
    Calculates the approximate area of the bounding box in square kilometers.
    Uses the distance along the meridians and the average distance along the parallels.
    """
    # 1. Calculate the North-South distance
    lat_distance = EARTH_RADIUS_KM * degrees_to_radians(max_lat - min_lat)

    # 2. Calculate the East-West distance
    # Use the average latitude for the parallel distance calculation
    avg_lat_rad = degrees_to_radians((min_lat + max_lat) / 2.0)
    lon_distance_factor = math.cos(avg_lat_rad)
    lon_distance = EARTH_RADIUS_KM * lon_distance_factor * degrees_to_radians(max_lon - min_lon)

    # 3. Calculate approximate area
    area_km2 = lat_distance * lon_distance
    return area_km2


def load_styles(filename):
    """Loads the OSM feature styles (key-value mapping) from a JSON file."""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: Configuration file '{filename}' not found.")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: Failed to decode JSON from '{filename}'. Check file format.")
        sys.exit(1)


def get_overpass_filters(styles_map):
    """
    Extracts the unique Overpass QL tag filters from the keys of the styles map.
    """
    filters = []
    for style_key in styles_map.keys():
        if style_key == "Comment":
            continue # Skip the comment entry

        # Check if the style key is in 'key.value' format (specific tag)
        if '.' in style_key:
            # Split by the first dot to get the key and value
            key, value = style_key.split('.', 1)
            # Specific tag filter: ["key"="value"]
            filters.append(f'["{key}"="{value}"]')
        else:
            # Generic tag filter: ["key"] (match any value for this key)
            filters.append(f'["{style_key}"]')

    # Ensure uniqueness and sort for stability
    return sorted(list(set(filters)))


def build_overpass_query(styles, bbox_coords):
    """
    Constructs the Overpass QL query string based on the derived filters and bounding box.
    This version includes the recursive descent command (._;>;) to ensure full geometry is fetched.
    """
    # Bounding box format: (min_lat, min_lon, max_lat, max_lon)
    bbox_str = ",".join(map(str, bbox_coords))

    # Get the unique, required tag filters from the style map keys
    tag_filters = get_overpass_filters(styles)

    query_parts = []
    # 1. Start the query with a timeout and the output format
    query_parts.append(f"[out:xml][timeout:180];\n")

    # 2. Define the search area and start the union block
    query_parts.append(f"// Collect all elements matching the style filters within the bounding box\n")
    query_parts.append(f"(\n")

    # 3. Add queries for all nodes, ways, and relations matching each filter
    for tag_filter in tag_filters:
        # Match nodes (n), ways (w), and relations (r) with the specific tag filter
        # Include the bounding box constraint in the query
        query_parts.append(f"  node{tag_filter}({bbox_str});\n")
        query_parts.append(f"  way{tag_filter}({bbox_str});\n")
        query_parts.append(f"  relation{tag_filter}({bbox_str});\n")

    query_parts.append(f");\n") # Close the union block

    # 4. CRITICAL: Recursively retrieve all dependent elements (nodes for ways, members for relations)
    query_parts.append(f"// Recursively retrieve all necessary dependent elements (nodes for ways, members for relations)\n")
    query_parts.append(f"(._;>;);\n") 
    
    # 5. Output the collected data with geometry
    query_parts.append(f"// Output the complete, fully-linked data set\n")
    query_parts.append(f"out geom;\n")

    return "".join(query_parts)


def fetch_overpass_data(query, max_retries=5):
    """
    Sends the constructed query to the Overpass API and handles rate limiting.
    """
    print("Executing Overpass QL query...")
    for attempt in range(max_retries):
        try:
            response = requests.post(OVERPASS_URL, data=query.encode('utf-8'))
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

            # Check for a successful response (usually 200 OK)
            if response.status_code == 200:
                print("Successfully received data from Overpass API.")
                return response.text

        except requests.HTTPError as e:
            if response.status_code in [429, 504]: # Too Many Requests, Gateway Timeout
                delay = 2 ** attempt  # Exponential backoff (1s, 2s, 4s, 8s, ...)
                print(f"Rate limit hit or timeout ({response.status_code}). Retrying in {delay}s...")
                time.sleep(delay)
                continue
            else:
                print(f"HTTP Error {response.status_code}: {e}")
                print("Response content (for debugging):\n", response.text[:500] + "...")
                return None
        except requests.RequestException as e:
            print(f"An error occurred during the API request: {e}")
            return None

    print(f"Failed to fetch data after {max_retries} attempts.")
    return None


def main():
    """Main function to run the downloader script."""
    # Check for the correct number of command-line arguments
    if len(sys.argv) != 5:
        print("Usage: python Overpass_downloader.py <lat1> <lon1> <lat2> <lon2>")
        print("\nExample (Small area near Paris, corners can be input in any order):")
        print("python Overpass_downloader.py 48.86 2.34 48.85 2.35") # Max, Min, Min, Max
        sys.exit(1)

    try:
        # Parse the two coordinate pairs the user provided (Corner 1 and Corner 2)
        lat1 = float(sys.argv[1])
        lon1 = float(sys.argv[2])
        lat2 = float(sys.argv[3])
        lon2 = float(sys.argv[4])
    except ValueError:
        print("Error: All four bounding box coordinates must be numeric.")
        sys.exit(1)

    # --- Normalize Coordinates ---
    # Ensure min_lat is the lower latitude and max_lat is the higher latitude
    min_lat = min(lat1, lat2)
    max_lat = max(lat1, lat2)
    # Ensure min_lon is the lower longitude and max_lon is the higher longitude
    min_lon = min(lon1, lon2)
    max_lon = max(lon1, lon2)

    bbox_coords = (min_lat, min_lon, max_lat, max_lon)
    # -----------------------------

    # --- Area Check ---
    area_km2 = calculate_bbox_area(min_lat, min_lon, max_lat, max_lon)
    print(f"Calculated bounding box area: {area_km2:.2f} km^2.")
    
    if area_km2 > MAX_AREA_KM2:
        print(f"\nError: Requested area ({area_km2:.2f} km²) exceeds the maximum limit of {MAX_AREA_KM2} km².")
        print("Please choose a smaller bounding box.")
        sys.exit(1)
    # ------------------

    print(f"Using normalized bounding box (min_lat, min_lon, max_lat, max_lon): {bbox_coords}")
    print(f"Loading styles and extracting filters from {STYLES_FILE}...")
    styles = load_styles(STYLES_FILE)

    query = build_overpass_query(styles, bbox_coords)
    
    print("\n--- Generated Overpass QL Query (for reference) ---\n")
    print(query.strip())
    print("\n-----------------------------------------------------\n")

    osm_data = fetch_overpass_data(query)

    if osm_data:
        # --- FIX: Inject the missing <bounds> tag in the correct location ---
        
        # 1. Create the <bounds> tag using the normalized coordinates
        bounds_tag = f'<bounds minlat="{min_lat}" minlon="{min_lon}" maxlat="{max_lat}" maxlon="{max_lon}"/>'
        
        # 2. Find the index of the CLOSING BRACKET of the <osm...> root tag.
        # We find the start of '<osm' and then search for the first '>' after that.
        osm_tag_start_index = osm_data.find('<osm')
        if osm_tag_start_index == -1:
            print("Error: Could not find the <osm> root tag in the response. Cannot inject bounds.")
            # Continue to write the file without bounds rather than failing entirely.
            inserted_data = osm_data
        else:
            # Find the closing bracket of the <osm...> tag
            osm_tag_end_index = osm_data.find('>', osm_tag_start_index)
            
            # 3. Find the newline that immediately follows the <osm> tag line
            # This ensures we insert the bounds tag on its own line after the root tag.
            first_newline_after_osm = osm_data.find('\n', osm_tag_end_index)
            
            if first_newline_after_osm != -1:
                # Insert the bounds tag on its own line immediately after the <osm> tag line.
                inserted_data = (
                    osm_data[:first_newline_after_osm + 1] +  # XML declaration + <osm...> tag + its newline
                    bounds_tag + "\n" +                 # The bounds tag followed by a newline
                    osm_data[first_newline_after_osm + 1:]    # The rest of the data (starting with <note>, <meta>, etc.)
                )
                print("Injected <bounds> tag on a new line immediately following the <osm> root tag.")
            else:
                # Fallback: if there's no newline, just insert it immediately after the >
                inserted_data = (
                    osm_data[:osm_tag_end_index + 1] + 
                    bounds_tag + 
                    osm_data[osm_tag_end_index + 1:]
                )
                print("Injected <bounds> tag using fallback method (no newline found after <osm>).")


        # Write the modified OSM XML data to the output file
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(inserted_data)
            
        print(f"\n✅ Data successfully written to {OUTPUT_FILE}")
        print(f"   (XML structure is now correct for <bounds> placement.)")
    else:
        print("\n❌ Failed to generate map file. See errors above.")


if __name__ == "__main__":
    main()
