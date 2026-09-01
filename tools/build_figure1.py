#!/usr/bin/env python3
"""Rebuild Figure 1 (the pipeline overview) as inline SVG from the paper's deck.

Source of truth: slide 1 of Paper/plots/figure1.pptx. Every shape, connector,
text run and picture on the slide is walked in z-order, group transforms are
resolved to absolute slide coordinates (points), and the result is spliced into
docs/index.html as one <svg class="fig1"> with real text. Only the slide's
raster pictures ship as files (cropped, downscaled, under
docs/assets/figures/pipeline/); the logos that are SVG in the deck are inlined
as paths.

Run it after editing the deck:

    python tools/build_figure1.py            # default pptx path below
    python tools/build_figure1.py --pptx /path/to/figure1.pptx

Then re-run tools/check_release_safety.py (the pre-commit hook does this).
The line breaks PowerPoint chose are hard-coded in WRAP and asserted against
the slide text, so a reworded box fails loudly instead of wrapping differently.
Text metrics (0.952 ascent, 1.2 line height, 7.2/3.6pt insets) and the dash and
arrowhead sizes were calibrated against PowerPoint's own PNG export.
"""
import argparse
import math
import re
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree
from PIL import Image

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
SITE = REPO / "docs"
DEFAULT_PPTX = Path("/home/ruiyi/livemacro/Paper/plots/figure1.pptx")
ICON_DIR = "assets/figures/pipeline"     # relative to docs/, also the href prefix

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--pptx", type=Path, default=DEFAULT_PPTX)
ap.add_argument("--site", type=Path, default=SITE, help="docs/ directory to write into")
ap.add_argument("--svg-only", type=Path, default=None,
                help="write the SVG fragment here instead of splicing into index.html")
args = ap.parse_args()
if not args.pptx.exists():
    sys.exit(f"pptx not found: {args.pptx}")
_tmp = tempfile.TemporaryDirectory()
SRC = Path(_tmp.name)
with zipfile.ZipFile(args.pptx) as z:
    z.extractall(SRC)
OUT_ICONS = args.site / ICON_DIR
ICON_HREF = ICON_DIR + "/"

ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'asvg': 'http://schemas.microsoft.com/office/drawing/2016/SVG/main'}
R_EMBED = '{%s}embed' % ns['r']
PT = 12700.0

# ---- theme ---------------------------------------------------------------
SCHEME = {'dk1': '000000', 'lt1': 'FFFFFF', 'dk2': '0E2841', 'lt2': 'E8E8E8',
          'accent1': '156082', 'accent2': 'E97132', 'accent3': '196B24',
          'accent4': '0F9ED5', 'accent5': 'A02B93', 'accent6': '4EA72E'}
SCHEME.update({'tx1': SCHEME['dk1'], 'bg1': SCHEME['lt1'],
               'tx2': SCHEME['dk2'], 'bg2': SCHEME['lt2']})
THEME_LN_W = [1.0, 1.5, 2.0]     # lnStyleLst widths in pt (idx 1..3)
FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif'
LINS = RINS = 7.2                # default text insets (0.1 in)
TINS = BINS = 3.6                # (0.05 in)
ASC = 0.952                      # Helvetica Neue ascent, baseline from line top
LH = 1.2                         # PowerPoint single spacing
ICON_PX_PER_PT = 4               # never upscale; downscale big icons to this

# Line breaks exactly as PowerPoint wrapped them in the exported PNG.
# key: shape name -> list of paragraphs -> list of lines. Paragraphs not
# listed are single-line. Asserted below against the source text.
WRAP = {
    'TextBox 140': {0: ['Market Return', 'Capture Score'],
                    1: ['· Did model-predicted', 'shocks match realized', 'market moves?']},
    'TextBox 141': {0: ['Polymarket', 'Betting Return'],
                    1: ['· Did the model beat', 'the wisdom-of-the-', 'crowd?']},
    'TextBox 145': {1: ['· Where does each', 'model specialize', 'across the macro', 'economy?']},
    'Rectangle 48': {0: ['Bloomberg', 'Consensus'],
                     1: ['· Large professional forecast', 'survey']},
    'Rectangle 32': {1: ['· Statistical', 'benchmark forecast']},
    'TextBox 65':   {0: ['Ground', 'Truth', 'Release']},
}


