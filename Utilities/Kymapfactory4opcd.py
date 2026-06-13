#!/usr/bin/env python3
"""
Kymapfactory4opcd.py.  This is a Ky (USA) secific utility program that uses a library called abovepy from KyFromAbove.   It's uses a KY specific ArcGIS REST interface for fetching DEM (Digital Elevation Maps) , and Ortho arial imagary,  is used to aligned inner and outer satellite images where the inner images can be used as background images for osm2svg4opcd. (osm2svg_v9.py).  It also extracts from your collection of DEM / LAZ tile, the height map, and hillshade image.  It also builds from the height map an inner_terrain.obj and outer_terrain.obj.   It organizes the output and stores these into your output projects folder.

EXAMPLE: ./Kymapfactory4opcd.py -lat 38.17345 -lon -85.56277 --ky_dem --ky_ortho --google_sat --bing_sat --download_osm -o ~/Projects/Charlie_Vettner --build_inner_obj --build_outer_obj 

./Kymapfactory4opcd.py -lat 38.1732061 -lon -85.5626482 --ky_dem --ky_ortho --google_sat --bing_sat --download_osm -o ~/Projects/Seneca_Golf_Club

./Kymapfactory4opcd.py -lat 38.1732061 -lon -85.5626482 --ky_dem --ky_ortho --google_sat --bing_sat --download_osm -o ~/Projects/Charlie_Vettner

./Kymapfactory4opcd.py -lat 38.260466 -lon -85.678923 --output_folder ~/Projects/Crescent_Hill --auto_center --ky_dem --ky_ortho --google_sat --bing_sat --download_osm

Kymapfactory4opcd.py -lat "38.2155513" -lon "-85.3602120" --output_folder ~/Projects/UofL_Golf_Club --download_osm 

"""
import os
import sys
import math
import time
import json
import glob
import argparse
import requests
import subprocess
import shutil
import textwrap
import mercantile
import numpy as np
import rasterio
import pyproj
import xml.etree.ElementTree as ET
import abovepy # For KY only
import subprocess # To run gdal commands

from PIL import Image
from rasterio.merge import merge
from rasterio.mask import mask
from shapely.geometry import box
from shapely.ops import transform
from pystac_client import Client
from scipy.ndimage import gaussian_filter
from osgeo import gdal

# Turn of futurewarning
gdal.UseExceptions()

# Base URL for the Overpass API
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
STYLES_FILE = "styles.json"
KY_PHASE2_Z_SCALE = 0.6096 # 2ft elevation per pixel
KY_Z_SCALE = 0.3048 # Feet to Meters

# ANSI Escape Codes for Emacs/Terminal
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"

# =================================================================================
# --- CLASS A1. CoordinateManager                                               ---
# ---    CoordinateManager.__init__(self, lat, lon, inner_m=2000, outer_m=4000) ---
# ---    CoordinateManager.get_bbox(self, size_m)                               ---
# ---    CoordinateManager.get_gdal_bounds(self, size_m)                        ---
# ---    CoordinateManager.calculate_unity_params(self, inner_min, outer_min)   ---
# ---    CoordinateManager.recenter_from_osm(self, osm_file, project_path)      --- 
# ---------------------------------------------------------------------------------
class CoordinateManager:
    def __init__(self, lat, lon, inner_m=2000, outer_m=4000):
        self.lat = lat
        self.lon = lon
        self.inner_m = inner_m
        self.outer_m = outer_m

        # High-precision constants
        self.METERS_PER_DEGREE_LAT = 111320.0
        self.M2FT = 3.2808333333465  # US Survey Foot
        self.FT2M = 1.0 / self.M2FT

    def get_bbox(self, size_m):
        """Calculates a square bounding box centered on the golf course."""
        # Latitude offset is constant
        lat_off = (size_m / 2.0) / self.METERS_PER_DEGREE_LAT

        # Longitude offset varies by Latitude (Cos factor)
        lon_dist = self.METERS_PER_DEGREE_LAT * math.cos(math.radians(self.lat))
        lon_off = (size_m / 2.0) / lon_dist

        bbox = {
            'min_lat': self.lat - lat_off,
            'max_lat': self.lat + lat_off,
            'min_lon': self.lon - lon_off,
            'max_lon': self.lon + lon_off
        }

        # We store the North-East corner as our "Master Anchor"
        # This is where Clinder/Blender will consider 0,0,0
        self.anchor_lat = bbox['max_lat']
        self.anchor_lon = bbox['max_lon']

        return (bbox['min_lat'], bbox['min_lon'], bbox['max_lat'], bbox['max_lon'])

    def get_gdal_bounds(self, size_m):
        """Returns bounds in (min_lon, min_lat, max_lon, max_lat) for GDAL."""
        b = self.get_bbox(size_m)
        # Reorders for gdal (min_lat, min_lon, max_lat, max_lon) -> (min_lon, min_lat, max_lon, max_lat)
        return (b[1], b[0], b[3], b[2])

    def calculate_unity_params(self, inner_min, outer_min):
        """Calculates the vertical offset between inner/outer grids."""
        return (outer_min * self.FT2M) - (inner_min * self.FT2M)

    def recenter_from_osm(self, osm_file, project_path):
        """Parses OSM for golf_course boundary and updates self.lat/lon"""
        if not os.path.exists(osm_file):
            print(f"{YELLOW}[WARNING]{RESET}: {osm_file} not found for centering.")
            return False

        tree = ET.parse(osm_file)
        root = tree.getroot()
        nodes = {n.get('id'): (float(n.get('lat')), float(n.get('lon'))) for n in root.findall('node')}
        
        coords = []
        for way in root.findall('way'):
            if any(t.get('k') == 'leisure' and t.get('v') == 'golf_course' for t in way.findall('tag')):
                for nd in way.findall('nd'):
                    node_id = nd.get('ref')
                    if node_id in nodes:
                        coords.append(nodes[node_id])

        if not coords:
            print("{YELLOW}[WARNING]{RESET}  No 'leisure=golf_course' found in OSM. Keeping original center.")
            return False

        # Calculate Midpoint
        lats, lons = [c[0] for c in coords], [c[1] for c in coords]
        new_lat = (min(lats) + max(lats)) / 2
        new_lon = (min(lons) + max(lons)) / 2

        # Update self
        self.lat = new_lat
        self.lon = new_lon

        # Save to etc/
        etc_dir = os.path.join(project_path, "etc")
        os.makedirs(etc_dir, exist_ok=True)
        with open(os.path.join(etc_dir, "course_center.txt"), "w") as f:
            f.write(f"{self.lat:.7f}, {self.lon:.7f}\n")
            
        return True
    
    def load_from_manifest(self, file_path):
        try:
            with open(file_path, 'r') as f:
                content = f.read()
                # Look for the "Midpoint Center:" line you mentioned
                for line in content.splitlines():
                    if "Midpoint Center:" in line:
                        coords_str = line.split("Midpoint Center:")[1].strip()
                        lat_str, lon_str = coords_str.split(",")
                        self.lat = float(lat_str.strip())
                        self.lon = float(lon_str.strip())
                        return True
        except Exception as e:
            return False
        return False
    
