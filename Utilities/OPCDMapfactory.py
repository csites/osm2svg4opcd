#!/usr/bin/env python3
"""
OPCDmapfactory.py.   This Utility program is used to aligned inner and outer satellite images where the inner images can be used as background images for osm2svg4opcd. (osm2svg_v9.py).  It also extracts from your collection of DEM / LAZ tile, the height map, and hillshade image.  It also builds from the height map an inner_terrain.obj and outer_terrain.obj.   It organizes the output and stores these into your output projects folder.

EXAMPLE: ./OPCDMapfactory.py -lat 38.17345 -lon -85.56277 --ky_dem --ky_ortho --google_sat --bing_sat --download_osm -o ~/Projects/Charlie_Vettner

./OPCDMapfactory.py -lat 38.1732061 -lon -85.5626482 --ky_dem --ky_ortho --google_sat --bing_sat --download_osm -o ~/Projects/Seneca_Golf_Club

./OPCDMapfactory.py -lat 38.1732061 -lon -85.5626482 --ky_dem --ky_ortho --google_sat --bing_sat --download_osm -o ~/Projects/Charlie_Vettner

"""
import os
import sys
import math
import time
import json
import argparse
import requests
import textwrap
import mercantile
import numpy as np
import rasterio
import pyproj

import abovepy # For KY only
import subprocess # To run gdal commands
from PIL import Image
from rasterio.merge import merge
from rasterio.mask import mask
from shapely.geometry import box
from shapely.ops import transform
from pystac_client import Client
from scipy.ndimage import gaussian_filter

# Base URL for the Overpass API
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
STYLES_FILE = "styles.json"
KY_PHASE2_Z_SCALE = 0.6096 # 2ft elevation per pixel

# ---------------------------------------------------------------------------------
# --- CLASS A1. CoordinateManager                                               ---
# ---    CoordinateManager.__init__(self, lat, lon, inner_m=2000, outer_m=4000) ---
# ---    CoordinateManager.get_bbox(self, size_m)                               ---
# ---    CoordinateManager.calculate_unity_params(self, inner_min, outer_min)   ---
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

    def calculate_unity_params(self, inner_min, outer_min):
        """Calculates the vertical offset between inner/outer grids."""
        return (outer_min * self.FT2M) - (inner_min * self.FT2M)