def srgb_to_lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lin_to_srgb(v):
    v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
    return max(0, min(255, round(v * 255)))


def color_of(el):
    """<a:solidFill>/<a:schemeClr>… -> '#rrggbb' or None."""
    if el is None:
        return None
    for c in el:
        t = etree.QName(c).localname
        if t == 'srgbClr':
            hexv = c.get('val')
        elif t == 'schemeClr':
            hexv = SCHEME[c.get('val')]
        else:
            continue
        rgb = [int(hexv[i:i + 2], 16) for i in (0, 2, 4)]
        for m in c:
            mt = etree.QName(m).localname
            if mt == 'shade':
                f = int(m.get('val')) / 100000
                rgb = [lin_to_srgb(srgb_to_lin(x) * f) for x in rgb]
            elif mt in ('lumMod', 'lumOff', 'tint', 'alpha'):
                pass  # only used on fills that never render here
        return '#%02X%02X%02X' % tuple(rgb)
    return None


def fill_of(spPr):
    for c in spPr:
        t = etree.QName(c).localname
        if t == 'noFill':
            return 'none'
        if t == 'solidFill':
            return color_of(c)
    return None  # not specified -> style


def style_ref(el, tag):
    st = el.find('p:style', ns)
    if st is None:
        return None, None
    ref = st.find('a:' + tag, ns)
    if ref is None:
        return None, None
    idx = ref.get('idx')
    return (int(idx) if idx.isdigit() else idx), color_of(ref)


def resolve_fill(el, spPr):
    f = fill_of(spPr)
    if f is not None:
        return f
    idx, col = style_ref(el, 'fillRef')
    if idx is None or idx == 0:
        return 'none'
    return col


def resolve_line(el, spPr):
    """-> dict(width, color, dash, tail) or None."""
    ln = spPr.find('a:ln', ns)
    idx, scol = style_ref(el, 'lnRef')
    if ln is None:
        if idx is None or idx == 0:
            return None
        return {'w': THEME_LN_W[idx - 1], 'color': scol, 'dash': 'solid', 'tail': None}
    d = {}
    f = fill_of(ln)
    if f == 'none':
        return None
    d['color'] = f if f else scol
    d['w'] = int(ln.get('w')) / PT if ln.get('w') else (THEME_LN_W[idx - 1] if idx else 1.0)
    pd = ln.find('a:prstDash', ns)
    d['dash'] = pd.get('val') if pd is not None else 'solid'
    te = ln.find('a:tailEnd', ns)
    d['tail'] = te.get('type') if te is not None else None
    he = ln.find('a:headEnd', ns)
    assert he is None or he.get('type') in (None, 'none'), 'headEnd not handled'
    if d['color'] is None:
        return None
    return d


# ---- transforms ------------------------------------------------------------
class T:
    def __init__(self, ox, oy, sx, sy, cx, cy):
        self.ox, self.oy, self.sx, self.sy, self.cx, self.cy = ox, oy, sx, sy, cx, cy

    def pt(self, x, y):
        return self.ox + (x - self.cx) * self.sx, self.oy + (y - self.cy) * self.sy


def apply(stack, x, y):
    for t in reversed(stack):
        x, y = t.pt(x, y)
    return x, y


def scale(stack):
    sx = sy = 1.0
    for t in stack:
        sx *= t.sx
        sy *= t.sy
    return sx, sy