# =============================================================================================================
# --- CLASS A2. ImageryService                                                                              ---
# ---    ImageryService.__init__(self, provider="Google")                                                   ---
# ---    ImageryService._to_bing_quadkey(self, x, y, z)                                                     ---
# ---    ImageryService.fetch_and_stitch(self, bbox, zoom=19, output_path="satellite.png")                  ---
# ---    ImageryService.get_safe_zoom(self, bbox, target_tiles=500)                                         ---
# ---    ImageryService.write_world_file(self, image_path, bbox, width, height)                             ---
# ---    ImageryService.asseble_local_orthos(self, tile_dir, output_folder, inner_te, outer_te)             ---
# ---    ImageryService.save_georeferenced_image(self, input_array, output_path, geo_transform, projection) ---
# -------------------------------------------------------------------------------------------------------------
class ImageryService:
    def __init__(self, provider="Google"):
        self.providers = {
            "Google": "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
            "Bing": "https://ecn.t3.tiles.virtualearth.net/tiles/a{q}.jpeg?g=1"
        }
        self.provider = provider

    def _to_bing_quadkey(self, x, y, z):
        quadkey = ""
        for i in range(z, 0, -1):
            digit = 0
            mask = 1 << (i - 1)
            if (x & mask) != 0: digit += 1
            if (y & mask) != 0: digit += 2
            quadkey += str(digit)
        return quadkey

    def fetch_and_stitch(self, bbox, zoom, output_path, force=False):
        """
        Fetches satellite tiles, stitches them, and georeferences using a 
        memory-backed dataset to avoid PNG 'Update' errors.
        """
        # 1. Setup local project-specific cache in /Imagery
        imagery_dir = os.path.dirname(output_path)
        local_cache_dir = os.path.join(imagery_dir, f"tiles_{self.provider.lower()}")
        os.makedirs(local_cache_dir, exist_ok=True)
        
        if os.path.exists(local_cache_dir) and any(os.scandir(local_cache_dir)) and not force:
            print(f"    └── {GREEN}[INFO]{RESET} Found previous {self.provider} tiles in project Imagery. Using prior results. Skipping network query.")
        else:
            if force:
                print(f"    └── {YELLOW}[FORCE]{RESET} Re-downloading {self.provider} imagery as requested...")
                
        # 2. Tile Calculation
        min_lat, min_lon, max_lat, max_lon = bbox[0], bbox[1], bbox[2], bbox[3]

        def deg2tile(lat_deg, lon_deg, zoom_level):
            lat_rad = math.radians(lat_deg)
            n = 2.0 ** zoom_level
            xtile = int((lon_deg + 180.0) / 360.0 * n)
            ytile = int((1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
            return xtile, ytile

        x_start, y_start = deg2tile(max_lat, min_lon, zoom)
        x_end, y_end = deg2tile(min_lat, max_lon, zoom)
        x_min, x_max = min(x_start, x_end), max(x_start, x_end)
        y_min, y_max = min(y_start, y_end), max(y_start, y_end)
        
        total_tiles = (x_max - x_min + 1) * (y_max - y_min + 1)
        print(f"    └── Provider '{self.provider}' mapping grid: Zoom {zoom}, processing {total_tiles} tiles...")

        # 3. Download Loop
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
        for tile_y in range(y_min, y_max + 1):
            for tile_x in range(x_min, x_max + 1):
                tile_filename = f"{self.provider.lower()}_{zoom}_{tile_x}_{tile_y}.jpg"
                tile_filepath = os.path.join(local_cache_dir, tile_filename)
                
                if not os.path.exists(tile_filepath):
                    if self.provider.lower() == "google":
                        url = f"https://mt1.google.com/vt/lyrs=s&x={tile_x}&y={tile_y}&z={zoom}"
                    else:
                        url = f"https://ecn.t0.tiles.virtualearth.net/tiles/a{self._to_bing_quadkey(tile_x, tile_y, zoom)}.jpeg?g=587"
                    
                    try:
                        resp = requests.get(url, headers=headers, timeout=10)
                        if resp.status_code == 200:
                            with open(tile_filepath, "wb") as f: f.write(resp.content)
                    except Exception as e:
                        print(f"    └── [WARN] Failed tile {tile_x},{tile_y}: {e}")

        # 4. Stitching to Memory (Avoids PNG Update Error)
        tile_w, tile_h = 256, 256
        grid_w, grid_h = (x_max - x_min + 1) * tile_w, (y_max - y_min + 1) * tile_h
        
        print(f"    └── Stitching canvas ({grid_w}x{grid_h} px)...")
        canvas = Image.new("RGB", (grid_w, grid_h))
        
        for tile_y in range(y_min, y_max + 1):
            for tile_x in range(x_min, x_max + 1):
                tile_filepath = os.path.join(local_cache_dir, f"{self.provider.lower()}_{zoom}_{tile_x}_{tile_y}.jpg")
                if os.path.exists(tile_filepath):
                    with Image.open(tile_filepath) as img:
                        canvas.paste(img, ((tile_x - x_min) * tile_w, (tile_y - y_min) * tile_h))

        # 5. Georeferencing via VRT (The GDAL 4.0 compatible way)
        temp_raw = os.path.join(local_cache_dir, "raw_stitch.tif")
        canvas.save(temp_raw) # Save as Tiff temporarily (Tiff supports metadata)
        canvas.close()

        def tile2deg(xtile, ytile, zoom_level):
            n = 2.0 ** zoom_level
            lon = xtile / n * 360.0 - 180.0
            lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * ytile / n))))
            return lat, lon

        def latlon_to_meters(lat, lon):
            x = lon * 20037508.34 / 180.0
            y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0) * 20037508.34 / 180.0
            return x, y

        g_top, g_left = tile2deg(x_min, y_min, zoom)
        g_bottom, g_right = tile2deg(x_max + 1, y_max + 1, zoom)
        xmin, ymax = latlon_to_meters(g_top, g_left)
        xmax, ymin = latlon_to_meters(g_bottom, g_right)

        # Open the Tiff and set metadata
        ds = gdal.Open(temp_raw, gdal.GA_Update)
        ds.SetGeoTransform([xmin, (xmax-xmin)/grid_w, 0, ymax, 0, (ymin-ymax)/grid_h])
        ds.SetProjection('EPSG:3857')
        ds = None # Flush

        # 6. Warp to final PNG (clips to exact bbox)
        temp_clipped = os.path.join(local_cache_dir, "clipped_temp.tif")
        b_minlat, b_minlon, b_maxlat, b_maxlon = bbox
        m_left, m_bottom = latlon_to_meters(b_minlat, b_minlon)
        m_right, m_top = latlon_to_meters(b_maxlat, b_maxlon)
        gdal.Warp(temp_clipped, temp_raw, options=gdal.WarpOptions(
            format="GTiff",  
            srcSRS="EPSG:3857",  
            dstSRS="EPSG:3857",
            outputBounds=[m_left, m_bottom, m_right, m_top], # Standardized order
            resampleAlg=gdal.GRA_Bilinear
        ))
        gdal.Translate(output_path, temp_clipped, options=gdal.TranslateOptions(
            format="PNG",
            creationOptions=["ZLEVEL=9"]
        ))
        for f in [temp_raw, temp_clipped]:
            if os.path.exists(f):
                os.remove(f)
                

    def assemble_local_orthos(self, tile_dir, output_folder, inner_te, outer_te):
        """Standardized stitcher for ortho tiles using a two-step GeoTIFF process."""
        if not os.path.isdir(tile_dir):
            print(f"❌ ERROR: Tile directory not found: {tile_dir}")
            return

        # 1. Get the file list
        tile_paths = glob.glob(os.path.join(tile_dir, "**", "*.tif"), recursive=True)
        tile_paths += glob.glob(os.path.join(tile_dir, "**", "*.jp2"), recursive=True)

        if not tile_paths:
            print(f"❌ ERROR: No .tif or .jp2 files found in {tile_dir}")
            return

        def ensure_list(te):
            if isinstance(te, str):
                return [float(x) for x in te.replace(',', ' ').split()]
            return te

        vrt_path = os.path.join(output_folder, "orthos_mosaic.vrt")
        
        try:
            print(f"--> Building VRT from {len(tile_paths)} tiles...")
            gdal.BuildVRT(vrt_path, tile_paths)

            for name, te_input in [("inner", inner_te), ("outer", outer_te)]:
                te_list = ensure_list(te_input)
                temp_tif = os.path.join(output_folder, f"{name}_temp.tif")
                final_png = os.path.join(output_folder, f"{name}_ky_ortho.png")
                if os.path.exists(final_png):
                    print(f"    └── {GREEN}[SKIP]{RESET} {name}_ky_ortho.png already exists. Skipping warp/stitch.")
                    continue
                
                # --- RESOLUTION LOGIC ---
                # 3-inch (0.0762) for inner, 12-inch (0.3048) for outer
                res = 0.0762 if name == "inner" else 0.3048

                print(f"--> [INFO] Warping {name} ortho at {res}m resolution...")

                warp_opts = gdal.WarpOptions(
                    format='GTiff',
                    outputBounds=te_list,
                    outputBoundsSRS="EPSG:4326",
                    dstSRS="EPSG:3089",
                    xRes=res,
                    yRes=res,
                    resampleAlg=gdal.GRIORA_Cubic,
                    # BIGTIFF=YES is the critical fix for the 4GB error
                    creationOptions=[
                        'BIGTIFF=YES', 
                        'COMPRESS=DEFLATE', 
                        'PREDICTOR=2', 
                        'ZLEVEL=6'
                    ],
                    multithread=True
                )

                gdal.Warp(temp_tif, vrt_path, options=warp_opts)
            
                # Step 2: Translate to PNG with High Compression
                if os.path.exists(temp_tif):
                    print(f"--> [INFO] Converting {name} TIFF to PNG (Max Compression)...")
                    
                    # --- ADDED COMPRESSION OPTIONS ---
                    translate_opts = gdal.TranslateOptions(
                        format="PNG",
                        creationOptions=["ZLEVEL=9"],
                        outputType=gdal.GDT_Byte,
                        width=4096, height=4096
                    )
                    
                    gdal.Translate(final_png, temp_tif, options=translate_opts)
                    os.remove(temp_tif) 
                else:
                    print(f"--> [ERROR] Failed to create intermediate TIFF for {name}")
                
        finally:
            print(f"Finished stitching orthos. Cleaning up VRT.")
            if os.path.exists(vrt_path):
                os.remove(vrt_path)
                
    def save_georeferenced_image(self, input_array, output_path, geo_transform, projection):
        # Implementation to handle array-to-PNG with georeferencing
        temp_tif = output_path.replace(".png", ".tif")
        driver = gdal.GetDriverByName('GTiff')
        h, w = input_array.shape[:2]
        ds = driver.Create(temp_tif, w, h, 3 if len(input_array.shape)==3 else 1, gdal.GDT_Byte)
        ds.SetGeoTransform(geo_transform)
        ds.SetProjection(projection)
        for i in range(ds.RasterCount):
            ds.GetRasterBand(i+1).WriteArray(input_array[:,:,i] if ds.RasterCount > 1 else input_array)
        ds = None
        gdal.Translate(output_path, temp_tif, format='PNG')
        os.remove(temp_tif)
        
