# osm2svg4opcd
Openstreetmap to SVG formatted for the Open Platform Course Design (Quick GSPro/Inkscape Assist)

OSM2SVG4OPCD is a tool to help map out golf courses quickly, with the goal of creating a file ready to pass through the 'Clender' step in the OPCD process of building Unity/GSPro golf simulator courses. The identification of features and the assignment of colors, paths, and styles is controlled by the styles.json file.  The idea is to: go to https://openstreetmap.org and search for the golf course you would like.  You would provide it with the longitude and latitudes of the image to map that would match the Inner Lidar/DEM images used for the terrain.  You would run the program osm2svg_v4.py and it would create the SVG image with all the features outlined and mapped into an OPCD 'Clender' passible '.svg' file.

OpenStreetMap is a fantastic community-driven mapping service with many users, both professional and amateur. Volunteers use the OpenStreetMap editor to identify features from aerial imagery of land and tag them. The resulting maps are distributed under the 'Open Data Commons Open Database' License (ODbL). https://opendatacommons.org/licenses/odbl/1-0.   'map.osm' (from OpenStreetMap's 'Export' function) is a special XML-based file language that features identified labeled with a key and value pair.  For example, the XML "tag k='streetname' v='You streetname here'" might be one such.  The list of keys (there are hundreds) is provided on the OpenStreetMap website.   The mapping of the keys/values to an SVG object is controlled by the styles.json file.  You can add buildings by modifying the styles.json to include that type of tag in the search list.

Note this is a work in progress, and I'm in the process of developing functions to locate Strokes and paths that may overlap. Second, roads, cartpaths, and trails need to have any sharp corners converted to curves (fellet) similar to how Inkscape processes them.


License: My code (osm2svg_v4.py, svg_points2path.py, and styles.json) is released under the MIT license.
The 'map.osm' is covered under the ODbL license.  

Now it's a three step processes 1) Assuming you have  map.osm from OpenStreetMap export function run 'python3 osm2svg_v4.py" to create out.svg.
2) run "python3 svg_points2path.py" to create path_out.svg and smoothed_out.svg from out.svg.  The smoothed_out.svg will pass Clender's color checks but fails on bunker inset.
3) run "fix_bunker_inset.py" to create final_smoothed_out.svg from smoothed_out.svg.   (Work in progress, still broken)

KNOWN BUGS and things to work on:
We now can pass the precut pd the  OPCD cloud tool 'Clender'.  Yeah!

The out.svg created by osm2svg_v4.py contains several <code><polyline></code> line segments that Clender objects to (usually reported as a 'Color Error'.  To resolve that, I have a second-stage program, svg_points2path, that will clear the issue.   It converts the <polyline> to <path> structures in the 'out.svg'.   The output from that step is two files called 'paths_out.svg' and 'smoothed_out.svg'.  It also performs auto-smooth curves (Bezier curves) of the node points and outputs that into 'smoothed_out.svg'.  Hopefully, this will improve the 'Clender' processing.  I've found sandtraps to be a tricky feature with 'Clender' as 'Clender' performs a two-step inset on the sandtrap shapes, and if the sandtraps are too narrow or not rounded enough, that process will fail.   I'm still looking for a solution (coding rules) to create those properly and maintain their shape.  The Inset issue is tricky for sure.  Also need to look at the size of the SVG may need adjustments to match the Lidar terrain in Blender. 

Side Motivation:  One aspect of the auto-segmented golf courses is the need for labeled courses to provide training data for an AI like the SAM2 system or others computer vision tools.  This tool should help provide that training data.   