def xfrm_of(el):
    x = el.find('a:xfrm', ns)
    if x is None:
        x = el.find('.//a:xfrm', ns)
    off, ext = x.find('a:off', ns), x.find('a:ext', ns)
    assert not x.get('rot') and not x.get('flipH') and not x.get('flipV'), 'rotation/flip not handled'
    d = {'x': int(off.get('x')) / PT, 'y': int(off.get('y')) / PT,
         'w': int(ext.get('cx')) / PT, 'h': int(ext.get('cy')) / PT}
    cho, che = x.find('a:chOff', ns), x.find('a:chExt', ns)
    if cho is not None:
        d['cx'], d['cy'] = int(cho.get('x')) / PT, int(cho.get('y')) / PT
        d['cw'], d['ch'] = int(che.get('cx')) / PT, int(che.get('cy')) / PT
    return d


def f(v):
    s = ('%.2f' % v).rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


# ---- output ---------------------------------------------------------------
out = []
bounds = [math.inf, math.inf, -math.inf, -math.inf]


def grow(x0, y0, x1, y1):
    bounds[0] = min(bounds[0], x0); bounds[1] = min(bounds[1], y0)
    bounds[2] = max(bounds[2], x1); bounds[3] = max(bounds[3], y1)


def dasharray(d, w):
    if d == 'solid':
        return None
    if d == 'dash':          # PowerPoint preset: 4w dash, 3w gap (verified on the PNG)
        return f'{f(4 * w)} {f(3 * w)}'
    raise ValueError(d)


def stroke_attrs(ln):
    if ln is None:
        return 'stroke="none"'
    s = f'stroke="{ln["color"]}" stroke-width="{f(ln["w"])}"'
    da = dasharray(ln['dash'], ln['w'])
    if da:
        s += f' stroke-dasharray="{da}"'
    return s


def arrow_head(x0, y0, x1, y1, ln):
    """Filled triangle at (x1,y1) pointing along the line. PowerPoint 'triangle'
    medium head: length = width = 3 x line width (measured 9pt on the 3pt
    arrows and ~5.3pt on the 1.5pt dashed ones)."""
    w = ln['w']
    L = 3 * w if w >= 2 else 5.5
    W = 3 * w if w >= 2 else 5.0
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    ux, uy = dx / n, dy / n
    bx, by = x1 - ux * L, y1 - uy * L            # base centre
    px, py = -uy * W / 2, ux * W / 2             # half-width perpendicular
    pts = f'{f(x1)} {f(y1)} {f(bx + px)} {f(by + py)} {f(bx - px)} {f(by - py)}'
    return f'<polygon points="{pts}" fill="{ln["color"]}" stroke="none"/>', (bx, by)


# ---- media ----------------------------------------------------------------
rels = {}
for rel in etree.parse(SRC / 'ppt/slides/_rels/slide1.xml.rels').getroot():
    rels[rel.get('Id')] = (SRC / 'ppt/slides' / rel.get('Target')).resolve()

ICON_NAMES = {   # media file -> published icon name
    'image1.png': 'factory', 'image2.png': 'housing', 'image3.png': 'cart',
    'image4.png': 'briefcase', 'image5.png': 'target', 'image6.png': 'polymarket',
    'image7.png': 'theme', 'image13.png': 'arima', 'image14.png': 'fed',
}


def premul_resize(im, size):
    """Downscale an RGBA image without dark fringes: premultiply, resize, unpremultiply."""
    if im.mode != 'RGBA':
        return im.resize(size, Image.LANCZOS)
    import numpy as np
    a = np.asarray(im).astype('float64')
    alpha = a[..., 3:4] / 255.0
    pm = np.concatenate([a[..., :3] * alpha, a[..., 3:4]], axis=2)
    pm_im = Image.fromarray(pm.round().astype('uint8'), 'RGBA').resize(size, Image.LANCZOS)
    b = np.asarray(pm_im).astype('float64')
    al = b[..., 3:4] / 255.0
    rgb = np.where(al > 0, b[..., :3] / np.maximum(al, 1e-9), 0)
    outa = np.concatenate([np.clip(rgb, 0, 255), b[..., 3:4]], axis=2)
    return Image.fromarray(outa.round().astype('uint8'), 'RGBA')