# -----------------------------------------------------------------------------------------------
# --- CLASS A2. ImageryService                                                                ---
# ---    ImageryService.__init__(self, provider="Google", cache_dir="~/projects/cache/tiles") ---
# ---    ImageryService._to_bing_quadkey(self, x, y, z)                                       ---
# ---    ImageryService.fetch_and_stitch(self, bbox, zoom=19, output_path="satellite.png")    ---
# ---    ImageryService.get_safe_zoom(self, bbox, target_tiles=500)                           ---
# ---    ImageryService.write_world_file(image_path, bbox, width, height)                     ---
# -----------------------------------------------------------------------------------------------
class ImageryService:
    def __init__(self, provider="Google", cache_dir="~/Projects/cache/tiles"):
        self.providers = {
            "Google": "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
            "Bing": "https://ecn.t3.tiles.virtualearth.net/tiles/a{q}.jpeg?g=1"
        }

        self.provider = provider
        self.url_template = self.providers.get(provider)
        self.cache_dir = os.path.expanduser(cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)

    def _to_bing_quadkey(self, x, y, z):
        """Converts XYZ to Bing's unique QuadKey string."""
        quadkey = ""
        for i in range(z, 0, -1):
            digit = 0
            mask = 1 << (i - 1)
            if (x & mask) != 0: digit += 1
            if (y & mask) != 0: digit += 2
            quadkey += str(digit)
        return quadkey

    def fetch_and_stitch(self, bbox, zoom=19, output_path="satellite.png"):
        min_lat, min_lon, max_lat, max_lon = bbox
        tiles = list(mercantile.tiles(min_lon, min_lat, max_lon, max_lat, zoom))
        num_tiles = len(tiles)

        # SAFETY GATE: Prevent accidental massive downloads
        if num_tiles > 800:
            print(f"⚠️  Zoom {zoom} is too dense for this area ({num_tiles} tiles).")
            return None

        print(f"🛰  Stitching {num_tiles} tiles into {output_path}...")

        min_x = min(t.x for t in tiles)
        max_x = max(t.x for t in tiles)
        min_y = min(t.y for t in tiles)
        max_y = max(t.y for t in tiles)

        grid_w = (max_x - min_x + 1) * 256
        grid_h = (max_y - min_y + 1) * 256
        canvas = Image.new("RGB", (grid_w, grid_h))

        for i, t in enumerate(tiles):
            tile_filename = os.path.join(self.cache_dir, f"{self.provider}_{t.z}_{t.x}_{t.y}.jpg")

            if not os.path.exists(tile_filename):
                # FIX: Conditional formatting to prevent KeyErrors
                if self.provider == "Bing":
                    url = self.url_template.format(q=self._to_bing_quadkey(t.x, t.y, t.z))
                else:
                    url = self.url_template.format(x=t.x, y=t.y, z=t.z)

                try:
                    r = requests.get(url, timeout=15)
                    if r.status_code == 200:
                        with open(tile_filename, "wb") as f:
                            f.write(r.content)
                        # ⏳ RATE LIMIT: Give the network a 100ms breather
                        time.sleep(0.1)
                except Exception as e:
                    print(f"\n❌ Network Error on tile {t.x},{t.y}: {e}")
                    continue

            if os.path.exists(tile_filename):
                tile_img = Image.open(tile_filename)
                canvas.paste(tile_img, ((t.x - min_x) * 256, (t.y - min_y) * 256))

        # PRECISE CROPPING
        # Get the geographic bounds of the entire tile grid we just stitched
        # We use the min/max tiles and the zoom level
        west, south, _, _ = mercantile.bounds(min_x, max_y, zoom)
        _, _, east, north = mercantile.bounds(max_x, min_y, zoom)

        left = grid_w * (min_lon - west) / (east - west)
        top = grid_h * (north - max_lat) / (north - south)
        right = grid_w * (max_lon - west) / (east - west)
        bottom = grid_h * (north - min_lat) / (north - south)

        final_img = canvas.crop((left, top, right, bottom))
        final_img.save(output_path)
        print(f"\n✅ Saved image to {output_path}")
        self.write_world_file(output_path, bbox, final_img.width, final_img.height)

    def get_safe_zoom(self, bbox, target_tiles=500):
        for z in range(19, 10, -1):
            tiles = list(mercantile.tiles(bbox[1], bbox[0], bbox[3], bbox[2], z))
            if len(tiles) <= target_tiles:
                return z
        return 12

    def write_world_file(self, image_path, bbox, width, height):
            """
            Writes a sidecar file (.pgw for .png) for georeferencing.
            """
            # (Your math logic remains the same)
            lon_width = bbox[3] - bbox[1]
            lat_height = bbox[2] - bbox[0]
            pixel_x_size = lon_width / width
            pixel_y_size = -lat_height / height

            world_file_content = [
                f"{pixel_x_size}\n", "0.0\n", "0.0\n", f"{pixel_y_size}\n",
                f"{bbox[1]}\n", f"{bbox[2]}\n"
            ]

            # Use the specific extension for the image type (e.g., .png -> .pgw)
            ext_map = {".png": ".pgw", ".jpg": ".jgw", ".jpeg": ".jgw"}
            base, ext = os.path.splitext(image_path)
            sidecar_ext = ext_map.get(ext.lower(), ".wld")

            world_path = base + sidecar_ext
            with open(world_path, "w") as f:
                f.writelines(world_file_content)
            print(f"🌍 Georeference created: {world_path}")