# ====================================================================================================
# --- CLASS A3. TerrainService                                                                     ---
# ---    TerrainService.__init__(self, dem_folder)                                                 ---
# ---    TerrainService.process_dem(self, tile_dir, bbox, label="inner", res=0.6096)               ---
# ---    TerrainService.process_ortho(self, tile_dir, bbox, label="inner", res=0.1)                ---
# ---    TerrainService.make_hillshade(self, input_tif, output_path, z=3)                          ---
# ---    TerrainService.make_slope_map(self, input_tif, output_path)                               ---
# ---    TerrainService.make_heightmap(make_heightmap(self, input_tif, output_path, min_z, max_z)  ---
# ---    TerrainService.export_obj_from_tif(self, input_tif, output_path, scale_z=0.3048)          ---
# ----------------------------------------------------------------------------------------------------
class TerrainService:
    def __init__(self, dem_folder):
        self.dem_folder = os.path.expanduser(dem_folder)
        os.makedirs(self.dem_folder, exist_ok=True)
        self.output_dir = self.dem_folder
        
    def process_dem(self, tile_dir, output_path, bbox, label="inner", res=0.6096):
        """Stitches and crops LiDAR tiles using the native Warp API."""
        output_tif = os.path.join(self.output_dir, f"{label}.tif")
        tile_pattern = os.path.join(tile_dir, "*.tif")
        
        warp_options = gdal.WarpOptions(
            format="GTiff",
            dstSRS="EPSG:3857", # Web Mercator for Unity
            outputBounds=(bbox[1], bbox[0], bbox[3], bbox[2]), # xmin, ymin, xmax, ymax
            outputBoundsSRS="EPSG:4326",
            xRes=res, yRes=res,
            resampleAlg="bilinear"
        )
        tile_files = glob.glob(tile_pattern)
        if not tile_files:
            print(f"--> [ERROR] No files matched the pattern: {tile_pattern}")
            return
        gdal.Warp(output_tif, tile_files, options=warp_options)
        if os.path.exists(output_tif):
            print(f"--> [SUCCESS] Created: {output_tif}")
        return output_tif

    def process_ortho(self, tile_dir, output_path, bbox, label="inner", res=0.1):
        """Stitches tiles to a temp GeoTIFF, then converts to PNG for Inkscape."""
        # Path definitions
        temp_tif = os.path.join(self.output_dir, f"{label}_temp.tif")
        final_png = os.path.join(self.output_dir, f"{label}_ky_ortho.png")
        
        # 1. Collect tiles
        tile_files = glob.glob(os.path.join(tile_dir, "**", "*.tif"), recursive=True)
        tile_files += glob.glob(os.path.join(tile_dir, "**", "*.jp2"), recursive=True)

        if not tile_files:
            print(f"--> [ERROR] No tiles found in {tile_dir}")
            return None

        # 2. STEP ONE: Warp to GeoTIFF (This handles the math and coordinates)
        warp_options = gdal.WarpOptions(
            format="GTiff",
            outputSRS="EPSG:3089", 
            outputBounds=(bbox[1], bbox[0], bbox[3], bbox[2]),
            outputBoundsSRS="EPSG:4326",
            xRes=res, yRes=res,
            resampleAlg="bilinear"
        )
        
        print(f"--> [INFO] Warping to intermediate GeoTIFF...")
        gdal.Warp(temp_tif, tile_files, options=warp_options)

        # 3. STEP TWO: Translate GeoTIFF to PNG (This creates the visual background)
        if os.path.exists(temp_tif):
            print(f"--> [INFO] Converting GeoTIFF to PNG...")
            translate_options = gdal.TranslateOptions(
                format='PNG',
                rgbExpand='rgb', # Ensures 1-band paletted images become 3-band RGB
                stats=True,      # Calculates the range of the image first
                scaleParams=[[]] # Leaving this empty tells GDAL to auto-scale based on stats
            )
            gdal.Translate(final_png, temp_tif, options=translate_options)
            
            # Cleanup the heavy intermediate TIFF
            os.remove(temp_tif)
            print(f"--> [SUCCESS] Created: {final_png}")
            return final_png
        else:
            print(f"--> [ERROR] Intermediate TIFF was not created. Check coordinates.")
            return None
        

    def make_hillshade(self, input_tif, output_path, z=3):
        gdal.DEMProcessing(output_path, input_tif, "hillshade", 
                          format="PNG", zFactor=z, azimuth=315, altitude=45)

    def make_slope_map(self, input_tif, output_path):
        gdal.DEMProcessing(output_path, input_tif, "slope", format="GTiff")
        
    def make_heightmap(self, input_tif, output_path, min_z, max_z):
        translate_options = gdal.TranslateOptions(
            format="PNG",
            outputType=gdal.GDT_UInt16,
            scaleParams=[[min_z, max_z, 0, 65535]],
            noData=0
        )
        gdal.Translate(output_path, input_tif, options=translate_options)

    def export_obj_from_tif(self, input_tif, output_path, scale_z=0.3048):
        """
        Reads the TIF directly and exports an OBJ. 
        No need for manual array passing!
        """
        ds = gdal.Open(input_tif)
        band = ds.GetRasterBand(1)
        elevation_array = band.ReadAsArray()
        
        rows, cols = elevation_array.shape
        gt = ds.GetGeoTransform()
        
        with open(output_path, "w") as f:
            f.write("# OPCD Map Factory - GDAL Edition\n")
            f.write(f"# Source Res: {gt[1]:.2f}m x {abs(gt[5]):.2f}m\n")
            for r in range(rows):
                for c in range(cols):
                    # Use the GeoTransform to get local meter coordinates
                    v_x = c * gt[1]
                    # v_y = -(r * abs(gt[5])) # Flip Y for Blender/Unity
                    v_y = r * abs(gt[5])
                    v_z = elevation_array[r, c] * scale_z
                    f.write(f"v {v_x:.4f} {v_z:.4f} {v_y:.4f}\n")

            for r in range(rows - 1):
                for c in range(cols - 1):
                    v1 = r * cols + c + 1
                    v2 = r * cols + (c + 1) + 1
                    v3 = (r + 1) * cols + (c + 1) + 1
                    v4 = (r + 1) * cols + c + 1
                    f.write(f"f {v1} {v2} {v3} {v4}\n")
        print(f"{GREEN}[OK]{RESET}: OBJ Exported from {input_tif}")
        
        
# ======================================================================================
# --- CLASS A4. DataHarvester                                                        ---
# ---    DataHarvester.__init__(self, dem_dir, imagery_dir)                          ---
# ---    DataHarvester.fetch_ky_assets(self, bbox)                                   ---
# --------------------------------------------------------------------------------------
class DataHarvester:
    def __init__(self, dem_dir, imagery_dir):
        self.dem_dir = os.path.expanduser(dem_dir)
        self.imagery_dir = os.path.expanduser(imagery_dir)
        os.makedirs(self.dem_dir, exist_ok=True)
        os.makedirs(self.imagery_dir, exist_ok=True)

    def fetch_ky_assets(self, bbox):
        """
        Queries KyFromAbove via abovepy to harvest raw DEM and Ortho tiles.
        Does not hit the legacy kyraster export endpoints.
        """
        # West, South, East, North layout mapping for abovepy
        search_bbox = (bbox[1], bbox[0], bbox[3], bbox[2])

        dem_paths = []
        img_paths = []

        print(f"{GREEN}[NOTICE]{RESET} Searching KyFromAbove for raw grid assets...")

        try:
            # 1. RAW LiDAR DEM SELECTION
            print(f"{GREEN}[NOTICE]{RESET} Checking dem-phase3...")
            dem_tiles = abovepy.search(bbox=search_bbox, product="dem_phase3")

            if dem_tiles.empty:
                print(f"{YELLOW}[NOTICE]{RESET} -> Phase 3 DEM not available here. Falling back to dem-phase2...")
                dem_tiles = abovepy.search(bbox=search_bbox, product="dem_phase2")

            if not dem_tiles.empty:
                print(f"! -> Downloading {len(dem_tiles)} raw LiDAR DEM tiles...")
                dem_paths = abovepy.download(dem_tiles, output_dir=self.dem_dir)
            else:
                print(f"{RED}[WARNING]{RESET} No DEM tiles found in Phase 3 or Phase 2 grids.")
                
            # 2. RAW ORTHOPHOTOGRAPHY SELECTION
            print(f"{GREEN}[NOTICE]{RESET} Checking ortho_phase3...")
            img_tiles = abovepy.search(bbox=search_bbox, product="ortho_phase3")

            if img_tiles.empty:
                print(f"{YELLOW}[WARNING]{RESET} -> Phase 3 Ortho not available here. Falling back to ortho_phase2...")
                img_tiles = abovepy.search(bbox=search_bbox, product="ortho_phase2")

            if not img_tiles.empty:
                print(f"{CYAN}[INFO]{RESET} -> Downloading {len(img_tiles)} raw Orthophoto tiles...")
                # Note: We send these straight to your raw Tiles repository directory
                # so that ImageryService can grab them via glob.glob()
                raw_tiles_dir = os.path.dirname(self.imagery_dir)
                target_dir = os.path.join(raw_tiles_dir, "QGIS/Tiles")
                os.makedirs(target_dir, exist_ok=True)
                
                img_paths = abovepy.download(img_tiles, output_dir=target_dir)
            else:
                print(f"{RED}[ERROR]{RESET} No raw Ortho tiles found in either Phase grid layout.")

        except Exception as e:
            print(f"{RED}[ERROR]{RESET} Data Harvester failed during abovepy operation: {e}")

        return dem_paths, img_paths
    