def export_icon(media, src_rect, w_pt, h_pt):
    name = ICON_NAMES[media.name]
    im = Image.open(media)
    W, H = im.size
    l = t = r = b = 0.0
    if src_rect is not None:
        l = int(src_rect.get('l', 0)) / 100000
        t = int(src_rect.get('t', 0)) / 100000
        r = int(src_rect.get('r', 0)) / 100000
        b = int(src_rect.get('b', 0)) / 100000
    x0, y0 = round(l * W), round(t * H)
    x1, y1 = round(W - r * W), round(H - b * H)
    # negative offsets extend past the bitmap with transparent padding
    if im.mode != 'RGBA':
        im = im.convert('RGBA')
    canvas = Image.new('RGBA', (x1 - x0, y1 - y0), (0, 0, 0, 0))
    canvas.paste(im, (-x0, -y0))
    cw, ch = canvas.size
    tw, th = round(w_pt * ICON_PX_PER_PT), round(h_pt * ICON_PX_PER_PT)
    if tw < cw or th < ch:
        # keep the crop's aspect; the <image> is stretched to the frame anyway
        s = min(tw / cw, th / ch)
        canvas = premul_resize(canvas, (max(1, round(cw * s)), max(1, round(ch * s))))
    OUT_ICONS.mkdir(parents=True, exist_ok=True)
    p = OUT_ICONS / f'{name}.png'
    canvas.save(p, optimize=True)
    return f'{ICON_HREF}{name}.png', canvas.size


def inline_svg(media, x, y, w, h, label):
    """Embed a vector logo as a group of paths, stretched to the frame the way
    PowerPoint stretches it (non-uniform for Claude/Qwen: 59.36 x 62.78)."""
    root = etree.parse(media).getroot()
    vb = [float(v) for v in root.get('viewBox').split()]
    sx, sy = w / vb[2], h / vb[3]
    tr = f'translate({f(x)} {f(y)}) scale({f(sx)} {f(sy)}) translate({f(-vb[0])} {f(-vb[1])})'
    body = []
    for el in root.iter():
        tag = etree.QName(el).localname
        if tag == 'path':
            attrs = {k: v for k, v in el.attrib.items() if k not in ('class',)}
            attrs.setdefault('fill', '#000000')
            body.append('<path ' + ' '.join(f'{k}="{v}"' for k, v in attrs.items()) + '/>')
        elif tag == 'linearGradient':
            stops = ''.join(f'<stop offset="{s.get("offset")}" stop-color="{s.get("stop-color")}"'
                            + (f' stop-opacity="{s.get("stop-opacity")}"' if s.get('stop-opacity') else '')
                            + '/>' for s in el)
            body.append(f'<defs><linearGradient id="{el.get("id")}" x1="{el.get("x1")}" y1="{el.get("y1")}" '
                        f'x2="{el.get("x2")}" y2="{el.get("y2")}">{stops}</linearGradient></defs>')
    return f'<g transform="{tr}" aria-label="{label}">' + ''.join(body) + '</g>'


# ---- text -----------------------------------------------------------------
def run_props(r):
    p = r.find('a:rPr', ns)
    d = {'sz': 18.0, 'b': False, 'i': False, 'color': None, 'font': None}
    if p is not None:
        if p.get('sz'):
            d['sz'] = int(p.get('sz')) / 100
        d['b'] = p.get('b') == '1'
        d['i'] = p.get('i') == '1'
        d['color'] = color_of(p.find('a:solidFill', ns))
        lat = p.find('a:latin', ns)
        if lat is not None:
            d['font'] = lat.get('typeface')
    return d


