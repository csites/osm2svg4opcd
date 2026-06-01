import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import os
import threading

class KyMapFactoryGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Kymapfactory4opcd - Pipeline GUI")
        self.root.geometry("700x850")

        # --- Main Layout ---
        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Coordinate Section
        coord_frame = ttk.LabelFrame(main_frame, text="📍 Project Location", padding="10")
        coord_frame.pack(fill=tk.X, pady=5)

        ttk.Label(coord_frame, text="Latitude:").grid(row=0, column=0, sticky=tk.W)
        self.lat_entry = ttk.Entry(coord_frame)
        self.lat_entry.insert(0, "38.2378") # Seneca Default
        self.lat_entry.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(coord_frame, text="Longitude:").grid(row=1, column=0, sticky=tk.W)
        self.lon_entry = ttk.Entry(coord_frame)
        self.lon_entry.insert(0, "-85.6882")
        self.lon_entry.grid(row=1, column=1, padx=5, pady=2)
        self.auto_center = tk.BooleanVar(value=True) # Default to True is safer for golf course
        ttk.Checkbutton(coord_frame, text="Adjust Lat/Lon to course mid-center", 
                variable=self.auto_center).grid(row=2, column=0, columnspan=2, sticky=tk.W)
        
        # 2. Paths Section
        path_frame = ttk.LabelFrame(main_frame, text="📂 Paths & Environment", padding="10")
        path_frame.pack(fill=tk.X, pady=5)

        self.out_path = tk.StringVar(value="~/Projects/inprogress")
        ttk.Label(path_frame, text="Output Folder:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(path_frame, textvariable=self.out_path, width=40).grid(row=0, column=1, padx=5)
        ttk.Button(path_frame, text="Browse", command=self.browse_folder).grid(row=0, column=2)

        # 3. Harvesting Options
        harvest_frame = ttk.LabelFrame(main_frame, text="🌊 Raw Data Harvesting", padding="10")
        harvest_frame.pack(fill=tk.X, pady=5)

        self.ky_dem = tk.BooleanVar()
        self.ky_ortho = tk.BooleanVar()
        self.google_sat = tk.BooleanVar()
        self.bing_sat = tk.BooleanVar()
        self.download_osm = tk.BooleanVar()

        ttk.Checkbutton(harvest_frame, text="Harvest KY LiDAR (DEM)", variable=self.ky_dem).grid(row=0, column=0, sticky=tk.W)
        ttk.Checkbutton(harvest_frame, text="Harvest KY Ortho (Imagery)", variable=self.ky_ortho).grid(row=1, column=0, sticky=tk.W)
        ttk.Checkbutton(harvest_frame, text="Harvest Google Satellite", variable=self.google_sat).grid(row=0, column=1, sticky=tk.W)
        ttk.Checkbutton(harvest_frame, text="Harvest Bing Satellite", variable=self.bing_sat).grid(row=1, column=1, sticky=tk.W)
        ttk.Checkbutton(harvest_frame, text="Download inner_map.osm", variable=self.download_osm).grid(row=2, column=0, sticky=tk.W)

        # 4. Processing Options
        process_frame = ttk.LabelFrame(main_frame, text="🏗️ Processing & Build", padding="10")
        process_frame.pack(fill=tk.X, pady=5)

        self.build_obj = tk.BooleanVar()
        self.build_heightmap = tk.BooleanVar()
        self.build_hillshade = tk.BooleanVar()
        self.osm_to_svg = tk.BooleanVar()
        self.water_edge = tk.BooleanVar()

        ttk.Checkbutton(process_frame, text="Build Blender OBJ", variable=self.build_obj).grid(row=0, column=0, sticky=tk.W)
        ttk.Checkbutton(process_frame, text="Build 16-bit Heightmap", variable=self.build_heightmap).grid(row=1, column=0, sticky=tk.W)
        ttk.Checkbutton(process_frame, text="Build Hillshade PNG", variable=self.build_hillshade).grid(row=2, column=0, sticky=tk.W)
        ttk.Checkbutton(process_frame, text="Run OSM to SVG (v9)", variable=self.osm_to_svg).grid(row=0, column=1, sticky=tk.W)
        ttk.Checkbutton(process_frame, text="Run Water Edge Adjuster", variable=self.water_edge).grid(row=1, column=1, sticky=tk.W)

        # 5. Log Console
        log_frame = ttk.LabelFrame(main_frame, text="📝 Console Output", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.log_text = tk.Text(log_frame, height=12, bg="black", fg="lightgreen", font=("Courier", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 6. Action Button
        self.run_btn = ttk.Button(main_frame, text="🚀 START PIPELINE", command=self.start_thread)
        self.run_btn.pack(pady=10, fill=tk.X)

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.out_path.set(folder)

    def log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def start_thread(self):
        # Run the process in a separate thread to keep the GUI responsive
        t = threading.Thread(target=self.run_pipeline)
        t.start()

    def run_pipeline(self):
        self.run_btn.config(state=tk.DISABLED)
        self.log("--> Initializing Pipeline...")

        # Construct Command Arguments
        cmd = ["python3", "Kymapfactory4opcd.py", 
               "-lat", self.lat_entry.get(), 
               "-lon", self.lon_entry.get(),
               "--output_folder", self.out_path.get()]
        
        if self.auto_center.get(): cmd.append("--auto_center")

        if self.ky_dem.get(): cmd.append("--ky_dem")
        if self.ky_ortho.get(): cmd.append("--ky_ortho")
        if self.google_sat.get(): cmd.append("--google_sat")
        if self.bing_sat.get(): cmd.append("--bing_sat")
        if self.download_osm.get(): cmd.append("--download_osm")
        
        if self.build_obj.get(): cmd.append("--rebuild_obj")
        if self.build_heightmap.get(): cmd.append("--rebuild_heightmap")
        if self.build_hillshade.get(): cmd.append("--rebuild_hillshade")
        if self.osm_to_svg.get(): cmd.append("--osm_to_svg")
        if self.water_edge.get(): cmd.append("--svg_water_edge")

        try:
            # Execute and capture output in real-time
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                self.log(line.strip())
            process.wait()
            self.log(">>> Pipeline Task Completed.")
        except Exception as e:
            self.log(f"xxx ERROR: {str(e)}")
        
        self.run_btn.config(state=tk.NORMAL)

if __name__ == "__main__":
    root = tk.Tk()
    app = KyMapFactoryGUI(root)
    root.mainloop()
    
