#!/usr/bin/env python3

import xml.etree.ElementTree as ET
import rasterio
import numpy as np
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading

class SVGWaterRefiner:
    def __init__(self, root):
        self.root = root
        self.root.title("Water Edge Refiner - The Seneca Edition")
        self.root.geometry("700x550")
        self.root.configure(bg="#2b2b2b")
        
        # UI Elements (Same as your provided setup)
        main_frame = tk.Frame(root, bg="#2b2b2b", padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.svg_path = self.create_file_row(main_frame, "Input SVG File:", 0)
        self.dem_path = self.create_file_row(main_frame, "DEM TIF (2ft):", 1)
        
        coord_frame = tk.LabelFrame(main_frame, text="Course Anchor (Center)", bg="#2b2b2b", fg="white", pady=10)
        coord_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=10)
        
        tk.Label(coord_frame, text="Lat:", bg="#2b2b2b", fg="white").pack(side=tk.LEFT, padx=5)
        self.lat_entry = tk.Entry(coord_frame, width=12); self.lat_entry.pack(side=tk.LEFT, padx=5)
        self.lat_entry.insert(0, "38.2300") 
        
        tk.Label(coord_frame, text="Lon:", bg="#2b2b2b", fg="white").pack(side=tk.LEFT, padx=5)
        self.lon_entry = tk.Entry(coord_frame, width=12); self.lon_entry.pack(side=tk.LEFT, padx=5)
        self.lon_entry.insert(0, "-85.6880")

        tk.Label(main_frame, text="Output SVG:", bg="#2b2b2b", fg="white").grid(row=3, column=0, sticky="w")
        self.out_name = tk.Entry(main_frame, width=50)
        self.out_name.insert(0, "water_refined_map.svg")
        self.out_name.grid(row=3, column=1, pady=10)

        self.progress = ttk.Progressbar(main_frame, length=500, mode='determinate')
        self.progress.grid(row=4, column=0, columnspan=3, pady=10)
        
        self.log = tk.Text(main_frame, height=10, width=80, bg="black", fg="#00ff00", font=("Courier", 9))
        self.log.grid(row=5, column=0, columnspan=3, pady=10)

        self.run_btn = tk.Button(main_frame, text="🚀 RUN DENSITY SNAP", bg="#27ae60", fg="white", 
                                 font=("Arial", 12, "bold"), command=self.start_thread)
        self.run_btn.grid(row=6, column=0, columnspan=3, pady=10, sticky="ew")

    def create_file_row(self, master, label, row):
        tk.Label(master, text=label, bg="#2b2b2b", fg="white").grid(row=row, column=0, sticky="w")
        entry = tk.Entry(master, width=50)
        entry.grid(row=row, column=1, padx=5, pady=5)
        tk.Button(master, text="Browse", command=lambda: self.browse(entry)).grid(row=row, column=2)
        return entry

    def browse(self, entry):
        f = filedialog.askopenfilename(filetypes=[("Files", "*.svg *.tif *.tiff")])
        if f: entry.delete(0, tk.END); entry.insert(0, f)

    def write_log(self, msg):
        self.log.insert(tk.END, f"{msg}\n")
        self.log.see(tk.END)

    def start_thread(self):
        self.run_btn.config(state="disabled")
        threading.Thread(target=self.process, daemon=True).start()

    # --- THE ENGINE ---

    def process(self):
        svg_in, dem_in, out_file = self.svg_path.get(), self.dem_path.get(), self.out_name.get()
        
        try:
            with rasterio.open(dem_in) as dataset:
                dem_data = dataset.read(1)
                self.write_log(f"--> DEM Loaded. Size: {dem_data.shape}")

                ET.register_namespace("", "http://www.w3.org/2000/svg")
                tree = ET.parse(svg_in)
                root = tree.getroot()
                
                target_keys = ['water', 'stream', 'creek', 'river', 'hazard', 'lake']
                paths = root.findall(".//{http://www.w3.org/2000/svg}path")
                
                self.progress['maximum'] = len(paths)
                count = 0

                for path in paths:
                    p_id = (path.get('id') or "").lower()
                    p_cls = (path.get('class') or "").lower()
                    
                    if any(k in p_id or k in p_cls for k in target_keys):
                        d_str = path.get('d')
                        if d_str:
                            refined_d = self.densify_and_snap_relaxed(d_str, dem_data, dataset)
                            path.set('d', refined_d)
                            count += 1
                    
                    self.progress['value'] += 1
                    self.root.update_idletasks()

                tree.write(out_file)
                self.write_log(f"✅ SUCCESS: Refined {count} water elements.")
                messagebox.showinfo("Done", f"Creek geometry densified and snapped.")

        except Exception as e:
            self.write_log(f"❌ ERROR: {str(e)}")
        finally:
            self.run_btn.config(state="normal")

    def find_local_minimum(self, px, py, dem_data):
        window_size = 12 # slightly larger window for densified points
        rows, cols = dem_data.shape
        best_z, best_pos = float('inf'), (px, py)

        for r in range(int(py - window_size), int(py + window_size + 1)):
            for c in range(int(px - window_size), int(px + window_size + 1)):
                if 0 <= r < rows and 0 <= c < cols:
                    z = dem_data[r, c]
                    if z < best_z and z > -900:
                        best_z, best_pos = z, (c, r)
        return best_pos

    def svg_to_dem_pixel(self, x_svg, y_svg, dataset):
        # Using the anchor logic: SVG 0,0 is at the center of the TIF
        # Assuming 2000m TIF at 2ft res is roughly 3280x3280 pixels
        center_x, center_y = dataset.width // 2, dataset.height // 2
        # Convert meters to pixels (1 meter is approx 1.64 pixels at 2ft res)
        px = center_x + (x_svg * 1.64042)
        py = center_y + (y_svg * 1.64042)
        return px, py

    def dem_pixel_to_svg(self, px, py, dataset):
        center_x, center_y = dataset.width // 2, dataset.height // 2
        x_svg = (px - center_x) / 1.64042
        y_svg = (py - center_y) / 1.64042
        return x_svg, y_svg

    def densify_and_snap_relaxed(self, path_d_string, dem_data, dataset):
        """
        Fixes vertex stacking and implements 'flood' style snapping.
        """
        raw_coords = re.findall(r'([-+]?\d*\.\d+|[-+]?\d+)', path_d_string)
        points = np.array([float(x) for x in raw_coords]).reshape(-1, 2)

        if len(points) < 2:
            return path_d_string

        # --- STEP 1: De-clutter (The Fix for 'Stacked' Vertices) ---
        # If vertices are closer than 5 pixels, we merge them to prevent jitter.
        clean_points = [points[0]]
        for i in range(1, len(points)):
            if np.linalg.norm(points[i] - clean_points[-1]) > 5.0: 
                clean_points.append(points[i])
        points = np.array(clean_points)

        # --- STEP 2: Relaxed Sampling ---
        refined_points = []
        step_size = 12.0 # Relaxed step (approx 24ft) for a smoother look

        for i in range(len(points) - 1):
            p1, p2 = points[i], points[i+1]
            dist = np.linalg.norm(p2 - p1)
            
            # Snap the current point using the 'Basin' logic
            px, py = self.svg_to_dem_pixel(p1[0], p1[1], dataset)
            new_px, new_py = self.find_relaxed_minimum(px, py, dem_data)
            refined_points.append(self.dem_pixel_to_svg(new_px, new_py, dataset))

            # Only add points if there's a big gap
            if dist > step_size:
                num_steps = int(dist // step_size)
                for s in range(1, num_steps):
                    interp_p = p1 + (s / num_steps) * (p2 - p1)
                    px, py = self.svg_to_dem_pixel(interp_p[0], interp_p[1], dataset)
                    new_px, new_py = self.find_relaxed_minimum(px, py, dem_data)
                    refined_points.append(self.dem_pixel_to_svg(new_px, new_py, dataset))

        # Add the final point
        last_px, last_py = self.svg_to_dem_pixel(points[-1][0], points[-1][1], dataset)
        fin_px, fin_py = self.find_relaxed_minimum(last_px, last_py, dem_data)
        refined_points.append(self.dem_pixel_to_svg(fin_px, fin_py, dataset))

        return "M " + " L ".join([f"{p[0]:.3f},{p[1]:.3f}" for p in refined_points])

    def find_relaxed_minimum(self, px, py, dem_data):
        """
        Finds the center of the local low basin (10th percentile).
        This prevents the line from snapping to 'holes' or noise.
        """
        window = 5 
        rows, cols = dem_data.shape
        r_s, r_e = max(0, int(py-window)), min(rows, int(py+window+1))
        c_s, c_e = max(0, int(px-window)), min(cols, int(px+window+1))
        
        crop = dem_data[r_s:r_e, c_s:c_e]
        if crop.size == 0: return px, py
        
        # Take the lowest 10% of elevations in this 5x5 area
        thresh = np.percentile(crop, 10)
        low_spots = np.argwhere(crop <= thresh)
        
        if len(low_spots) > 0:
            avg_y, avg_x = np.mean(low_spots, axis=0)
            return c_s + avg_x, r_s + avg_y
        return px, py

    
if __name__ == "__main__":
    root = tk.Tk()
    app = SVGWaterRefiner(root)
    root.mainloop()
    
