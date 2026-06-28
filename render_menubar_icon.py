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
Books + network menu-bar glyph — echoes the app icon, simplified for ~18px.
Template image: solid black on transparent. We try a couple of simplification
levels so we can pick what survives at menu-bar scale.
"""
from PIL import Image, ImageDraw

SS = 8
TARGET = 36
W = TARGET * SS
BLACK = (0, 0, 0, 255)
def sw(v): return max(1, int(v * SS))
def P(x): return x * SS

def render_books_network(node_r=2.4, n_nodes=3, book_gap=1.6, books_only=False):
    """
    A compact stack of 3 books with a small network above.
    node_r: node radius; n_nodes: how many nodes in the network (3 or 5).
    """
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ── Stack of 3 books (rounded bars), bottom region of the glyph ──
    # Each book is a filled rounded rect with a thin gap between them.
    book_h = 3.6
    book_w_top, book_w_mid, book_w_bot = 17, 20, 18
    cx = 18
    y_bot = 30.5
    def book(cx, w, ytop, h):
        x0, x1 = cx - w/2, cx + w/2
        d.rounded_rectangle([P(x0), P(ytop), P(x1), P(ytop+h)],
                            radius=P(1.0), fill=BLACK)
    # bottom, middle, top (slightly offset widths like the app icon)
    book(cx+0.5, book_w_bot, y_bot - book_h, book_h)
    book(cx-1.0, book_w_mid, y_bot - 2*book_h - book_gap, book_h)
    book(cx+1.0, book_w_top, y_bot - 3*book_h - 2*book_gap, book_h)

    if books_only:
        return img.resize((TARGET, TARGET), Image.LANCZOS)

    # ── Network above the books ──
    # Apex node centered, with branch nodes; thin connecting lines.
    apex = (18, 4.2)
    if n_nodes == 3:
        branches = [(10, 10.5), (26, 10.5)]
    else:
        branches = [(8.5, 11), (18, 8.2), (27.5, 11), (13, 14.5), (23, 14.5)]
        branches = branches[:n_nodes-1]
    # connect apex to each branch
    for bx, by in branches:
        d.line([P(apex[0]), P(apex[1]), P(bx), P(by)], fill=BLACK, width=sw(1.1))
    # connect the two inner branches to the book stack (a stem down)
    stem_top = (18, 8.2 if n_nodes==5 else 10.5)
    d.line([P(apex[0]), P(apex[1]), P(18), P(13.5)], fill=BLACK, width=sw(1.1))
    # nodes as filled dots
    for (nx, ny) in [apex] + branches:
        d.ellipse([P(nx-node_r), P(ny-node_r), P(nx+node_r), P(ny+node_r)], fill=BLACK)
    return img.resize((TARGET, TARGET), Image.LANCZOS)

# Variants to compare
v1 = render_books_network(node_r=2.4, n_nodes=3)   # books + 3-node network
v2 = render_books_network(node_r=2.2, n_nodes=5)   # books + 5-node network (busier)
v3 = render_books_network(books_only=True)         # books only (simplest)

for g, name in [(v1,"v1_3node"), (v2,"v2_5node"), (v3,"v3_booksonly")]:
    g.save(f"/tmp/books_{name}.png")

# Preview on simulated bars
def tint(glyph, color):
    out = Image.new("RGBA", glyph.size, (0,0,0,0))
    pi, po = glyph.load(), out.load()
    for y in range(glyph.height):
        for x in range(glyph.width):
            a = pi[x,y][3]
            if a>0: po[x,y]=(color[0],color[1],color[2],a)
    return out

def bar(glyph, bg, fg):
    BARH, BARW = 44, 300
    b = Image.new("RGBA",(BARW,BARH),bg)
    g = tint(glyph,fg); y=(BARH-g.height)//2
    b.alpha_composite(g,(BARW-150,y))
    d=ImageDraw.Draw(b)
    d.rounded_rectangle([BARW-60,16,BARW-30,28],radius=3,outline=fg,width=2)
    d.rectangle([BARW-30,19,BARW-28,25],fill=fg)
    d.text((BARW-110,15),"9:41",fill=fg)
    return b

sheet = Image.new("RGBA",(620,360),(245,245,245,255))
d=ImageDraw.Draw(sheet)
rows = [("v1 — books + 3-node network", v1, 40),
        ("v2 — books + 5-node network", v2, 150),
        ("v3 — books only", v3, 260)]
for label, g, y in rows:
    d.text((24,y-22), label, fill=(20,20,20))
    sheet.alpha_composite(bar(g,(236,236,238,255),(40,40,40)),(24,y))
    sheet.alpha_composite(bar(g,(40,42,46,255),(235,235,235)),(24,y+50))
    big = g.resize((90,90), Image.NEAREST)
    sheet.alpha_composite(big,(470,y-4))
sheet.convert("RGB").save("/tmp/books_glyph_compare.png")
print("✓ /tmp/books_glyph_compare.png")