# ====================================================================================
# --- CLASS A5. SVGWaterRefiner                                                    ---
# ---    SVGWaterRefiner.__init__(elf, svg_file, dem_file, center_lat, center_lon  ---
# ---    SVGWaterRefiner.gravity_snap(self, lat, lon, search_radius_px=3           ---
# ---    SVGWaterRefiner.refine_all_water_svg(self, factory_coords)                ---
# ------------------------------------------------------------------------------------
class SVGWaterRefiner:
    def __init__(self, svg_file, dem_file, center_lat, center_lon):
        self.svg_file = svg_file
        self.dem_file = dem_file
        self.center_lat = center_lat
        self.center_lon = center_lon
        
        # Load DEM metadata AND the actual data array for fast sampling
        with rasterio.open(self.dem_file) as src:
            self.dem_data = src.read(1)
            self.dem_transform = src.transform
            self.dem_crs = src.crs
            self.res = src.res[0]
            self.bounds = src.bounds
            
        # Transformers - standardized naming
        self.to_dem_crs = Transformer.from_crs("epsg:4326", self.dem_crs, always_xy=True)
        self.to_latlon = Transformer.from_crs(self.dem_crs, "epsg:4326", always_xy=True)

    def gravity_snap(self, lat, lon, search_radius_px=3):
        """Finds the lowest elevation in a NxN grid around the point"""
        # Convert Lat/Lon to DEM pixel coordinates
        x_crs, y_crs = self.to_dem_crs.transform(lon, lat)
        # Use inverse transform to get row/col
        row, col = ~self.dem_transform * (x_crs, y_crs)
        row, col = int(row), int(col)

        # Define search window boundaries
        r_start = max(0, row - search_radius_px)
        r_end = min(self.dem_data.shape[0], row + search_radius_px + 1)
        c_start = max(0, col - search_radius_px)
        c_end = min(self.dem_data.shape[1], col + search_radius_px + 1)

        window = self.dem_data[r_start:r_end, c_start:c_end]
        
        # Check if window is empty to avoid argmin crash
        if window.size == 0:
            return lon, lat

        # Find local minimum index in that window
        min_idx = np.unravel_index(np.argmin(window), window.shape)
        
        # Convert back to global row/col
        best_row = r_start + min_idx[0]
        best_col = c_start + min_idx[1]
        
        # Convert back to Lat/Lon
        best_x_crs, best_y_crs = self.dem_transform * (best_col, best_row)
        return self.to_latlon.transform(best_x_crs, best_y_crs)

    def refine_all_water_svg(self, factory_coords):
        """Iterates through SVG, snaps all water bodies, and overwrites file"""
        import re # Ensure re is available
        target_keywords = ['water', 'pond', 'lake', 'stream', 'creek', 'waterway', 'hazard']
        tree = ET.parse(self.svg_file)
        root = tree.getroot()
        paths_found = 0

        # Namespace handling for SVGs
        ns = {'svg': 'http://www.w3.org/2000/svg'}
        
        for path in root.findall(".//{http://www.w3.org/2000/svg}path"):
            path_id = (path.get('id') or "").lower()
            path_class = (path.get('class') or "").lower()

            if any(k in path_id or k in path_class for k in target_keywords):
                d_string = path.get('d')
                if not d_string: continue

                # Regex to pull out the coordinate pairs
                points = re.findall(r'([-+]?\d*\.\d+|[-+]?\d+),([-+]?\d*\.\d+|[-+]?\d+)', d_string)
                new_coords = []
                
                for x_svg, y_svg in points:
                    # 1. SVG Pixels -> Lat/Lon
                    lat, lon = factory_coords.pixel_to_latlon(float(x_svg), float(y_svg))
                    # 2. Gravity Snap! (returns lon, lat)
                    new_lon, new_lat = self.gravity_snap(lat, lon)
                    # 3. Lat/Lon -> SVG Pixels
                    new_x, new_y = factory_coords.latlon_to_pixel(new_lat, new_lon)
                    new_coords.append(f"{new_x:.3f},{new_y:.3f}")

                if new_coords:
                    # Rebuild the path data. Using 'L' for simplicity.
                    path.set('d', f"M { ' L '.join(new_coords) } Z")
                    paths_found += 1

        tree.write(self.svg_file)
        print(f"--> {GREEN}A5:{RESET} Water bodies refined ({paths_found} paths processed)")