def para_props(p):
    pp = p.find('a:pPr', ns)
    d = {'algn': 'l', 'marL': 0.0, 'indent': 0.0, 'bu': None, 'spcAft': 0.0}
    if pp is None:
        return d
    d['algn'] = pp.get('algn', 'l')
    d['marL'] = int(pp.get('marL', 0)) / PT
    d['indent'] = int(pp.get('indent', 0)) / PT
    bc = pp.find('a:buChar', ns)
    if bc is not None:
        d['bu'] = bc.get('char')
    sa = pp.find('a:spcAft/a:spcPts', ns)
    if sa is not None:
        d['spcAft'] = int(sa.get('val')) / 100
    return d


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def emit_text(el, name, x, y, w, h, font_color):
    tb = el.find('p:txBody', ns)
    if tb is None:
        return
    bp = tb.find('a:bodyPr', ns)
    if not ''.join(tb.itertext()).strip():
        return  # shape carries no text; anchor is irrelevant
    assert bp.get('anchor', 't') == 't', name
    for k in ('lIns', 'rIns', 'tIns', 'bIns'):
        assert bp.get(k) is None, name
    paras = tb.findall('a:p', ns)
    cursor = y + TINS
    pieces = []
    for pi, p in enumerate(paras):
        pp = para_props(p)
        runs = [(r.findtext('a:t', namespaces=ns) or '', run_props(r)) for r in p.findall('a:r', ns)]
        text = ''.join(t for t, _ in runs).strip()
        sizes = {rp['sz'] for t, rp in runs if t.strip()}
        if not text:
            end = p.find('a:endParaRPr', ns)
            sz = int(end.get('sz')) / 100 if end is not None and end.get('sz') else 18.0
            cursor += LH * sz + pp['spcAft']
            continue
        assert len(sizes) == 1, (name, sizes)
        styles = {(rp['b'], rp['i'], rp['color'], rp['font']) for t, rp in runs if t.strip()}
        assert len(styles) == 1, (name, styles)
        sz = sizes.pop()
        b, i, col, fnt = styles.pop()
        lines = WRAP.get(name, {}).get(pi, [text])
        assert ' '.join(lines) == text, (name, pi, lines, text)
        col = col or font_color or '#000000'
        attrs = f'font-size="{f(sz)}"'
        if b:
            attrs += ' font-weight="700"'
        if i:
            attrs += ' font-style="italic"'
        if col != '#000000':
            attrs += f' fill="{col}"'
        if pp['algn'] == 'ctr':
            attrs += ' text-anchor="middle"'
            tx = x + w / 2
        else:
            tx = x + LINS + pp['marL']
        spans = []
        for li, line in enumerate(lines):
            base = cursor + ASC * sz
            if li == 0 and pp['bu']:
                bx = x + LINS + pp['marL'] + pp['indent']
                spans.append(f'<tspan x="{f(bx)}" y="{f(base)}" font-family="Arial, sans-serif">{esc(pp["bu"])}</tspan>')
            spans.append(f'<tspan x="{f(tx)}" y="{f(base)}">{esc(line)}</tspan>')
            cursor += LH * sz
        cursor += pp['spcAft']
        pieces.append(f'<text {attrs}>' + ''.join(spans) + '</text>')
    out.extend(pieces)


# ---- walk -----------------------------------------------------------------
def walk(node, stack):
    for c in node:
        t = etree.QName(c).localname
        if t == 'grpSp':
            xf = xfrm_of(c.find('p:grpSpPr', ns))
            tr = T(xf['x'], xf['y'], xf['w'] / xf['cw'], xf['h'] / xf['ch'], xf['cx'], xf['cy'])
            walk(c, stack + [tr])
        elif t in ('sp', 'cxnSp', 'pic'):
            shape(c, t, stack)


