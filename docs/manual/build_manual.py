#!/usr/bin/env python3
"""Genera docs/manual/Cafeteria-Manual-de-Pruebas.docx con el estilo Softbot.

Estilo: plantilla y funciones de `ProyectoAI/Expreso/docs/bin/seo-impacto/build_seo_impacto.py`
(plantilla-softbot.docx = `Expreso/docs/assets/plantilla.docx`). El contenido está en `contenido.py`.
Uso: python3 docs/manual/build_manual.py   (sobrescribe la salida)
Otro documento con el mismo estilo: python3 docs/manual/build_manual.py <modulo> <salida.docx> "<pie>"
(p. ej. contenido_token Guia-Access-Token-Mercado-Pago.docx "…"); la salida queda junto a este archivo."""
import html
import os
import random
import re
import shutil
import sys
import tempfile
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(AQUI, 'plantilla-softbot.docx')
MODULO = sys.argv[1] if len(sys.argv) > 1 else 'contenido'
OUT = os.path.join(AQUI, sys.argv[2] if len(sys.argv) > 2 else 'Cafeteria-Manual-de-Pruebas.docx')
PIE = sys.argv[3] if len(sys.argv) > 3 else 'Cafetería escolar · Manual de pruebas · Softbot'
W = 10080
random.seed(20261109)
_used = set()


def pid():
    while True:
        v = '%08X' % random.randint(0x10000000, 0x7FFFFFFF)
        if v not in _used:
            _used.add(v); return v

def P(ppr, runs):
    return (f'<w:p w14:paraId="{pid()}" w14:textId="{pid()}" w:rsidR="00000000" '
            f'w:rsidRDefault="00000000"><w:pPr>{ppr}</w:pPr>{runs}</w:p>')

def esc(t):
    return html.escape(t, quote=False)

def R(t, sz=21, color='000000', b=False, mono=False):
    rpr = ''
    if mono:
        rpr += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>'
    if b:
        rpr += '<w:b/><w:bCs/>'
    rpr += f'<w:color w:val="{color}"/><w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>'
    return f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{esc(t)}</w:t></w:r>'

def rich(t, sz=21, color='000000', bold_all=False):
    """**negrita** y `código` en línea."""
    out = []
    for part in re.split(r'(\*\*[^*]+\*\*|`[^`]+`)', t):
        if not part:
            continue
        if part.startswith('**'):
            out.append(R(part[2:-2], sz, color, True))
        elif part.startswith('`'):
            out.append(R(part[1:-1], sz - 2, '0B2A4A', bold_all, mono=True))
        else:
            out.append(R(part, sz, color, bold_all))
    return ''.join(out)

BODY = '<w:spacing w:after="60" w:line="264" w:lineRule="auto"/><w:jc w:val="both"/>'

def h1(t):
    return P('<w:pStyle w:val="Ttulo1"/><w:pBdr><w:bottom w:val="single" w:sz="14" w:space="6" '
             'w:color="1F6FB2"/></w:pBdr><w:spacing w:before="280" w:after="140"/>',
             R(t, 30, '0B2A4A', True))

def h2(t):
    return P('<w:pStyle w:val="Ttulo2"/><w:spacing w:before="220" w:after="80"/>',
             R(t, 25, '1F6FB2', True))

def para(t):
    return P(BODY, rich(t))

def bullet(t):
    return P('<w:spacing w:after="40" w:line="264" w:lineRule="auto"/><w:ind w:left="340" w:hanging="200"/>',
             R('•', 21, '1F6FB2') + '<w:r><w:rPr><w:color w:val="1F6FB2"/><w:sz w:val="21"/>'
             '<w:szCs w:val="21"/></w:rPr><w:tab/></w:r>' + rich(t))

def sep():
    return P('<w:spacing w:after="120" w:line="264" w:lineRule="auto"/><w:jc w:val="both"/>', '')

def code(lines):
    out = []
    for i, ln in enumerate(lines):
        before = 80 if i == 0 else 0
        after = 80 if i == len(lines) - 1 else 0
        ppr = (f'<w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="1F6FB2"/></w:pBdr>'
               f'<w:shd w:val="clear" w:color="auto" w:fill="F2F5F8"/>'
               f'<w:spacing w:before="{before}" w:after="{after}" w:line="240" w:lineRule="auto"/>'
               f'<w:ind w:left="200"/>')
        out.append(P(ppr, R(ln if ln else ' ', 17, '0B2A4A', mono=True)))
    return ''.join(out)

def note(t):
    return P('<w:spacing w:before="300" w:after="60" w:line="264" w:lineRule="auto"/>', R(t, 19, '4A5A6A'))

BRD = ''.join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="CBD2D9"/>'
              for s in ('top', 'left', 'bottom', 'right'))
MAR = ('<w:tcMar><w:top w:w="70" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>'
       '<w:bottom w:w="70" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tcMar>')

def cell(w, t, fill='FFFFFF', color='000000', b=False, sz=20):
    paras = []
    for chunk in t.split('\n'):
        paras.append(P('<w:spacing w:line="264" w:lineRule="auto"/>', rich(chunk, sz, color, b)))
    return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/><w:tcBorders>{BRD}</w:tcBorders>'
            f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>{MAR}<w:vAlign w:val="center"/>'
            f'</w:tcPr>{"".join(paras)}</w:tc>')

