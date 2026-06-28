# Copyright 2026 Riccardo Nevoso
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
MedSearch Menu Bar — bundle icon.
Takes the main app icon (books + neural network, sage+caramel) and adds a thin
menu-bar strip across the top to signal it's the menu-bar companion.
Renders a full .iconset (all sizes) and assembles an .icns.
"""
from PIL import Image, ImageDraw
import math as m, os

SIZE = 1024          # render large, downscale for each iconset size
SS = 2
W = SIZE * SS
def s(v): return int(v * SS * (SIZE/512))   # scale from the original 512 design

img = Image.new("RGBA", (W, W), (0,0,0,0))

# ── Sage green background ──
bg = Image.new("RGBA", (W, W), (0,0,0,0))
bgd = ImageDraw.Draw(bg)
for y in range(W):
    t = y / W
    r = int(179 - (179-154)*t)
    g = int(196 - (196-174)*t)
    b = int(168 - (168-140)*t)
    bgd.line([(0,y),(W,y)], fill=(r,g,b,255))
mask = Image.new("L", (W, W), 0)
ImageDraw.Draw(mask).rounded_rectangle([0,0,W,W], radius=s(112), fill=255)
img.paste(bg, (0,0), mask)
d = ImageDraw.Draw(img)

caramel    = (181, 124, 71, 255)
caramel_d  = (146, 97, 52, 255)
brown      = (120, 80, 50, 255)
brown_d    = (94, 62, 38, 255)
tan        = (201, 154, 105, 255)
tan_d      = (168, 124, 80, 255)
cream      = (240, 228, 210, 255)
node_dark  = (74, 50, 32, 255)

# ── Menu-bar strip across the very top ──
# A translucent dark bar hugging the top inside the rounded corners, with a few
# faint "menu items" and a highlighted dot standing for our icon in the bar.
strip_h = s(74)
strip = Image.new("RGBA", (W, W), (0,0,0,0))
sd = ImageDraw.Draw(strip)
sd.rectangle([0, 0, W, strip_h], fill=(28, 30, 34, 210))
# faint menu items on the left
for i, x in enumerate([s(40), s(96), s(150)]):
    sd.rounded_rectangle([x, strip_h//2 - s(7), x+s(34), strip_h//2 + s(7)],
                         radius=s(5), fill=(235,235,235,90))
# our presence on the right: a bright dot (the menu-bar icon)
dot_x = W - s(70)
sd.ellipse([dot_x-s(13), strip_h//2-s(13), dot_x+s(13), strip_h//2+s(13)],
           fill=(245, 240, 230, 255))
# clip the strip to the rounded-rect mask so corners stay rounded
img.paste(strip, (0,0), Image.composite(strip.split()[3], Image.new("L",(W,W),0), mask))

# Recompute draw context after paste
d = ImageDraw.Draw(img)

# ── Shift the artwork down slightly so it doesn't collide with the strip ──
DY = s(40)

# ── Stack of books ──
books = [
    (s(78),  s(372)+DY, s(434), s(432)+DY, caramel, caramel_d),
    (s(64),  s(312)+DY, s(420), s(372)+DY, brown,   brown_d),
    (s(88),  s(252)+DY, s(448), s(312)+DY, tan,     tan_d),
]
for x0,y0,x1,y1,body,spine in books:
    d.rounded_rectangle([x0,y0,x1,y1], radius=s(8), fill=body)
    d.rounded_rectangle([x0,y0,x0+s(30),y1], radius=s(8), fill=spine)
    d.rectangle([x1-s(19),y0+s(8),x1-s(6),y1-s(8)], fill=cream)
    d.line([(x0+s(36),y0+s(7)),(x1-s(24),y0+s(7))], fill=(255,255,255,40), width=s(4))

# ── Neural network ──
top_y = s(252)+DY
origin = (s(266), top_y)
nodes = [
    (s(112), s(120)+DY), (s(266), s(62)+DY),  (s(420), s(120)+DY),
    (s(180), s(178)+DY), (s(352), s(178)+DY),
]
trace_col = (node_dark[0], node_dark[1], node_dark[2], 110)
for nx, ny in nodes:
    d.line([origin, (nx,ny)], fill=trace_col, width=s(6))
d.line([nodes[0], nodes[3]], fill=trace_col, width=s(6))
d.line([nodes[1], nodes[3]], fill=trace_col, width=s(6))
d.line([nodes[1], nodes[4]], fill=trace_col, width=s(6))
d.line([nodes[2], nodes[4]], fill=trace_col, width=s(6))
d.line([nodes[3], nodes[4]], fill=trace_col, width=s(6))
node_cols = [caramel, brown, tan, caramel, brown]
for i,(nx,ny) in enumerate(nodes):
    r = s(21)
    d.ellipse([nx-r,ny-r,nx+r,ny+r], fill=node_cols[i])
    d.ellipse([nx-s(9),ny-s(9),nx+s(9),ny+s(9)], fill=cream)
d.ellipse([origin[0]-s(16),origin[1]-s(16),origin[0]+s(16),origin[1]+s(16)], fill=node_dark)

final = img.resize((SIZE, SIZE), Image.LANCZOS)
final.save("/tmp/menubar_app_icon_1024.png")
print("✓ /tmp/menubar_app_icon_1024.png")

# ── Build a full .iconset and convert to .icns ──
os.makedirs("/tmp/MenuBar.iconset", exist_ok=True)
specs = [
    (16,  "icon_16x16.png"),       (32,  "icon_16x16@2x.png"),
    (32,  "icon_32x32.png"),       (64,  "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),     (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),     (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),     (1024,"icon_512x512@2x.png"),
]
for px, name in specs:
    final.resize((px, px), Image.LANCZOS).save(f"/tmp/MenuBar.iconset/{name}")
print("✓ iconset written to /tmp/MenuBar.iconset")