# ==================================================================================================================
# --- CLASS A6. ProjectFolderService                                                                             ---
# ---     ProjectFolderService.__init__(self, output_folder, course_name)                                        ---
# ---     ProjectFolderService.initialize_workspace(self)                                                        ---
# ---     ProjectFolderService.get_path(self, key)                                                               ---
# ---     ProjectFolderService.resolve_leaf_tile_directory(self, asset_type, phase_num)                          ---
# ---     ProjectFolderService.write_metadata_file(self, filename, content)                                      ---
# ---     ProjectFolderService.deploy_unity_template(self, template_base_path)                                   ---
# ---     ProjectFolderService.generate_project_report(self, lat, lon, inner_m, outer_m, inner_bbox, outer_bbox) ---
# ------------------------------------------------------------------------------------------------------------------
class ProjectFolderService:
    """
    Handles initialization, verification, cross-platform path resolution,
    and asset routing for the OPCD course workspace.
    """
    def __init__(self, output_folder, course_name):
        self.course_name = course_name
        # Resolve absolute, system-safe path (resolves ~ on Linux and mappings on Windows)
        self.root_dir = os.path.abspath(os.path.expanduser(output_folder))
        self.course_name = course_name if course_name else os.path.basename(self.root_dir)
        
        # Define the structural schema mapping exactly to layout.txt specifications
        self.paths = {
            "root":       self.root_dir,
            "cache":      os.path.join(self.root_dir, ".cache"),
            "blender":    os.path.join(self.root_dir, "Blender"),
            "imagery":    os.path.join(self.root_dir, "Imagery"),
            "inkscape":   os.path.join(self.root_dir, "Inkscape"),
            "osm":        os.path.join(self.root_dir, "OSM"),
            "etc":        os.path.join(self.root_dir, "etc"),
            "qgis":       os.path.join(self.root_dir, "QGIS"),
            "tiles":      os.path.join(self.root_dir, "QGIS", "Tiles"),
            "heightmap":  os.path.join(self.root_dir, "QGIS", "Heightmap"),
            "overlays":   os.path.join(self.root_dir, "QGIS", "Overlays"),
            "shapefiles": os.path.join(self.root_dir, "QGIS", "Shapefiles"),
            "tif":        os.path.join(self.root_dir, "QGIS", "TIF"),
            "unity_root": os.path.join(self.root_dir, "Unity"),
            "unity":      os.path.join(self.root_dir, "Unity", self.course_name),
        }

    def initialize_workspace(self):
        """Creates the necessary subdirectory footprint safely if not present."""
        for name, path in self.paths.items():
            os.makedirs(path, exist_ok=True)

        print(f"--> Project Workspace Configured: {self.root_dir}")
        print(f"    └── Identified Course Identity Base: '{self.course_name}'")
        
        # Generate the standard hierarchy
        for folder_name, folder_path in self.paths.items():
            os.makedirs(folder_path, exist_ok=True)
        print("    └── Workspace architectural directory layout verified [OK]")

    def get_best_phase_dir(self, asset_type):
        """
        Scans QGIS/Tiles/ for the highest phase available.
        Standardizes on 'Ortho' and 'DEM' prefixes.
        Returns the absolute path to the best folder found (Phase 3 > 2 > 1).
        """
        asset_lower = asset_type.lower()
        best_folder = None
        best_phase = -1

        # Standardized fallback names
        default_name = "dem-phase3" if "dem" in asset_lower else "Ortho_phase3"
        fallback_path = os.path.join(self.paths["tiles"], default_name)

        if not os.path.exists(self.paths["tiles"]):
            return fallback_path

        # Scan for existing folders
        for item in os.listdir(self.paths["tiles"]):
            item_path = os.path.join(self.paths["tiles"], item)
            if not os.path.isdir(item_path):
                continue

            item_lower = item.lower()
            
            # Match Logic:
            # Matches 'dem' for DEM
            # Matches 'ortho' (standard) or 'otho' (legacy/typo) for Ortho
            is_match = False
            if "dem" in asset_lower and "dem" in item_lower:
                is_match = True
            elif "ortho" in asset_lower:
                if "ortho" in item_lower or "otho" in item_lower:
                    is_match = True

            if is_match and "phase" in item_lower:
                try:
                    # Extract phase number (e.g., '2' from 'Ortho_phase2')
                    phase_num = int(''.join(filter(str.isdigit, item_lower)))
                    if phase_num > best_phase:
                        best_phase = phase_num
                        best_folder = item_path
                except ValueError:
                    continue

        return best_folder if best_folder else fallback_path
    
    
    def get_path(self, key):
        """Safely extracts a structured absolute directory path by identifier key."""
        if key not in self.paths:
            raise KeyError(f"Requested layout identifier '{key}' is not defined in project schema.")
        return self.paths.get(key)

    def resolve_leaf_tile_directory(self, asset_type, phase_num):
        """
        Dynamically handles dynamic execution targets like Ortho-phase3 or dem-phase2.
        Example: service.resolve_leaf_tile_directory('Ortho', 3)
        """
        # Formulate exact case-sensitive directory naming rules matching layout
        folder_name = f"{asset_type.capitalize()}_phase{phase_num}"
        target_path = os.path.join(self.paths["tiles"], folder_name)
        os.makedirs(target_path, exist_ok=True)
        return target_path

    def write_metadata_file(self, filename, content):
        """Utility function to easily dump metadata strings into the root folder (e.g. Title.txt)"""
        target_file = os.path.join(self.root_dir, filename)
        try:
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"    └── [WARN] Failed to write structural root metadata file '{filename}': {e}")

    def deploy_unity_template(self, template_base_path):
        """
        Safely deploys a fresh copy of the master OPCD base Unity template project 
        into the course's target Unity subfolder if it doesn't already exist.
        Excludes massive local Unity cache metadata directories to maximize build speed.
        """
        target_unity_project = self.get_path("unity")
        
        # Normalize the template source path target format cross-platform
        src_template = os.path.abspath(os.path.expanduser(template_base_path))
        
        # 1. Quick presence verification safeguard
        # If an 'Assets' directory already lives inside our target folder, do not risk overwriting work
        if os.path.exists(os.path.join(target_unity_project, "Assets")):
            print(f"    └── Unity engine environment group '{self.course_name}' already present. Skipping clone overwrite.")
            return True
            
        # 2. Check if the master baseline template directory can be found
        if not os.path.exists(src_template):
            print(f"    └── [WARN] Master Unity baseline template not found at target: {src_template}")
            print(f"    └── Skipping automated project deployment scaffolding step...")
            return False
            
        print(f"--> Deploying clean Unity project environment template for '{self.course_name}'...")
        print(f"    ├── Base Template: {src_template}")
        print(f"    └── Target Project Subfolder: {target_unity_project}")
        
        try:
            # If our baseline initialize loop created an empty placeholder leaf directory,
            # we delete it temporarily so shutil.copytree can generate the directory link tree without collision errors.
            if os.path.exists(target_unity_project):
                os.rmdir(target_unity_project) 
                
            # Crucial filtering function: prevents dragging machine-specific temporary cache folders 
            # across onto new builds, which keeps your drive uncluttered and file copies blazing fast.
            def ignore_unity_temp_data(dir_path, contents):
                ignored = []
                for item in contents:
                    if item in ["Library", "Logs", "UserSettings", "obj", ".vs", ".idea"]:
                        ignored.append(item)
                return ignored

            shutil.copytree(src_template, target_unity_project, ignore=ignore_unity_temp_data)
            print("    └── [SUCCESS] Unity course project template fully deployed to workspace layout.")
            return True
            
        except Exception as e:
            print(f"    └── [ERROR] Failed to scaffold structural course Unity environment: {e}")
            return False


    def generate_project_report(self, lat, lon, inner_m, outer_m, inner_bbox, outer_bbox):
        """
        Calculates spatial metadata, prints a console report, and saves 
        the 'source of truth' to {project}/QGIS/etc/project_coords.txt.
        """
        # GDAL -te format: <xmin> <ymin> <xmax> <ymax> 
        # (min_lon, min_lat, max_lon, max_lat)
        inner_te = f"{inner_bbox[1]:.7f} {inner_bbox[0]:.7f} {inner_bbox[3]:.7f} {inner_bbox[2]:.7f}"
        outer_te = f"{outer_bbox[1]:.7f} {outer_bbox[0]:.7f} {outer_bbox[3]:.7f} {outer_bbox[2]:.7f}"

        report = f"""
-----------------------------------------------------------------
⛳ PROJECT COORDINATES for: {self.course_name}
📍 Midpoint Center: {lat:.7f}, {lon:.7f}
-----------------------------------------------------------------
🖼  INNER BOX ({inner_m}m):
    NW Corner: {inner_bbox[2]:.7f}, {inner_bbox[1]:.7f}
    SE Corner: {inner_bbox[0]:.7f}, {inner_bbox[3]:.7f}
    GDAL -te:  {inner_te}
-----------------------------------------------------------------
🖼  OUTER BOX ({outer_m}m):
    NW Corner: {outer_bbox[2]:.7f}, {outer_bbox[1]:.7f}
    SE Corner: {outer_bbox[0]:.7f}, {outer_bbox[3]:.7f}
    GDAL -te:  {outer_te}

✅ Success: Spatial reference locked for project toolchain.
-----------------------------------------------------------------
"""
        # 1. Print to console for immediate verification
        print(report)

        # 2. Save to the etc folder as specified
        # self.paths["etc"] was defined as os.path.join(self.root_dir, "QGIS", "etc")
        report_path = os.path.join(self.paths["etc"], "Project_coords.txt")
        
        try:
            with open(report_path, "w") as f:
                f.write(report)
            print(f"--> {GREEN}[INFO]{RESET} Spatial manifest saved to: {report_path}")
        except Exception as e:
            print(f"--> {RED}[ERROR]{RESET} Failed to write coordinate report: {e}")

        return {"inner_te": inner_te, "outer_te": outer_te}
    