def table(widths, header, rows, first_bold=True, sz=20):
    assert sum(widths) == W, (widths, sum(widths))
    grid = ''.join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    tbl = ('<w:tbl><w:tblPr><w:tblW w:w="10080" w:type="dxa"/><w:tblBorders>'
           + ''.join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                     for s in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))
           + '</w:tblBorders><w:tblLayout w:type="fixed"/><w:tblCellMar><w:left w:w="10" w:type="dxa"/>'
             '<w:right w:w="10" w:type="dxa"/></w:tblCellMar><w:tblLook w:val="0000" w:firstRow="0" '
             'w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="0" w:noVBand="0"/></w:tblPr>'
           f'<w:tblGrid>{grid}</w:tblGrid>')
    if header:
        tbl += (f'<w:tr w:rsidR="0000EDE5" w14:paraId="{pid()}" w14:textId="77777777"><w:trPr><w:tblHeader/></w:trPr>'
                + ''.join(cell(w, h, '1F6FB2', 'FFFFFF', True, sz) for w, h in zip(widths, header)) + '</w:tr>')
    for r in rows:
        tbl += (f'<w:tr w:rsidR="0000EDE5" w14:paraId="{pid()}" w14:textId="77777777"><w:trPr><w:cantSplit/></w:trPr>'
                + ''.join(cell(w, c, 'FFFFFF', '0B2A4A' if (i == 0 and first_bold) else '000000',
                               i == 0 and first_bold, sz)
                          for i, (w, c) in enumerate(zip(widths, r))) + '</w:tr>')
    return tbl + '</w:tbl>' + sep()

def cover(title, sub, line):
    c = (f'<w:tc><w:tcPr><w:tcW w:w="10080" w:type="dxa"/><w:tcBorders>'
         + ''.join(f'<w:{s} w:val="none" w:sz="0" w:space="0" w:color="FFFFFF"/>' for s in ('top', 'left', 'bottom', 'right'))
         + '</w:tcBorders><w:shd w:val="clear" w:color="auto" w:fill="0B2A4A"/><w:tcMar><w:top w:w="440" w:type="dxa"/>'
           '<w:left w:w="420" w:type="dxa"/><w:bottom w:w="440" w:type="dxa"/><w:right w:w="420" w:type="dxa"/></w:tcMar></w:tcPr>'
         + P('<w:spacing w:after="60"/>', R(title, 48, 'FFFFFF', True))
         + P('<w:spacing w:after="200"/>', R(sub, 24, 'CFE0F0'))
         + P('<w:pBdr><w:top w:val="single" w:sz="8" w:space="8" w:color="3C8DCC"/></w:pBdr><w:spacing w:before="60"/>',
             R(line, 24, 'FFFFFF', True))
         + '</w:tc>')
    return ('<w:tbl><w:tblPr><w:tblW w:w="10080" w:type="dxa"/><w:tblLayout w:type="fixed"/><w:tblCellMar>'
            '<w:left w:w="10" w:type="dxa"/><w:right w:w="10" w:type="dxa"/></w:tblCellMar><w:tblLook w:val="0000" '
            'w:firstRow="0" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="0" w:noVBand="0"/></w:tblPr>'
            f'<w:tblGrid><w:gridCol w:w="10080"/></w:tblGrid><w:tr w:rsidR="0000EDE5" w14:paraId="{pid()}" '
            f'w14:textId="77777777">{c}</w:tr></w:tbl>' + sep())


sys.path.insert(0, AQUI)
build = __import__(MODULO).build  # noqa: E402

body = build(dict(h1=h1, h2=h2, para=para, bullet=bullet, table=table, code=code,
                  note=note, cover=cover, sep=sep))

work = tempfile.mkdtemp(prefix='manual-cafeteria-')
with zipfile.ZipFile(TPL) as z:
    z.extractall(work)
dp = os.path.join(work, 'word', 'document.xml')
d = open(dp, encoding='utf-8').read()
a = d.index('<w:body>') + len('<w:body>')
b = d.rindex('<w:sectPr')
open(dp, 'w', encoding='utf-8').write(d[:a] + body + d[b:])

fp = os.path.join(work, 'word', 'footer1.xml')
f = open(fp, encoding='utf-8').read()
assert 'Mesa Digital · Hallazgos y alcance · Softbot interno' in f
open(fp, 'w', encoding='utf-8').write(
    f.replace('Mesa Digital · Hallazgos y alcance · Softbot interno', esc(PIE)))

cp = os.path.join(work, 'docProps', 'core.xml')
c = open(cp, encoding='utf-8').read()
c = re.sub(r'<cp:lastModifiedBy>[^<]*</cp:lastModifiedBy>', '<cp:lastModifiedBy>Softbot</cp:lastModifiedBy>', c)
c = re.sub(r'<cp:revision>[^<]*</cp:revision>', '<cp:revision>1</cp:revision>', c)
c = re.sub(r'(<dcterms:created[^>]*>)[^<]*', r'\g<1>2026-11-09T18:00:00Z', c)
c = re.sub(r'(<dcterms:modified[^>]*>)[^<]*', r'\g<1>2026-11-09T18:00:00Z', c)
open(cp, 'w', encoding='utf-8').write(c)

ap = os.path.join(work, 'docProps', 'app.xml')
x = open(ap, encoding='utf-8').read()
for tag in ('TotalTime', 'Pages', 'Words', 'Characters', 'Lines', 'Paragraphs', 'CharactersWithSpaces'):
    x = re.sub(rf'<{tag}>[^<]*</{tag}>', f'<{tag}>0</{tag}>', x)
open(ap, 'w', encoding='utf-8').write(x)

with zipfile.ZipFile(TPL) as zin, zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        if item.is_dir():
            continue
        zout.write(os.path.join(work, item.filename), item.filename)
shutil.rmtree(work, ignore_errors=True)
print('OK', OUT)
