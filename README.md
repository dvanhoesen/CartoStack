# CartoStack

CartoStack helps to build the base layers and structure of geoscience maps and figures over and over again without the overhead of redrawing all components every time. 

Weather and climate maps are often produced on a schedule. The coastlines, borders, cities, logo, and legend never change. Frequently on the data, title, and/or timestamp change. Redrawing all of it with Matplotlib and Cartopy every time is slow. CartoStack draws the map once, saves it as a layered `.cstack` file, and then builds each new map by swapping in only the new data and stacking the layers into a PNG.

The stack can be imagined as transparent sheets where the basemap is drawn ahead of time, and on every run only the data sheet and title are replaced. Producing a map needs only **NumPy and Pillow**, so it is quick to start, lightweight, and does not require Cartopy, shapefiles, or Natural Earth data repeatedly.

![Time to make one map in a fresh Python process: Matplotlib/Cartopy 16.1 s, most of it drawing the static map, versus CartoStack 0.13 s](docs/images/cold_start.png)

Making one map in a fresh Python process (as a scheduled job does), starting from data on disk, is quick. In the figure above the synthetic 1200x800 pixel temperature map took 0.13 s with CartoStack instead of 16 s with Matplotlib. The CartoStack time does not include the one-time step of building the `.cstack` file from the Matplotlib/Cartopy figure (about 18 s for this map, roughly one ordinary render). That step is done once per map layout and reused for every later map. Times listed in the figure are the average of 10 cold start runs on an Apple M3 Pro.

## Install

```bash
pip install cartostack            # make maps from .cstack files (NumPy + Pillow)
pip install "cartostack[build]"   # also create .cstack files (adds Matplotlib + Cartopy)
```

Requires Python 3.12 or newer.

## Usage

**1. Create the file once** from your existing Matplotlib/Cartopy figure:

```python
import math
from cartostack.build import SceneBuilder

fig, ax = draw_my_map()   # your existing Matplotlib/Cartopy code

b = SceneBuilder(fig, ax, dpi=150)
b.add_static("below", order=0, background=True, zorder=(-math.inf, 2))
b.add_grid_slot("temperature", order=10, lon=lon, lat=lat, cmap="coolwarm",
                vmin=-5, vmax=30, alpha=0.8, artists=[mesh])
b.add_static("above", order=20, zorder=(2, math.inf), artists=[title])
b.add_text_slot("subtitle", subtitle_artist, order=30)
b.save("temperature.cstack")
```

**2. Make a new map** whenever new data arrives:

```python
from cartostack import Scene

with Scene.load("temperature.cstack") as scene:
    scene.replace_grid("temperature", values)       # new 2-D array
    scene.text["subtitle"] = "Valid 2026-10-09 18:00 UTC"
    scene.save_png("temperature_18z.png")
```

In addition to gridded data, a map can hold replaceable polygons (such as forecast contours), text, and colorbars that update when the color range changes. Complete examples are in
[`examples/`](examples/README.md).

## Documentation

- [API reference](docs/api.md)
- [File format specification](docs/format.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT
