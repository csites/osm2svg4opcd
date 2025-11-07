# osm2svg4opcd
Openstreetmap to SVG formated for the Open Platform Course Design (Quick GSPro/Inkscape Assist)

OSM2SVG4OPCD is a tool to help map out golf courses quickly with the goal of having a file ready to pass through the 'Clender' step in the OPCD process of building the Unity Course. The identification of features and and the assignment of colors and paths and styles is controlled by the styles.json file.  The idea is to goto https://openstreetmap.org and search for the golf course you would like.  You would give it the longitude and lattitudes of the image to map that would match the Inner Lidar/DEM images used for the terrain.  You would simply run this program osm2svg_v4.py and it would create the SVG image with all of the features outlined and mapped into a OPCD 'Clender' passible .SVG file.

Openstreetmap is a fantastic community driven mapping service and it has many uses. Volenteers use the openstreetmap editor to identify features from arial imagry of land and tag them. The resulting maps are distributed under 'Open Data Commons Open Database' License (ODbL). https://opendatacommons.org/licenses/odbl/1-0.   Features identified are tagged and labled. For exmple, Srteetname might be one tag. 

Note this is a work in progress and I'm in the processes of developing functions to locate Strokes and paths that may overlap. Second; roads, cartpaths and trails need to have any sharp corners converted to curves (fellet) similar to how inkscape processes them.


License; My code (osm2svg_v4.py, svg_points2path.py and styles.json) are released under the MIT license.
The map.osm is covered under the ODbL license.  


KNOWN BUGS and things to work on:

The OPCD Clender has troubles with small sandtraps or sharp pinch areas when building and inset mesh.  I have yet to find a work around other than manual trial and error processing with clender.
The out.svg created by the osm2svg_v4.py contains several <polyline> line segments that Clender objects to (Usually showing as a 'Color Error'.  We have a second stage program svg_points2path to clear that up.   It converts the <polyline> to <path> structures.   The output from that is a file called 'paths_out.svg'.  It also performs a auto-smooth curving (bezier curves) of the node points and outputs that into 'smoothed_out.svg'.  Hopefully this will improve the 'clender' processing.  

I've created a second program to process the out.svg is the 'svg_points2path.py' program.