# ------------------------------------------------------------------------------------------------------------
# --- CLASS A3. TerrainService                                                                             ---
# ---    TerrainService.__init__(self, dem_folder)                                                         ---
# ---    TerrainService.export_obj(self, elevation_array, output_path, scale_factor=1.0)                   ---
# ---    TerrainService.make_hillshade(self, elevation_array, output_path, bbox, azimuth=315, altitude=45) ---
# ---    TerrainService.make_heightmap(self, elevation_array, output_path, bbox=None, sigma=1.0)           ---
# ---    TerrainService.process_files(self, file_list, target_bbox, label="inner")                         ---
# ---    TerrainService.write_world_file(self, image_path, bbox, width, height)                            ---
# ------------------------------------------------------------------------------------------------------------
class TerrainService:
    def __init__(self, dem_folder):
        self.dem_folder = os.path.expanduser(dem_folder)
        os.makedirs(self.dem_folder, exist_ok=True)

    def export_obj(self, elevation_array, output_path, anchor_bbox=None, scale_factor=0.3048):
        rows, cols = elevation_array.shape

        # 1. Calculate the real-world width and height in meters
        # For a 2000m box, these should be 2000.0
        # If anchor_bbox is (min_lat, min_lon, max_lat, max_lon):
        width_m = (anchor_bbox[3] - anchor_bbox[1]) * (111320 * math.cos(math.radians(anchor_bbox[0])))
        height_m = (anchor_bbox[2] - anchor_bbox[0]) * 111320

        # 2. Define how many meters are between each point
        x_spacing = width_m / (cols - 1)
        y_spacing = height_m / (rows - 1)

        print(f"📐 OBJ Resolution: {x_spacing:.3f}m x {y_spacing:.3f}m per vertex")

        with open(output_path, "w") as f:
            f.write("# OPCD Map Factory - Charlie Vettner Fix\n")

            # --- VERTICES ---
            for r in range(rows):
                for c in range(cols):
                    # APPLY THE FLIP:
                    # c going from 0 to cols-1 becomes (cols-1-c) to reverse East/West
                    # r going from 0 to rows-1 becomes (rows-1-r) to reverse North/South
                    # v_x = (cols - 1 - c) * x_spacing # Odd.  Needs an
                    # v_y = (rows - 1 - r) * y_spacing
                    # v_z = elevation_array[r, c] * scale_factor
                    #
                    #v_x = c * x_spacing
                    #v_y = (rows - 1 - r) * y_spacing
                    #v_z = elevation_array[r, c] * scale_factor
                    #
                    #v_x = (cols - 1 - c) * x_spacing  # Mirror X
                    #v_y = r * y_spacing               # Don't invert R (let it stay "Screen Style")
                    #v_z = elevation_array[r, c] * KY_PHASE2_Z_SCALE
                    max_y = rows * y_spacing
                    v_x = - (c * x_spacing)
                    v_y = - (r * y_spacing) + max_y
                    v_z = elevation_array[r, c] * KY_PHASE2_Z_SCALE

                    # Blender Y is North/South, Blender Z is Up/Down
                    f.write(f"v {v_x:.4f} {v_z:.4f} {v_y:.4f}\n")

            # --- FACES (remains the same) ---
            for r in range(rows - 1):
                for c in range(cols - 1):
                    v1 = r * cols + c + 1
                    v2 = r * cols + (c + 1) + 1
                    v3 = (r + 1) * cols + (c + 1) + 1
                    v4 = (r + 1) * cols + c + 1
                    f.write(f"f {v1} {v2} {v3} {v4}\n")

        print(f"✅ Aligned OBJ saved to {output_path}")

    def make_obj_model(self, elevation_array, output_path, bbox, scale_z=0.3048, sigma=3.0):
        from scipy.ndimage import gaussian_filter
        print(f"🏗  Building 3D Terrain Mesh: {output_path}...")

        # 1. Smoothing (Seneca-style)
        data = gaussian_filter(elevation_array, sigma=sigma) if sigma > 0 else elevation_array

        rows, cols = data.shape

        # 2. Coordinate Math for Spacing
        avg_lat = (bbox[0] + bbox[2]) / 2.0
        width_m = (bbox[3] - bbox[1]) * (111320 * math.cos(math.radians(avg_lat)))
        height_m = (bbox[2] - bbox[0]) * 111320

        x_spacing = width_m / (cols - 1)
        y_spacing = height_m / (rows - 1)

        with open(output_path, 'w') as f:
            f.write(f"# OPCD Terrain Model: {os.path.basename(output_path)}\n")

            # 3. Generate Vertices (Top-Right Aligned & Rotated)
            for r in range(rows):
                for c in range(cols):
                    # APPLY THE FLIP (180 deg Z-rotation)
                    # This ensures it matches your SVG coordinates in Blender
                    # x = (cols - 1 - c) * x_spacing
                    # y = (rows - 1 - r) * y_spacing

                    # We use absolute elevation * scale_z (No min_z subtraction)
                    # This keeps different mesh chunks vertically aligned.
                    # z = float(data[r, c]) * scale_z
                    #
                    #v_x = c * x_spacing
                    #v_y = (rows - 1 - r) * y_spacing
                    #v_z = elevation_array[r, c] * scale_factor
                    #
                    # v_x = (cols - 1 - c) * x_spacing  # Mirror X
                    # v_y = r * y_spacing               # Don't invert R (let it stay "Screen Style")
                    # v_z = elevation_array[r, c] * KY_PHASE2_Z_SCALE
                    #
                    max_y = rows * y_spacing
                    v_x = - (c * x_spacing)
                    v_y = - (r * y_spacing) + max_y
                    v_z = elevation_array[r, c] * KY_PHASE2_Z_SCALE

                    # Blender Y is North/South, Blender Z is Up
                    f.write(f"v {x:.6f} {z:.6f} {y:.6f}\n")

            # 4. Generate Faces
            for r in range(rows - 1):
                for c in range(cols - 1):
                    v1 = r * cols + c + 1
                    v2 = r * cols + (c + 1) + 1
                    v3 = (r + 1) * cols + (c + 1) + 1
                    v4 = (r + 1) * cols + c + 1
                    f.write(f"f {v1} {v2} {v3} {v4}\n")

        print(f"✅ 3D Mesh successful: {rows*cols} vertices at {x_spacing:.3f}m resolution.")


    def make_hillshade(self, elevation_array, output_path, bbox, azimuth=315, altitude=45):
        print(f"🌄  Generating high-contrast hillshade for 'The Vet'...")

        # 1. GET ACCURATE SPACING
        # We need to tell the gradient the actual distance between LIDAR points
        # to get the true steepness of bunker lips and green tiers.
        rows, cols = elevation_array.shape
        width_m = (bbox[3] - bbox[1]) * (111320 * np.cos(np.radians(bbox[0])))
        height_m = (bbox[2] - bbox[0]) * 111320
        dx = width_m / (cols - 1)
        dy = height_m / (rows - 1)

        # 2. CALCULATE GRADIENTS WITH SPACING
        y, x = np.gradient(elevation_array, dy, dx)

        # Standard hillshade math (Slope and Aspect)
        slope = np.pi/2. - np.arctan(np.sqrt(x*x + y*y))
        aspect = np.arctan2(-x, y)

        azimuth_rad = azimuth * np.pi / 180.
        altitude_rad = altitude * np.pi / 180.

        shaded = np.sin(altitude_rad) * np.sin(slope) + \
                 np.cos(altitude_rad) * np.cos(slope) * \
                 np.cos(azimuth_rad - aspect)

        # 3. CONTRAST STRETCHING (The Inkscape Fix)
        # Instead of just (shaded + 1) / 2, we force the values to
        # occupy the full 0-255 spectrum.
        hs_min, hs_max = shaded.min(), shaded.max()
        if hs_max > hs_min:
            # Stretch the calculated shading to 0.0 - 1.0 range
            normalized = (shaded - hs_min) / (hs_max - hs_min)
        else:
            normalized = shaded

        # Convert to 8-bit for Inkscape
        img_data = (normalized * 255).astype(np.uint8)
        img = Image.fromarray(img_data)
        img.save(output_path)

        img_width, img_height = img.size
        self.write_world_file(output_path, bbox, img_width, img_height)
        print(f"✅ Hillshade saved with full contrast stretching.")


    def make_heightmap(self, elevation_array, output_path, bbox, sigma=1.0):
        """
        Generates a 16-bit grayscale heightmap with full contrast stretching.
        """
        from scipy.ndimage import gaussian_filter
        print(f"🌫  Generating high-contrast 16-bit heightmap: {os.path.basename(output_path)}")

        # 1. Smooth the data to remove LIDAR artifacts
        if sigma > 0:
            data = gaussian_filter(elevation_array, sigma=sigma)
        else:
            data = elevation_array

        # 2% Percentile Clip (Matches QGIS "Stretch to Min/Max" behavior)
        p2, p98 = np.percentile(data, (2, 98))
        data_clipped = np.clip(data, p2, p98)

        # Normalize the clipped data
        normalized = (data_clipped - p2) / (p98 - p2)
        img_data = (normalized * 65535).astype(np.uint16)

        # 3. Save as PNG
        img = Image.fromarray(img_data)
        img.save(output_path)

        # 4. Georeference logic (matching our updated signature)
        img_width, img_height = img.size
        self.write_world_file(output_path, bbox, img_width, img_height)

        print(f"✅ Heightmap saved (Elevation Range: {z_min:.2f}m - {z_max:.2f}m)")


    def process_files(self, file_list, target_bbox, label="inner"):
        if not file_list:
            return None, None

        print(f"🧩 Stitching and Clipping {len(file_list)} tiles for {label} area...")

        try:
            src_files = [rasterio.open(f) for f in file_list]
            mosaic, out_trans = merge(src_files)
            src_crs = src_files[0].crs

            min_lat, min_lon, max_lat, max_lon = target_bbox

            # 1. Create a transformer from WGS84 (Degrees) to DEM CRS (usually Feet/Meters)
            transformer = pyproj.Transformer.from_crs("EPSG:4326", src_crs, always_xy=True)

            # 2. Transform the cornqers of your box
            west, south = transformer.transform(min_lon, min_lat)
            east, north = transformer.transform(max_lon, max_lat)

            # 3. Create the clipping geometry in the LOCAL coordinate system
            bbox_geom = box(west, south, east, north)

            with rasterio.io.MemoryFile() as memfile:
                with memfile.open(
                    driver='GTiff', height=mosaic.shape[1], width=mosaic.shape[2],
                    count=1, dtype=mosaic.dtype, crs=src_crs, transform=out_trans
                ) as dataset:
                    dataset.write(mosaic)
                    out_image, out_transform = mask(dataset, [bbox_geom], crop=True)

            for src in src_files: src.close()

            elevation_array = out_image[0]
            print(f"✅ {label.upper()} terrain processed. Shape: {elevation_array.shape}")
            return elevation_array, out_transform

        except Exception as e:
            print(f"❌ Error in process_files: {e}")
            return None, None

    def write_world_file(self, image_path, bbox, width, height):
        """
        Writes a sidecar file (.pgw for .png) for georeferencing.
        """
        # (Your math logic remains the same)
        lon_width = bbox[3] - bbox[1]
        lat_height = bbox[2] - bbox[0]
        pixel_x_size = lon_width / width
        pixel_y_size = -lat_height / height

        world_file_content = [
            f"{pixel_x_size}\n", "0.0\n", "0.0\n", f"{pixel_y_size}\n",
            f"{bbox[1]}\n", f"{bbox[2]}\n"
        ]

        # Use the specific extension for the image type (e.g., .png -> .pgw)
        ext_map = {".png": ".pgw", ".jpg": ".jgw", ".jpeg": ".jgw"}
        base, ext = os.path.splitext(image_path)
        sidecar_ext = ext_map.get(ext.lower(), ".wld")

        world_path = base + sidecar_ext
        with open(world_path, "w") as f:
            f.writelines(world_file_content)
        print(f"🌍 Georeference created: {world_path}")

