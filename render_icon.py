"""MedSearch icon — sage + caramel, scaled up to fill more of the frame."""
from PIL import Image, ImageDraw
import math as m

SIZE = 512
SS = 4
W = SIZE * SS
def s(v): return int(v*SS)

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

# ── Caramel / brown palette ──
caramel    = (181, 124, 71, 255)
caramel_d  = (146, 97, 52, 255)
brown      = (120, 80, 50, 255)
brown_d    = (94, 62, 38, 255)
tan        = (201, 154, 105, 255)
tan_d      = (168, 124, 80, 255)
cream      = (240, 228, 210, 255)
node_dark  = (74, 50, 32, 255)

# ════════════════════════════════════════════════════
#  STACK OF BOOKS — scaled up, fills more width & height
# ════════════════════════════════════════════════════
books = [
    (s(78),  s(372), s(434), s(432), caramel, caramel_d),
    (s(64),  s(312), s(420), s(372), brown,   brown_d),
    (s(88),  s(252), s(448), s(312), tan,     tan_d),
]
for x0,y0,x1,y1,body,spine in books:
    d.rounded_rectangle([x0,y0,x1,y1], radius=s(8), fill=body)
    d.rounded_rectangle([x0,y0,x0+s(30),y1], radius=s(8), fill=spine)
    d.rectangle([x1-s(19),y0+s(8),x1-s(6),y1-s(8)], fill=cream)
    d.line([(x0+s(36),y0+s(7)),(x1-s(24),y0+s(7))], fill=(255,255,255,40), width=s(4))

# ════════════════════════════════════════════════════
#  NEURAL NETWORK — larger spread, fills upper frame
# ════════════════════════════════════════════════════
top_y = s(252)
origin = (s(266), top_y)

nodes = [
    (s(112), s(120)), (s(266), s(62)),  (s(420), s(120)),
    (s(180), s(178)), (s(352), s(178)),
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

img = img.resize((SIZE, SIZE), Image.LANCZOS)
img.save("icon_preview.png")
print("✓ Scaled-up version rendered")