def shape(el, kind, stack):
    nv = el.find('.//p:cNvPr', ns)
    name = nv.get('name')
    assert not nv.get('hidden'), name
    spPr = el.find('p:spPr', ns)
    xf = xfrm_of(spPr)
    x0, y0 = apply(stack, xf['x'], xf['y'])
    x1, y1 = apply(stack, xf['x'] + xf['w'], xf['y'] + xf['h'])
    w, h = x1 - x0, y1 - y0
    geom = spPr.find('a:prstGeom', ns)
    prst = geom.get('prst') if geom is not None else None
    ln = resolve_line(el, spPr)
    grow(x0, y0, x1, y1)

    if kind == 'pic':
        blip = el.find('.//a:blip', ns)
        svg = el.find('.//asvg:svgBlip', ns)
        if svg is not None:
            media = rels[svg.get(R_EMBED)]
            out.append(inline_svg(media, x0, y0, w, h, name))
        else:
            media = rels[blip.get(R_EMBED)]
            href, _ = export_icon(media, el.find('.//a:srcRect', ns), w, h)
            out.append(f'<image href="{href}" x="{f(x0)}" y="{f(y0)}" width="{f(w)}" height="{f(h)}" '
                       f'preserveAspectRatio="none"/>')
        return

    if kind == 'cxnSp':
        assert prst in ('line', 'straightConnector1'), prst
        if ln is None:
            return
        ex, ey = x1, y1
        parts = []
        if ln['tail']:
            assert ln['tail'] == 'triangle', ln
            poly, (ex, ey) = arrow_head(x0, y0, x1, y1, ln)
            parts.append(poly)
        out.append(f'<line x1="{f(x0)}" y1="{f(y0)}" x2="{f(ex)}" y2="{f(ey)}" {stroke_attrs(ln)}/>')
        out.extend(parts)
        return

    fill = resolve_fill(el, spPr)
    if prst == 'rect':
        if not (fill == 'none' and ln is None):
            out.append(f'<rect x="{f(x0)}" y="{f(y0)}" width="{f(w)}" height="{f(h)}" fill="{fill}" {stroke_attrs(ln)}/>')
    elif prst == 'ellipse':
        out.append(f'<ellipse cx="{f(x0 + w / 2)}" cy="{f(y0 + h / 2)}" rx="{f(w / 2)}" ry="{f(h / 2)}" fill="{fill}" {stroke_attrs(ln)}/>')
    else:
        raise ValueError((name, prst))
    _, fcol = style_ref(el, 'fontRef')
    emit_text(el, name, x0, y0, w, h, fcol)


tree = etree.parse(SRC / 'ppt/slides/slide1.xml')
walk(tree.find('.//p:spTree', ns), [])

PAD = 4.0
bx0, by0, bx1, by1 = bounds
vb = (bx0 - PAD, by0 - PAD, (bx1 - bx0) + 2 * PAD, (by1 - by0) + 2 * PAD)
header = (f'<svg class="fig1" viewBox="{f(vb[0])} {f(vb[1])} {f(vb[2])} {f(vb[3])}" '
          f'xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="fig1-title">\n'
          '<title id="fig1-title">LiveMacroEval pipeline: sixteen indicators, hourly LLM nowcasts across '
          'the pre-release window, scored against the market and against Fed, Bloomberg and ARIMA baselines</title>\n'
          f'<rect x="{f(vb[0])}" y="{f(vb[1])}" width="{f(vb[2])}" height="{f(vb[3])}" fill="#FFFFFF"/>\n')
svg = header + '\n'.join(out) + '\n</svg>'
print(f'content bounds {[round(v, 2) for v in bounds]}  viewBox {[round(v, 2) for v in vb]}')
print(f'{len(out)} elements, {len(svg):,} bytes of SVG, icons in {OUT_ICONS}')

if args.svg_only:
    args.svg_only.write_text(svg + '\n')
    print(f'wrote {args.svg_only}')
    sys.exit(0)

index = args.site / 'index.html'
html = index.read_text()
pat = re.compile(r'<svg class="fig1".*?</svg>|<img src="assets/figures/pipeline_overview\.png"[^>]*>', re.S)
hits = pat.findall(html)
if len(hits) != 1:
    sys.exit(f'expected exactly one Figure 1 block in {index}, found {len(hits)}')
html = pat.sub(lambda m: svg, html)
index.write_text(html)
print(f'spliced into {index}  -- now run tools/check_release_safety.py')