# --------------------------------------------------------------------------------------
# --- CLASS A4. DataHarvester                                                        ---
# ---    DataHarvester.__init__(self, dem_dir, laz_dir)                              ---
# ---    DataHarvester.fetch_ky_assets(self, bbox)                                   ---
# ---    DataHarvester.fetch_ky_imagery(self, bbox)                                  ---
# ---    DataHarvester.fetch_ky_ortho(self, bbox, output_path, resolution=0.5)       ---
# ---    DataHarvester.harvest_all_imagery(self, inner_bbox, outer_bbox, output_dir) ---
# --------------------------------------------------------------------------------------
class DataHarvester:
    def __init__(self, dem_dir):
        self.dem_dir = os.path.expanduser(dem_dir)
        self.imagery_dir = os.path.join(self.dem_dir, "Ortho_Imagery")
        os.makedirs(self.dem_dir, exist_ok=True)
        os.makedirs(self.imagery_dir, exist_ok=True)

        self.ky_ortho_url = (
                    "https://kyraster.ky.gov/arcgis/rest/services/ImageServices/"
                    "Ky_KyFromAbove_2022_6in_Phase3/ImageServer/exportImage"
        )

    def fetch_ky_assets(self, bbox):

        # West, South, East, North for abovepy
        search_bbox = (bbox[1], bbox[0], bbox[3], bbox[2])

        dem_paths = []
        img_paths = []

        print(f"⛰️  Searching KyFromAbove for Phase 3 assets...")

        try:
            # 1. DEM SEARCH & FALLBACK
            print("   🔍 Checking dem_phase3...")
            dem_tiles = abovepy.search(bbox=search_bbox, product="dem_phase3")

            if dem_tiles.empty:
                print("   ⚠️  Phase 3 DEM not available. Trying dem_phase2...")
                dem_tiles = abovepy.search(bbox=search_bbox, product="dem_phase2")

            if not dem_tiles.empty:
                print(f"   📥 Downloading {len(dem_tiles)} DEM tiles...")
                dem_paths = abovepy.download(dem_tiles, output_dir=self.dem_dir)
            else:
                print("   ❌ No DEM tiles found in Phase 3 or Phase 2.")

            # 2. ORTHO Imagery SEARCH & FALLBACK
            print("   🔍 Checking ortho_phase3...")
            img_tiles = abovepy.search(bbox=search_bbox, product="ortho_phase3")

            if img_tiles.empty:
                print("   ⚠️  Phase 3 Ortho not available. Trying ortho_phase2...")
                img_tiles = abovepy.search(bbox=search_bbox, product="ortho_phase2")

            if not img_tiles.empty:
                print(f"   📥 Downloading {len(img_tiles)} Ortho tiles...")
                img_paths = abovepy.download(img_tiles, output_dir=self.imagery_dir)
            else:
                print("   ❌ No Ortho tiles found in Phase 3 or Phase 2.")

        except Exception as e:
            print(f"❌ Harvester Error during search/download: {e}")

        # Always return the tuple to keep main() happy
        return dem_paths, img_paths


    def fetch_ky_imagery(self, bbox, phase="ortho_phase3"):
        """
        Fetches the high-res Kentucky-specific orthoimagery (3-inch or 6-inch).
        """
        print(f"📸 Fetching Kentucky {phase} imagery...")
        results = abovepy.search(
            bbox=(bbox[1], bbox[0], bbox[3], bbox[2]),
            product=phase
        )
        # abovepy handles the download and returns the file paths
        return abovepy.download(results, output_dir=self.imagery_dir)

    def harvest_all_imagery(self, inner_bbox, outer_bbox, output_dir):
        """Fetches Inner and Outer KY Orthos and handles georeferencing automatically."""
        # 1. Fetch High-Res Inner
        inner_path = os.path.join(output_dir, "inner_ky_ortho.png")
        self.fetch_ky_ortho(inner_bbox, inner_path, resolution=0.15)

        # 2. Fetch Lower-Res Outer
        outer_path = os.path.join(output_dir, "outer_ky_ortho.png")
        self.fetch_ky_ortho(outer_bbox, outer_path, resolution=0.60)

        print(f"📸 Imagery complete: High-res {inner_path} and Wide-angle {outer_path}")

        
    def fetch_ky_ortho(self, bbox, output_path, resolution=0.15):
        """
        Fetches imagery from the Kentucky Phase 2 ImageServer.
        """
        # 1. Update the base_url here to point to Phase 2 (Louisville area)
        base_url = "https://kyraster.ky.gov/arcgis/rest/services/ImageServices/Ky_KYAPED_Phase2_6IN/ImageServer/exportImage"

        # 2. Extract bounding box coordinates
        min_lat, min_lon, max_lat, max_lon = bbox

        # 3. Calculate pixel dimensions based on resolution
        # (This math ensures the image covers your 2000m or 4000m box correctly)
        width_m = (max_lon - min_lon) * (111320 * math.cos(math.radians(min_lat)))
        height_m = (max_lat - min_lat) * 111320
        
        width_px = int(width_m / resolution)
        height_px = int(height_m / resolution)

        # 4. Construct the parameters for the ArcGIS REST API
        params = {
            "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "bboxSR": "4326",
            "imageSR": "4326",
            "size": f"{width_px},{height_px}",
            "format": "png",
            "f": "image"
        }

        # 5. Execute the request
        try:
            import requests
            print(f"📸 Fetching Phase 2 Ortho ({width_px}x{height_px} px)...")
            response = requests.get(base_url, params=params, timeout=60)
            response.raise_for_status()

            with open(output_path, 'wb') as f:
                f.write(response.content)
            
            # Since you're using a world file, ensure it's called after a successful save
            self.write_world_file(output_path, bbox)

        except Exception as e:
            print(f"❌ Failed to fetch KY Ortho: {e}")


    def write_world_file(self, image_path, bbox):
        """Creates a .pgw file for georeferencing imagery."""
        # Check if file actually exists before trying to open it with PIL
        if not os.path.exists(image_path):
            print(f"⚠️ Cannot create world file: {image_path} does not exist.")
            return

        try:
            with Image.open(image_path) as img:
                width, height = img.size

            lat_range = bbox[2] - bbox[0]
            lon_range = bbox[3] - bbox[1]

            pixel_x = lon_range / width
            pixel_y = -(lat_range / height)

            base, ext = os.path.splitext(image_path)
            # Standardizing .pgw extension
            world_file = base + ".pgw"

            with open(world_file, "w") as f:
                f.write(f"{pixel_x:.12f}\n0.000000000000\n0.000000000000\n"
                        f"{pixel_y:.12f}\n{bbox[1]:.12f}\n{bbox[2]:.12f}")

            print(f"🌍 Georeference created: {world_file}")
        except Exception as e:
            print(f"❌ Error creating world file: {e}")