# ---------------------------------------------------------------------------------------------------
# --- CLASS A8. OSMSerice                                                                         ---
# ---     OSMService.__init__(self, styles_file="styles.json")                                    ---
# ---     OSMService._load_style(self)                                                            ---
# ---     OSMService._get_filters(self)                                                           ---
# ---     OSMService.build_query(self, bbox)                                                      ---
# ---     OSMService.download(self, bbox, output_path, center_lat, center_lon, inner_m, outer_m)  ---
# ---     OSMService._fetch_with_retries(self, query, max_retries=5)                              ---
# ---------------------------------------------------------------------------------------------------
class OSMService:
    def __init__(self, styles_file="styles.json"):
        self.styles_file = styles_file
        self.styles = self._load_styles(self.styles_file)
        self.OVERPASS_URL = "https://overpass-api.de/api/interpreter"

    def _load_styles(self, filename):
        """Loads the OSM feature styles from your proven logic."""
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"{RED}[ERROR]{RESET}: Configuration file '{filename}' not found.")
            sys.exit(1)
        except json.JSONDecodeError:
            print(f"{RED}[ERROR]{RESET}: Failed to decode JSON from '{filename}'.")
            sys.exit(1)

    def _get_overpass_filters(self):
        """Extracts unique tag filters (The Seneca Method)."""
        filters = []
        for style_key in self.styles.keys():
            if style_key == "Comment":
                continue
            if '.' in style_key:
                key, value = style_key.split('.', 1)
                filters.append(f'["{key}"="{value}"]')
            else:
                filters.append(f'["{style_key}"]')
        return sorted(list(set(filters)))

    def build_query(self, bbox_coords):
        """
        Constructs the query using your recursive descent command (._;>;).
        Optimized to prevent 'Double Ways' by using a single output.
        """
        # BBox format: (min_lat, min_lon, max_lat, max_lon)
        bbox_str = ",".join(map(str, bbox_coords))
        tag_filters = self._get_overpass_filters()

        query_parts = []
        query_parts.append("[out:xml][timeout:180];")
        query_parts.append("(")

        for tag_filter in tag_filters:
            query_parts.append(f"  node{tag_filter}({bbox_str});")
            query_parts.append(f"  way{tag_filter}({bbox_str});")
            query_parts.append(f"  relation{tag_filter}({bbox_str});")

        # Essential safety net for water
        query_parts.append(f"  way['natural'='water']({bbox_str});")
        query_parts.append(f"  relation['natural'='water']({bbox_str});")

        query_parts.append(");")

        # RECURSION: Get all nodes for the ways/relations found above
        query_parts.append("(._; >;);")

        # SINGLE OUTPUT: Prevents duplicates in the XML file
        query_parts.append("out body qt;")

        return "\n".join(query_parts)

    def download(self, bbox, output_path, center_lat, center_lon, inner_m, outer_m):
        """Executes query and saves XML with mandatory <bounds> and metadata <note>."""

        query = self.build_query(bbox)
        print(f"📡 Requesting targeted OSM data from Overpass...")

        data_text = self._fetch_with_retries(query)

        if data_text:
            # 1. Dynamically get Project Name and Generator
            project_name = os.path.basename(os.path.normpath(os.path.dirname(output_path)))
            generator_name = os.path.basename(sys.argv[0])

            # 2. Prepare the tags (Now with more detail)
            bounds_tag = f'  <bounds minlat="{bbox[0]}" minlon="{bbox[1]}" maxlat="{bbox[2]}" maxlon="{bbox[3]}"/>'

            # Multi-line note for better readability in the XML file
            note_content = (
                f"\n    Project: {project_name} | Generator: {generator_name}\n"
                f"    Center: {center_lat:.8f}, {center_lon:.8f}\n"
                f"    Coverage: Inner {inner_m:.1f}m / Outer {outer_m:.1f}m\n"
                f"    CRS: EPSG:4326 (WGS 84) | Lidar Scale: 0.6096 (2ft/step)"
            )
            note_tag = f'  <note>{note_content}\n  </note>'

            lines = data_text.splitlines()
            final_output = []

            # 3. Inject tags after the opening <osm> element
            for line in lines:
                # We append the original line first
                final_output.append(line)

                # Then we check if this was the <osm> tag to inject our metadata
                if "<osm" in line and not "</osm" in line:
                    final_output.append(bounds_tag)
                    final_output.append(note_tag)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(final_output))
            print(f"{GREEN}[OK]{RESET}: OSM data (with metadata) saved to: {output_path}")


    def _fetch_with_retries(self, query, max_retries=5):
        """Proven fetch logic with exponential backoff."""
        headers = {
            'User-Agent': 'Kymapfactory4opcd/1.0 (cbsite01@gmail.com)',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        for attempt in range(max_retries):
            try:
                response = requests.post(self.OVERPASS_URL, data={'data': query}, headers=headers, timeout=300)
                if response.status_code == 200:
                    return response.text
                elif response.status_code in [429, 504]:
                    delay = 2 ** attempt
                    print(f"⏳ Rate limit/Timeout ({response.status_code}). Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"{RED}[ERROR]{RESET}: HTTP Error {response.status_code}")
                    return None
            except requests.RequestException as e:
                print(f"{RED}[ERROR]{RESET}: Connection error: {e}")
                return None
        return None

# ---------------------------------------------------------------------------------
# --- Main FUNCTION M1.                                                         ---
# ---    main()                                                                 ---
# ---    Orchestrates the entire toolchain pipeline using unified services.     ---
# ---------------------------------------------------------------------------------

# Constants for Seneca / KY Elevation
KY_Z_SCALE = 0.3048  # Feet to Meters
SATELLITE_ZOOM = 18  # For Google and Bing Satallite image (Higher = more detail, larger files).


def main():
    parser = argparse.ArgumentParser(description="Kymapfactory4opcd: A Kentucky specific High-fidelity asset generator for OPCD")

    # Positional/Required
    parser.add_argument("-lat", type=float, required=True, help="Center latitude")
    parser.add_argument("-lon", type=float, required=True, help="Center longitude")
         
    # Grid Coverage Configurations (Explicit Dimensions in Meters)
    parser.add_argument("--inner_size", type=float, default=2000.0, help="Inner size (meters)")
    parser.add_argument("--outer_size", type=float, default=4000.0, help="Outer size (meters)")

    # Paths & Settings
    parser.add_argument("--style", "-s", default="styles.json", help="Styles for OSM")
    parser.add_argument("--output_folder", "-o", default="~/Projects/Course_Name", help="Project output folder")
    parser.add_argument("--auto_center", action="store_true", help="Center project on OSM boundary")

    # Raw Data Harvesting (Forces redownload)
    parser.add_argument("--ky_dem", action="store_true", help="Harvest-or-Redownload raw LiDAR from KyFromAbove")
    parser.add_argument("--ky_ortho", action="store_true", help="Harvest-or-Redownload high-res KY Ortho imagery")
    parser.add_argument("--google_sat", action="store_true", help="Harvest-n-Build inner and outer_google.png")
    parser.add_argument("--bing_sat", action="store_true", help="Harves-n-/Build inner and outer_bing.png")
    parser.add_argument("--download_osm", action="store_true", help="Harvest inner_map.osm from Overpass API")

    # If data exists require a force option
    parser.add_argument("--force_dem", action="store_true", help="Force re-download of LiDAR/DEM (Digital Elevation Maps)  tiles")
    parser.add_argument("--force_ortho", action="store_true", help="Force re-download of Ortho tiles (Overhead Arial Imagery)")
    parser.add_argument("--force_google", action="store_true", help="Force re-download of Google tiles")
    parser.add_argument("--force_bing", action="store_true", help="Force re-download of Bing tiles")
    parser.add_argument("--force_osm", action="store_true", help="Force re-download of OSM data")

    # Processing/Rebuild Flags
    parser.add_argument("--osm_to_svg", action="store_true", help="Process inner_map.osm into inner_out.svg")
    parser.add_argument("--svg_water_edge", action="store_true", help="Adjust svg water feature edges")    
    parser.add_argument("--rebuild_ortho", action="store_true", help="Re-process/stitch ortho imagery to inner and outer")
    parser.add_argument("--rebuild_obj", action="store_true", help="Re-generate 3D meshes")
    parser.add_argument("--rebuild_hillshade", action="store_true", help="Re-render hillshade PNG")
    parser.add_argument("--rebuild_heightmap", action="store_true", help="Re-calculate 16-bit heightmap")
         
    args = parser.parse_args()
         
    #===============================================================  
    # SECTION 1. Initialize Folders Tree and prepare destination ---   
    #---------------------------------------------------------------
    course_basename = os.path.basename(args.output_folder) # Get course name from output_folder
    
    folder_service = ProjectFolderService(args.output_folder, course_name=course_basename)
    folder_service.initialize_workspace()
   
    outpath = folder_service.get_path("root")
    imagery_dir = folder_service.get_path("imagery")
    unity_dir = folder_service.get_path("unity")
    qgis_dir = folder_service.get_path("qgis")
    dem_dir = folder_service.get_path("tiles")
    osm_dir = folder_service.get_path("osm")
    
    # Project Status Headers
    print(f"--> Project Workspace Location: {outpath}")
    print(f"    └── Extracted Project Core Identity: '{course_basename}'")
    
    # Nest the course-named project directory directly inside Unity/
    unity_dir = os.path.join(outpath, "Unity", course_basename)

    blender_dir = os.path.join(outpath, "Blender")
    inkscape_dir = os.path.join(outpath, "Inkscape")
    qgis_dir = os.path.join(outpath, "QGIS")
    tile_dir = os.path.join(qgis_dir, "Tiles")
    
    # Group all base directories (excluding unity_dir template destination)
    target_dirs = [outpath, dem_dir, imagery_dir, tile_dir, blender_dir, inkscape_dir, qgis_dir]
    for d in target_dirs:
        os.makedirs(d, exist_ok=True)

    # Project Status Headers
    print(f"--> Project Workspace: {outpath}")
    print(f"    └── Identified Course Identity Base: '{course_basename}'")

    course_name = course_basename.replace(" ", "_")
    pfs = ProjectFolderService(args.output_folder, course_name)
    
    #------------------------------------------------------------------
    #--- OPCD Toolchain Paths & Environment Variables Lookups  --------
    #--- User can specify the path to the ~/OPCD/OSM directory --------
    #------------------------------------------------------------------
    env_osm2svg = os.environ.get("OSM2SVG_EXE_PATH")
    if env_osm2svg:
        OSM2SVG_EXE = os.path.abspath(os.path.expanduser(env_osm2svg))
        print(f"    └── {CYAN}[INFO]{RESET} Using Environment SVG Engine: {OSM2SVG_EXE}")
    else:
        OSM2SVG_EXE = os.path.abspath(os.path.expanduser("~/OPCD/OSM/osm2svg_v9.py"))
        print(f"    └── {CYAN}[INFO]{RESET} Using Toolchain Default SVG Engine: {OSM2SVG_EXE}")

    #--- Unity Base Simulation Project Template ---
    env_unity_base = os.environ.get("UNITY_BASE_PROJECT_PATH")
    if env_unity_base:
        UNITY_BASE_PROJECT = os.path.abspath(os.path.expanduser(env_unity_base))
        print(f"    └── {CYAN}[INFO]{RESET} Using Environment Unity Template: {UNITY_BASE_PROJECT}")
    else:
        UNITY_BASE_PROJECT = os.path.abspath(os.path.expanduser("~/OPCD/Unity_base/GSPBaseProject_v4_2025_12_02"))
        print(f"    └── {CYAN}[INFO]{RESET} Using Toolchain Default Unity Template: {UNITY_BASE_PROJECT}")

    #--- Global Vector Styles Defs ---
    env_styles_base = os.environ.get("OPCD_STYLES_PATH")
    if env_styles_base:
        DEFAULT_STYLE_SRC = os.path.abspath(os.path.expanduser(env_styles_base))
        print(f"    └── {CYAN}[INFO]{RESET} Using Environment GIS Styles: {DEFAULT_STYLE_SRC}")
    else:
        DEFAULT_STYLE_SRC = os.path.abspath(os.path.expanduser("~/OPCD/OSM/styles.json"))
        print(f"    └── {CYAN}[INFO]{RESET} Using Toolchain Default GIS Styles: {DEFAULT_STYLE_SRC}")

    #-------------------------------------------------
    #--- Configuration File Staging (styles.json)   ---
    #-------------------------------------------------
    destination_style_file = os.path.join(outpath, "styles.json")
    style_arg = args.style if hasattr(args, 'style') else None

    if style_arg and os.path.exists(style_arg):
        style_source = os.path.abspath(style_arg)
        print(f"    └── Staging custom runtime style rules: {os.path.basename(style_source)}")
    else:
        style_source = DEFAULT_STYLE_SRC
        print(f"    └── Staging baseline toolchain style definitions.")

    try:
        if os.path.exists(style_source):
            shutil.copy2(style_source, destination_style_file)
            print(f"    └── Cached active style profile rules to project root.")
        else:
            print(f"    └── {YELLOW}[WARN]{RESET} Specified styles.json source not found. Skipping copy.")
    except Exception as e:
        print(f"    └── {RED}[ERROR]{RESET} Error copying style profile definitions: {e}")

    #------------------------------------------------------------------
    #--- Unity Core Asset Base Project Generation ---------------------
    #------------------------------------------------------------------
    if not os.path.exists(unity_dir):
        print(f"    └── Decompressing and staging base Unity environment stack...")
        if os.path.exists(UNITY_BASE_PROJECT):
            try:
                os.makedirs(os.path.dirname(unity_dir), exist_ok=True)
                shutil.copytree(UNITY_BASE_PROJECT, unity_dir, symlinks=False, ignore=None)
                print(f"    └── {GREEN}[SUCCESS]{RESET} Unity target engine workspace created: Unity/{course_basename}/")
            except Exception as e:
                print(f"    └── {RED}[ERROR]{RESET} Error staging base game engine project modules: {e}")
        else:
            print(f"    └── {RED}[CRITICAL]{RESET} Base Unity template source directory missing at {UNITY_BASE_PROJECT}")
    else:
        print(f"    └── Unity engine environment group '{course_basename}' already present. Skipping clone overwrite.")

    if not os.path.exists(OSM2SVG_EXE):
        print(f"    └── {YELLOW}[WARN]{RESET} Pipeline Tracker Notice: Could not resolve file execution target at {OSM2SVG_EXE}")
         
    #========================================#
    #--- SECTION 2. Coordinate Management ---#
    #========================================#
    # Wired up to the clean `--inner_size` and `--outer_size` parameters
    factory_coords = CoordinateManager(args.lat, args.lon, args.inner_size, args.outer_size)
    manifest_file = os.path.join(pfs.paths["etc"], "Project_coords.txt")
    discovery_osm = os.path.join(pfs.paths["etc"], "discovery_map.osm")

    # Path A: Load from existing local manifest (The fastest way)
    if os.path.exists(manifest_file):
        print(f"--> {GREEN}[INFO]{RESET} Using existing manifest: {manifest_file}")
        factory_coords.load_from_manifest(manifest_file)
    
    # Path B: Auto-Center/Discovery (The "First Run" or "Force" way)
    elif args.auto_center:
        if not os.path.exists(discovery_osm):
            print("--> Phase 0: Downloading discovery OSM for centering...")
            init_bbox = factory_coords.get_bbox(factory_coords.inner_m)
            osm = OSMService()
            osm.download(
                bbox=init_bbox,
                output_path=discovery_osm,
                center_lat=factory_coords.lat,
                center_lon=factory_coords.lon,
                inner_m=factory_coords.inner_m,
                outer_m=factory_coords.outer_m
            )

        if os.path.exists(discovery_osm):
            if factory_coords.recenter_from_osm(discovery_osm, args.output_folder):
                print(f"--> {GREEN}[NEW CENTER FOUND]{RESET} lat: {factory_coords.lat:.6f}, lon: {factory_coords.lon:.6f}")
            else:
                print(f"--> {YELLOW}[NOTICE]{RESET} No 'leisure=golf_course' found. Using provided coords.")
                
    # Always finalize these variables so the rest of the script has them!
    inner_bbox = factory_coords.get_bbox(factory_coords.inner_m)
    outer_bbox = factory_coords.get_bbox(factory_coords.outer_m)
    current_lat = factory_coords.lat
    current_lon = factory_coords.lon

    # Ensure the manifest is always up to date with the current state
    pfs.generate_project_report(
        lat=current_lat,
        lon=current_lon,
        inner_m=factory_coords.inner_m,
        outer_m=factory_coords.outer_m,
        inner_bbox=inner_bbox,
        outer_bbox=outer_bbox
    )
    
    #===========================================#
    #--- SECTION 3. DATA SERVICES HARVESTING ---#
    #===========================================#
    harvester = DataHarvester(dem_dir, qgis_dir)
    
    # 1. Dynamic Imagery Folder Detection
    
    found_ortho_paths = (
        glob.glob(os.path.join(tile_dir, "orthos-phase3", "*.tif")) or  
        glob.glob(os.path.join(tile_dir, "orthos-phase2", "*.tif")) 
    )
    
    if found_ortho_paths:
        ortho_source_subfolder = os.path.dirname(found_ortho_paths[0])
    else:
        ortho_source_subfolder = os.path.join(tile_dir, "orthos-phase3")

    # 2. Dynamic DEM Subfolder Detection
    found_dem_paths = (
        glob.glob(os.path.join(dem_dir, "dem-phase3", "*.tif")) or
        glob.glob(os.path.join(dem_dir, "dem-phase2", "*.tif")) or
        glob.glob(os.path.join(dem_dir, "*.tif"))
    )
    if found_dem_paths:
        active_dem_source_dir = os.path.dirname(found_dem_paths[0])
    else:
        active_dem_source_dir = dem_dir

    # 1. OSM Check-n-Download Define the osm_path path clearly
    osm_file = os.path.join(args.output_folder, "inner_map.osm")

    # 2. Check for existence and force flag
    if os.path.exists(osm_file) and not (args.force_osm or args.download_osm):
        print(f"--> {GREEN}[INFO]{RESET} Existing OSM data found. Skipping Overpass query.")
    else:
        # Only download if the user actually requested OSM OR if we are forcing a refresh
        if args.download_osm or args.force_osm:
            print(f"--> Downloading Open Street Map (OSM) located at Latitude={current_lat} Longitude={current_lon}")
            osm = OSMService()
            osm.download(
                bbox=inner_bbox, 
                output_path=osm_file, # Use the variable we already defined
                # Use the variables that might have been updated by auto_center
                center_lat=current_lat, 
                center_lon=current_lon, 
                inner_m=args.inner_size,
                outer_m=args.outer_size
            )

    if args.ky_dem or args.ky_ortho:
        existing_dem_tiles = glob.glob(os.path.join(active_dem_source_dir, "*.tif"))
        existing_ortho_tiles = glob.glob(os.path.join(ortho_source_subfolder, "*.tif"))
         
        need_dem = not existing_dem_tiles or args.force_dem
        need_ortho = not existing_ortho_tiles or args.force_ortho
         
        if not need_dem and args.ky_dem:
            print(f"--> Found {len(existing_dem_tiles)} previous DEM files in {os.path.basename(active_dem_source_dir)}. Skipping LiDAR download...")
        if not need_ortho and args.ky_ortho:
            print(f"--> Found {len(existing_ortho_tiles)} previous Ortho plates in {os.path.basename(ortho_source_subfolder)}. Skipping imagery download...")

        if (args.ky_dem and need_dem) or (args.ky_ortho and need_ortho):
            print("--> Launching abovepy harvester for missing raw assets...")
            harvester.fetch_ky_assets(outer_bbox)
            
            # Post-download re-scan to map paths cleanly if they were just created
            found_ortho_paths = (
                glob.glob(os.path.join(tile_dir, "orthos-phase3", "*.tif")) or  
                glob.glob(os.path.join(tile_dir, "orthos-phase2", "*.tif"))  
            )
            if found_ortho_paths:
                ortho_source_subfolder = os.path.dirname(found_ortho_paths[0])
                
            found_dem_paths = (
                glob.glob(os.path.join(dem_dir, "dem-phase3", "*.tif")) or
                glob.glob(os.path.join(dem_dir, "dem-phase2", "*.tif")) or
                glob.glob(os.path.join(dem_dir, "*.tif"))
            )
            if found_dem_paths:
                active_dem_source_dir = os.path.dirname(found_dem_paths[0])

    if args.ky_ortho or args.rebuild_ortho:
        print("--> Processing Inkscape background maps locally via GDAL...")
         
        inner_te_list = [inner_bbox[1], inner_bbox[0], inner_bbox[3], inner_bbox[2]]
        outer_te_list = [outer_bbox[1], outer_bbox[0], outer_bbox[3], outer_bbox[2]]
         
        if not glob.glob(os.path.join(ortho_source_subfolder, "*.tif")):
            print(f"--> {RED}[ERROR]{RESET} No downloaded ortho tiles found to stitch in: {ortho_source_subfolder}")
        else:
            print(f"     └── Stitched Imagery Source: {os.path.basename(ortho_source_subfolder)}")
            img_service = ImageryService()
            img_service.assemble_local_orthos(
                tile_dir=ortho_source_subfolder,
                output_folder=imagery_dir,
                inner_te=inner_te_list,
                outer_te=outer_te_list
            )
             
    if args.google_sat:
        print("--> Building Google Satellite layers...")
        google_service = ImageryService(provider="Google")
        if not os.path.exists(os.path.join(imagery_dir, "inner_google.png")):
            google_service.fetch_and_stitch(inner_bbox, zoom=SATELLITE_ZOOM, output_path=os.path.join(imagery_dir, "inner_google.png"))
        else:
            print(f"[Skipping] {os.path.join(imagery_dir, "inner_google.png")} exist")
        if not os.path.exists(os.path.join(imagery_dir, "outer_google.png")):
            google_service.fetch_and_stitch(outer_bbox, zoom=SATELLITE_ZOOM, output_path=os.path.join(imagery_dir, "outer_google.png"))
        else:
            print(f"[Skipping] {os.path.join(imagery_dir, "outer_google.png")} exist")
        
    if args.bing_sat:
        print("--> Building Bing Satellite layers...")
        bing_service = ImageryService(provider="Bing")
        if not os.path.exists(os.path.join(imagery_dir, "inner_bing.png")):
            bing_service.fetch_and_stitch(inner_bbox, zoom=SATELLITE_ZOOM, output_path=os.path.join(imagery_dir, "inner_bing.png"))
        else:
            print(f"[Skipping] {os.path.join(imagery_dir, "inner_bing.png")} exist");
        if not os.path.exists(os.path.join(imagery_dir, "outer_bing.png")):
            bing_service.fetch_and_stitch(outer_bbox, zoom=SATELLITE_ZOOM, output_path=os.path.join(imagery_dir, "outer_bing.png"))
        else:
            print(f"[Skipping] {os.path.join(imagery_dir, "outer_bing.png")} exist");
  
    #=========================================
    #---  SECTION 4. TERRAIN PROCESSING    ---
    #=========================================  
    if args.rebuild_obj or args.rebuild_hillshade or args.rebuild_heightmap or args.ky_dem:
        print("--> Initializing Terrain Service Engine...")
        
        terrain = TerrainService(active_dem_source_dir)
        elevation_source = active_dem_source_dir
        
        # Clean casting from the modified meters context attributes
        inner_size_int = int(args.inner_size)
        outer_size_int = int(args.outer_size)
         
        inner_tif = os.path.join(pfs.paths["tiles"], elevation_source, f"inner_{inner_size_int}m.tif")
        outer_tif = os.path.join(pfs.paths["tiles"], elevation_source, f"outer_{outer_size_int}m.tif")
        
        inner_heightmap_png = os.path.join(outpath, f"inner_heightmap_{inner_size_int}m.png")
        outer_heightmap_png = os.path.join(outpath, f"outer_heightmap_{outer_size_int}m.png")
        inner_hillshade_png = os.path.join(imagery_dir, f"inner_hillshade_{inner_size_int}m.png")

        inner_obj_path = os.path.join(pfs.paths['blender'], f"inner_terrain_{inner_size_int}m.obj")
        outer_obj_path = os.path.join(pfs.paths['blender'], f"outer_terrain_{outer_size_int}m.obj")
        
        raw_dem_tiles = glob.glob(os.path.join(active_dem_source_dir, "*.tif"))
        source_tiles = [t for t in raw_dem_tiles if "inner_" not in t and "outer_" not in t]

        if not source_tiles:
            print(f"--> {RED}[ERROR]{RESET} No source LiDAR/DEM tiles found in {active_dem_source_dir}! Cannot build terrain datasets.")
        else:
            print(f"     └── Elevation Model Source: {os.path.basename(active_dem_source_dir)}")
            if os.path.exists(inner_tif):
                print(f"--> Found existing base GeoTIFF: {inner_tif}. Skipping build...")
            else:
                print(f"--> [INFO] Generating base GeoTIFF: {inner_tif}...")
                terrain.process_dem(active_dem_source_dir, inner_bbox, label=f"inner_{inner_size_int}m", res=0.6096)
    
            if os.path.exists(outer_tif):
                print(f"--> Found existing base GeoTIFF: {outer_tif}. Skipping build...")
            else:
                print(f"--> [INFO] Generating base GeoTIFF: {outer_tif}...")
                terrain.process_dem(active_dem_source_dir, outer_bbox, label=f"outer_{outer_size_int}m", res=1.2192)

        #--- STEP B: 16-Bit Heightmap Generation ---
            if not os.path.exists(inner_heightmap_png) or args.rebuild_heightmap:
                print(f"--> Calculating 16-bit Inner Heightmap ({inner_size_int}m)...")
                terrain.make_heightmap(inner_tif, inner_heightmap_png, min_z=420, max_z=620)
             
            if not os.path.exists(outer_heightmap_png) or args.rebuild_heightmap:
                print(f"--> Calculating 16-bit Outer Heightmap ({outer_size_int}m)...")
                terrain.make_heightmap(outer_tif, outer_heightmap_png, min_z=420, max_z=620)

        #--- STEP C: Hillshade Overlay Generation ---
            if not os.path.exists(inner_hillshade_png) or args.rebuild_hillshade:
                print(f"--> Rendering terrain Hillshade preview PNG ({inner_size_int}m)...")
                terrain.make_hillshade(inner_tif, inner_hillshade_png, z=3)

        #--- STEP D: 3D Mesh Export ---
            if not os.path.exists(inner_obj_path) or args.rebuild_obj:
                print(f"--> Exporting optimized Blender/Unity OBJ mesh ({inner_size_int}m)...")
                terrain.export_obj_from_tif(inner_tif, inner_obj_path, scale_z=KY_Z_SCALE)
            if not os.path.exists(outer_obj_path) or args.rebuild_obj:
                terrain.export_obj_from_tif(outer_tif, outer_obj_path, scale_z=KY_Z_SCALE)
 
    #--- Build the Reminder.txt ---
    etc_path = pfs.paths["etc"]
    reminder_file = os.path.join(etc_path, "Reminder.txt")

    # We only create it if it doesn't exist, so we don't overwrite it if it's already there
    if not os.path.exists(reminder_file):
        with open(reminder_file, "w") as f:
            f.write("1) Please download a splash.jpg image you would like to have for the course (Required for GSPro).\n")
            f.write("2) Please create a description of the course (history, features, special events) and place it in ~/etc/coursedescription.txt (Required for GSPro).\n")
        print(f"--> {YELLOW}[NOTICE]{RESET} Created course asset reminders in {reminder_file}")
    

    #--- SVG WORKFLOW BLOCK ---
    if args.osm_to_svg:
        print("--> Executing osm2svg_v9.py...")
        script_path = os.path.expanduser("~/OPCD/OSM/osm2svg_v9.py")
        subprocess.run([
            "python3", script_path,
            "--infile", os.path.join(pfs.paths["osm"], "inner_map.osm"),
            "--outfile", os.path.join(pfs.paths["inkscape"], "inner_out.svg")
        ], check=True)

    if args.svg_water_edge:
        print("--> Adjusting SVG water edges...")
        script_path = os.path.expanduser("~/OPCD/OSM/svg_water_edge.py")
        subprocess.run([
            "python3", script_path,
            "--file", os.path.join(pfs.paths["inkscape"], "inner_out.svg")
        ], check=True)

    print(f"\n{GREEN}>>> Kymapfactory4opcd tasks finished.{RESET}")
    
if __name__ == "__main__":
    main()
    
