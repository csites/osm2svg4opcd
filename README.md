# osm2svg4opcd
Openstreetmap to SVG formatted for the Open Platform Course Design (Quick GSPro/Inkscape Assist)

OSM2SVG4OPCD is a tool to help map out golf courses quickly, with the goal of creating a file ready to pass through the 'Clender' step in the OPCD process of building Unity/GSPro golf simulator courses. The identification of features and the assignment of colors, paths, and styles is controlled by the styles.json file.  The idea is to: go to https://openstreetmap.org and search for the golf course you would like.  You would provide it with the longitude and latitudes of the image to map that would match the Inner Lidar/DEM images used for the terrain.  You would run the program osm2svg_v4.py and it would create the SVG image with all the features outlined and mapped into an OPCD 'Clender' passible '.svg' file.

OpenStreetMap is a fantastic community-driven mapping service with many users, both professional and amateur. Volunteers use the OpenStreetMap editor to identify features from aerial imagery of land and tag them. The resulting maps are distributed under the 'Open Data Commons Open Database' License (ODbL). https://opendatacommons.org/licenses/odbl/1-0.   Map.osm (from OpenStreetMap's 'Export' function) is a special xml based language that with most features identified labeled with a key and value pair.  For example, '<tag k="streetname" v="You streetname here"/>' might be one tag.  The list of keys (there are hundreds) is provided on the OpenStreetMap website.   The mapping of the keys to SVG object parameters is contained in the styles.json file.

Note this is a work in progress, and I'm in the process of developing functions to locate Strokes and paths that may overlap. Second, roads, cartpaths, and trails need to have any sharp corners converted to curves (fellet) similar to how Inkscape processes them.


License: My code (osm2svg_v4.py, svg_points2path.py, and styles.json) is released under the MIT license.
The 'map.osm' is covered under the ODbL license.  


KNOWN BUGS and things to work on:
 
The out.svg created by osm2svg_v4.py contains several <polyline> line segments that Clender objects to (usually reported as a 'Color Error'.  To resolve that, I have a second-stage program, svg_points2path, that will clear the issue.   It converts the <polyline> to <path> structures in the 'out.svg'.   The output from that step is two files called 'paths_out.svg' and 'smoothed_out.svg'.  It also performs auto-smooth curves (Bezier curves) of the node points and outputs that into 'smoothed_out.svg'.  Hopefully, this will improve the 'Clender' processing.  I've found sandtraps to be a tricky feature with 'Clender' as 'Clender' performs a two-step inset on the sandtrap shapes, and if the sandtraps are too narrow or not rounded enough, that process will fail.   I'm still looking for a solution (coding rules) to create those properly and maintain their shape.

Side Motivation:  One aspect of the auto-segmented golf courses is the need for labeled courses to provide training data for an AI like the SAM2 system or others computer vision tools.  This tool should help provide that training data.   