# --------------------------------------------------------------
# --- CLASS A6. OpenTopographyHarvester                      ---
# ---     OpenTopographyHarvester.__init__(self, output_dir) ---
# ---     OpenTopographyHarvester.fetch_dem(self, bbox)      ---
# --------------------------------------------------------------
class OpenTopographyHarvester:
    def __init__(self, output_dir, api_key):
        self.output_dir = output_dir
        self.api_key = api_key

    def fetch_dem(self, bbox):
        if not self.api_key:
            print("⚠️  No OpenTopography API key provided. Defaulting to SRTM 30m...")
            dataset = "SRTMGL1" # Global 30m
        else:
            print("🔓 API Key detected. Requesting high-res global data...")
            dataset = "OT_LiDAR" # Or specific regional sets

        # OpenTopography API Request logic here...


# -----------------------------------------------------------------------
# --- CLASS A8. OSMSerice                                             ---
# ---     OSMServices.__init__(self, styles_file="styles.json")       ---
# ---     OSMServices._load_style(self)                               ---
# ---     OSMServices._get_filters(self)                              ---
# ---     OSMServices.build_query(self, bbox)                         ---
# ---     OSMServices.download(self, bbox, output_path)               ---
# ---     OSMServices._fetch_with_retries(self, query, max_retries=5) ---
# -----------------------------------------------------------------------
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
            print(f"❌ Error: Configuration file '{filename}' not found.")
            sys.exit(1)
        except json.JSONDecodeError:
            print(f"❌ Error: Failed to decode JSON from '{filename}'.")
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
            print(f"💾 OSM data (with metadata) saved to: {output_path}")


    def _fetch_with_retries(self, query, max_retries=5):
        """Proven fetch logic with exponential backoff."""
        for attempt in range(max_retries):
            try:
                response = requests.post(self.OVERPASS_URL, data={'data': query}, timeout=200)
                if response.status_code == 200:
                    return response.text
                elif response.status_code in [429, 504]:
                    delay = 2 ** attempt
                    print(f"⏳ Rate limit/Timeout ({response.status_code}). Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"❌ HTTP Error {response.status_code}")
                    return None
            except requests.RequestException as e:
                print(f"❌ Connection error: {e}")
                return None
        return None


# -------------------------
# --- Main FUNCTION M1. ---
# -------------------------
def main():
    parser = argparse.ArgumentParser(description="OPCDMapfactory: High-fidelity course asset generator.")

    # Positional/Required
    parser.add_argument("-lat", type=float, required=True, help="Center latitude")
    parser.add_argument("-lon", type=float, required=True, help="Center longitude")

    # Paths & Settings
    parser.add_argument("--style", "--styles" , "-s", default="styles.json", help="A styles.json is required to use the --download_osm feature")
    parser.add_argument("--output_folder", "-o", default="~/Projects/inprogress", help="Project output folder")

    # Elevation Sources
    parser.add_argument("--ky_dem", action="store_true", help="LiDAR from KyFromAbove (Phase 3/2)")
    parser.add_argument("--usgs_dem", action="store_true", help="LiDAR from USGS The National Map (1m or 1/3 arc-second)")
    parser.add_argument("--ot_dem", action="store_true", help="Global DEM via OpenTopography Public API")
    parser.add_argument("--ot_key", type=str, default=None, help="OpenTopography API Key")

    # Imagery Sources
    parser.add_argument("--google_sat", action="store_true", help="Google Satellite imagery")
    parser.add_argument("--bing_sat", action="store_true", help="Bing Satellite imagery")
    parser.add_argument("--ky_ortho", action="store_true", help="KY-specific 3-inch/6-inch Ortho")

    # Download maps and set scales
    parser.add_argument("--download_osm", action="store_true", help="Download OpenStreetMap data")
    parser.add_argument("--inner_mwxh", type=float, default=2000.0, help="Inner Square size (meters)")
    parser.add_argument("--outer_mwxh", type=float, default=4000.0, help="Outer Square size (meters)")

    # CLI Flags for OPCDMapfactory.py to rebuild everything.
    parser.add_argument("--force_ortho", action="store_true", help="Re-generate inner and outer ortho imagery")
    parser.add_argument("--force_obj", action="store_true", help="Re-generate 3D meshes")
    parser.add_argument("--force_hillshade", action="store_true", help="Re-render hillshade PNG")
    parser.add_argument("--force_heightmap", action="store_true", help="Re-calculate 16-bit heightmap")
    args = parser.parse_args()
    styles = {}

    # 1. Initialize Folders
    outpath = os.path.abspath(os.path.expanduser(args.output_folder))
    dem_dir = os.path.join(outpath, "DEM")
    imagery_dir = os.path.join(outpath, "Imagery")

    for d in [outpath, dem_dir, imagery_dir]:
        os.makedirs(d, exist_ok=True)

    print(f"📂 Project Workspace Initialized: {outpath}")

    # 2. Setup Services
    factory_coords = CoordinateManager(args.lat, args.lon, args.inner_mwxh, args.outer_mwxh)
    inner_bbox = factory_coords.get_bbox(factory_coords.inner_m)
    outer_bbox = factory_coords.get_bbox(factory_coords.outer_m)


    # Pre-calculate zooms so all imagery services can use them
    imagery_tool = ImageryService()
    inner_zoom = imagery_tool.get_safe_zoom(inner_bbox)
    outer_zoom = imagery_tool.get_safe_zoom(outer_bbox)

    print(f"📍 Center: {args.lat}, {args.lon}")
    print(f"🗺️  Zoom levels: Inner {inner_zoom}, Outer {outer_zoom}")
    print(f"    Inner Box (2000m): {inner_bbox}")
    print(f"    Outer Box (4000m): {outer_bbox}")
    print(f"  Assets will be saved to: {outpath}")

    # 3. Elevation Harvesting
    dem_files = [os.path.join(dem_dir, f) for f in os.listdir(dem_dir) if f.endswith('.tif')]

    if args.ky_dem:
        if not dem_files or args.force_dem:
            harvester = DataHarvester(dem_dir)
            dem_files, _ = harvester.fetch_ky_assets(outer_bbox)

    if args.usgs_dem:
        usgs = USGSHarvester(dem_dir)
        dem_files = usgs.fetch_dem(outer_bbox)

    # 4. Terrain Processing
    # Check if the user actually requested any terrain-based assets
    terrain_requested = args.force_obj or args.force_hillshade or args.force_heightmap
    if terrain_requested:
        # Check if we have the raw ingredients (DEM files)
        if not dem_files:
            print("❌ ERROR: Terrain assets requested, but no .dem/.tif files found in directory.")
            print("   Please check your path or run with --download_osm only.")
        else:
            terrain = TerrainService(dem_dir)
            print(f"⛰  Processing Terrain for 'The Vet'...")

            # --- INNER BLOCK ---
            inner_elev, _ = terrain.process_files(dem_files, inner_bbox)

            if args.force_obj:
                terrain.export_obj(inner_elev, os.path.join(outpath, "inner_terrain.obj"),
                                   anchor_bbox=inner_bbox, scale_factor=KY_PHASE2_Z_SCALE)

            if args.force_hillshade:
                terrain.make_hillshade(inner_elev, os.path.join(outpath, "inner_hillshade.png"), inner_bbox)

            if args.force_heightmap:
                terrain.make_heightmap(inner_elev, os.path.join(outpath, "inner_heightmap.png"),
                                       inner_bbox, sigma=1.0)

            # --- OUTER BLOCK ---
            # We usually only build outer terrain if explicitly requested or for a full build
            outer_elev, _ = terrain.process_files(dem_files, outer_bbox)

            if args.force_obj:
                terrain.export_obj(outer_elev, os.path.join(outpath, "outer_terrain.obj"),
                                   anchor_bbox=outer_bbox, scale_factor=KY_PHASE2_Z_SCALE)

            if args.force_heightmap:
                terrain.make_heightmap(outer_elev, os.path.join(outpath, "outer_heightmap.png"),
                                       outer_bbox, sigma=2.0)
    else:
        print("⏭  Skipping Terrain Processing (No terrain flags set).")

    # 5. Imagery Logic (Selective)
    if args.ky_ortho:
        inner_ortho_path = os.path.join(imagery_dir, "inner_ky_ortho.png")
        outer_ortho_path = os.path.join(imagery_dir, "outer_ky_ortho.png")
        if os.path.exists(inner_ortho_path) and not args.force_ortho:
            print(f"✅ Found existing {inner_ortho_path}.")
        else:
            kh = DataHarvester(dem_dir)

            # Ky_Ortho INNER (2000m) 6in res ---
            print("📸 Fetching High-Res Inner KY Ortho (The Fairways)...")
            kh.fetch_ky_ortho(inner_bbox, inner_ortho_path, resolution=0.15) # ~6 inch

            # Write world file for alignment
            kh.write_world_file(inner_ortho_path, inner_bbox)


            # ky_OUTER (4000m) 1ft res  ---
            print("📸 Fetching Outer KY Ortho (The Neighborhood)...")
            outer_ortho_path = os.path.join(outpath, "outer_ky_ortho.png")
            # We can drop resolution slightly for the outer to keep file size sane
            kh.fetch_ky_ortho(outer_bbox, outer_ortho_path, resolution=0.30) # ~1 foot
            # Write world file for alignment
            kh.write_world_file(outer_ortho_path, outer_bbox)

    if args.google_sat:
        print("🛰️  Fetching Google Satellite...")
        google = ImageryService(provider="Google")
        google.fetch_and_stitch(inner_bbox, zoom=inner_zoom, output_path=os.path.join(outpath, "inner_google.png"))
        google.fetch_and_stitch(outer_bbox, zoom=outer_zoom, output_path=os.path.join(outpath, "outer_google.png"))

    if args.bing_sat:
        print("🛰️  Fetching Bing Satellite...")
        bing = ImageryService(provider="Bing")
        bing.fetch_and_stitch(inner_bbox, zoom=inner_zoom, output_path=os.path.join(outpath, "inner_bing.png"))
        bing.fetch_and_stitch(inner_bbox, zoom=outer_zoom, output_path=os.path.join(outpath, "outer_bing.png"))


    # 6. OpenStreetMap (Overpass API).
    if args.download_osm:
        styles = {}
        if not os.path.exists(args.style):
            print(f"\n X  ERROR: OSM Download requested, but '{args.style}' was not found.")
            print(f" -  Please provide a valid style file using --style or place 'styles.json' in the script directory.")
            sys.exit(1)

        osm = OSMService(styles_file = args.style)
        osm.download(
            bbox=inner_bbox,
            output_path=os.path.join(outpath, "inner_map.osm"),
            center_lat=args.lat,
            center_lon=args.lon,
            inner_m=args.inner_mwxh,
            outer_m=args.outer_mwxh
        )

        print(f"\n✅ All selected assets for 'The Vet' generated in: {outpath}")


if __name__ == "__main__":
    main()


