"""Build styled Word copies of the customer manuals with pandoc.

1. Make a reference.docx from pandoc's default: Letter page, 0.75in margins,
   brand font (Calibri by default), brand-colour headings, bordered tables with a shaded header row,
   shaded call-out (block quote) paragraphs, page-number footer.
2. pandoc each manual .md -> .docx using it.
3. Drop folder entries from the zip (Word can refuse them via co-editing).
"""
import html
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "content"
# Colours, product name and Word font come from brand/brand.yaml (shared with
# build.py's HTML/PDF output), so all three formats follow one brand.
BRAND = yaml.safe_load((HERE.parent / "brand" / "brand.yaml").read_text(encoding="utf-8"))
_hex = lambda key: BRAND["colors"][key].lstrip("#").upper()
BLUE = _hex("primary")
SLATE = _hex("text")
GREY = _hex("text_secondary")
BORDER = _hex("table_border")
FONT = BRAND.get("docx_font", "Calibri")
FOOTER = BRAND["footer"]
VERSION = "dev"  # set in __main__


def rewrite_zip(src: Path, dst: Path, changes: dict[str, bytes]):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        names = set()
        for item in zin.infolist():
            if item.filename.endswith("/"):
                continue
            data = changes.get(item.filename, zin.read(item.filename))
            zout.writestr(item.filename, data)
            names.add(item.filename)
        for name, data in changes.items():
            if name not in names:
                zout.writestr(name, data)


def style_block(style_id: str, name: str, body: str, based="Normal", kind="paragraph", extra=""):
    # Schema order: name, basedOn, next, uiPriority, unhideWhenUsed, qFormat, pPr, rPr
    meta = "".join(re.findall(r"<w:(?:next|uiPriority|unhideWhenUsed)[^>]*/>", body))
    body = re.sub(r"<w:(?:next|uiPriority|unhideWhenUsed)[^>]*/>", "", body)
    return (f'<w:style w:type="{kind}" w:styleId="{style_id}"{extra}><w:name w:val="{name}"/>'
            f'<w:basedOn w:val="{based}"/>{meta}<w:qFormat/>{body}</w:style>')


def heading(level: int, size_half_pts: int, color: str, before: int, after: int, border=False):
    pbdr = (f'<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" w:color="{BORDER}"/></w:pBdr>'
            if border else "")
    return style_block(
        f"Heading{level}", f"heading {level}",
        f'<w:next w:val="BodyText"/><w:uiPriority w:val="9"/><w:unhideWhenUsed/>'
        f'<w:pPr><w:keepNext/><w:keepLines/>{pbdr}<w:spacing w:before="{before}" w:after="{after}"/>'
        f'<w:outlineLvl w:val="{level - 1}"/></w:pPr>'
        f'<w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>'
        f'<w:b/><w:color w:val="{color}"/><w:sz w:val="{size_half_pts}"/><w:szCs w:val="{size_half_pts}"/></w:rPr>',
    )


def make_reference(dst: Path):
    base = HERE / "pandoc-default.docx"
    subprocess.run(["pandoc", "-o", str(base), "--print-default-data-file", "reference.docx"], check=True)
    z = zipfile.ZipFile(base)
    styles = z.read("word/styles.xml").decode()
    doc = z.read("word/document.xml").decode()
    rels = z.read("word/_rels/document.xml.rels").decode()
    ctypes = z.read("[Content_Types].xml").decode()

    # Document defaults: Calibri 11pt, slate text, comfortable spacing.
    styles = re.sub(r"<w:docDefaults>.*?</w:docDefaults>",
                    '<w:docDefaults><w:rPrDefault><w:rPr>'
                    f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>'
                    f'<w:color w:val="{SLATE}"/><w:sz w:val="22"/><w:szCs w:val="22"/>'
                    '<w:lang w:val="en-GB" w:eastAsia="en-US" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
                    '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>'
                    '</w:pPrDefault></w:docDefaults>', styles, flags=re.S)

    # Replace the styles we restyle (drop pandoc's versions first).
    for sid in ["Title", "Subtitle", "Heading1", "Heading2", "Heading3", "Heading4",
                "BlockText", "SourceCode", "VerbatimChar", "Table", "Hyperlink", "ImageCaption",
                "CaptionedFigure", "Compact", "FirstParagraph", "BodyText"]:
        styles = re.sub(rf'<w:style [^>]*w:styleId="{sid}"[^>]*>.*?</w:style>', "", styles, flags=re.S)

    new_styles = [
        style_block("BodyText", "Body Text", '<w:pPr><w:spacing w:before="0" w:after="140"/></w:pPr>'),
        style_block("FirstParagraph", "First Paragraph", "", based="BodyText"),
        style_block("Compact", "Compact", '<w:pPr><w:spacing w:before="20" w:after="40"/></w:pPr>', based="BodyText"),
        heading(1, 44, BLUE, 0, 240),
        heading(2, 32, BLUE, 360, 140, border=True),
        heading(3, 26, SLATE, 260, 100),
        heading(4, 23, SLATE, 200, 80),
        style_block("Title", "Title",
                    '<w:pPr><w:spacing w:after="200"/></w:pPr>'
                    f'<w:rPr><w:b/><w:color w:val="{BLUE}"/><w:sz w:val="48"/><w:szCs w:val="48"/></w:rPr>'),
        # Call-outs ("> ..."): shaded panel with a blue left rule.
        style_block("BlockText", "Block Text",
                    '<w:pPr><w:pBdr><w:left w:val="single" w:sz="24" w:space="8" w:color="' + BLUE + '"/></w:pBdr>'
                    '<w:shd w:val="clear" w:color="auto" w:fill="EFF6FF"/>'
                    '<w:spacing w:before="60" w:after="60"/><w:ind w:left="200" w:right="200"/></w:pPr>',
                    based="BodyText"),
        style_block("SourceCode", "Source Code",
                    '<w:pPr><w:shd w:val="clear" w:color="auto" w:fill="F8FAFC"/>'
                    '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
                    '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="17"/></w:rPr>',
                    extra=' w:customStyle="1"'),
        style_block("VerbatimChar", "Verbatim Char",
                    '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="20"/></w:rPr>',
                    based="DefaultParagraphFont", kind="character", extra=' w:customStyle="1"'),
        style_block("Hyperlink", "Hyperlink", f'<w:rPr><w:color w:val="{BLUE}"/><w:u w:val="single"/></w:rPr>',
                    based="DefaultParagraphFont", kind="character"),
        style_block("ImageCaption", "Image Caption",
                    f'<w:pPr><w:spacing w:before="40" w:after="200"/><w:jc w:val="center"/></w:pPr>'
                    f'<w:rPr><w:i/><w:color w:val="{GREY}"/><w:sz w:val="18"/></w:rPr>', based="BodyText"),
        style_block("CaptionedFigure", "Captioned Figure",
                    '<w:pPr><w:keepNext/><w:spacing w:before="120" w:after="0"/><w:jc w:val="center"/></w:pPr>',
                    based="BodyText"),
        # Tables: thin borders everywhere, shaded bold header row, compact cells.
        '<w:style w:type="table" w:styleId="Table"><w:name w:val="Table"/><w:basedOn w:val="TableNormal"/>'
        '<w:uiPriority w:val="99"/><w:pPr><w:spacing w:before="40" w:after="40" w:line="240" w:lineRule="auto"/></w:pPr>'
        '<w:rPr><w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr>'
        '<w:tblPr><w:tblBorders>'
        + "".join(f'<w:{e} w:val="single" w:sz="4" w:space="0" w:color="{BORDER}"/>'
                  for e in ["top", "left", "bottom", "right", "insideH", "insideV"])
        + '</w:tblBorders><w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
          '<w:bottom w:w="40" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr>'
          '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/><w:color w:val="' + SLATE + '"/></w:rPr>'
          '<w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="E2E8F0"/></w:tcPr></w:tblStylePr></w:style>',
    ]
    styles = styles.replace("</w:styles>", "".join(new_styles) + "</w:styles>")

    # Footer with "<brand footer> · v<version> · Page N".
    footer = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
              f'<w:r><w:rPr><w:color w:val="{GREY}"/><w:sz w:val="18"/></w:rPr><w:t xml:space="preserve">{html.escape(FOOTER)} · v{VERSION} · Page </w:t></w:r>'
              f'<w:r><w:rPr><w:color w:val="{GREY}"/><w:sz w:val="18"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
              f'<w:r><w:rPr><w:color w:val="{GREY}"/><w:sz w:val="18"/></w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
              f'<w:r><w:rPr><w:color w:val="{GREY}"/><w:sz w:val="18"/></w:rPr><w:fldChar w:fldCharType="separate"/></w:r>'
              f'<w:r><w:rPr><w:color w:val="{GREY}"/><w:sz w:val="18"/></w:rPr><w:t>1</w:t></w:r>'
              f'<w:r><w:rPr><w:color w:val="{GREY}"/><w:sz w:val="18"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r>'
              '</w:p></w:ftr>')
    rels = rels.replace("</Relationships>",
                        '<Relationship Id="rIdDmFooter" '
                        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
                        'Target="footer1.xml"/></Relationships>')
    if "footer+xml" not in ctypes:
        ctypes = ctypes.replace("</Types>",
                                '<Override PartName="/word/footer1.xml" ContentType="application/'
                                'vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>')
    sect = ('<w:sectPr><w:footerReference w:type="default" r:id="rIdDmFooter" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
            '<w:pgSz w:w="12240" w:h="15840"/>'
            '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="600" w:footer="500" w:gutter="0"/>'
            '</w:sectPr>')
    doc = re.sub(r"<w:sectPr.*?</w:sectPr>|<w:sectPr[^>]*/>", sect, doc, flags=re.S)

    rewrite_zip(base, dst, {
        "word/styles.xml": styles.encode(),
        "word/document.xml": doc.encode(),
        "word/_rels/document.xml.rels": rels.encode(),
        "[Content_Types].xml": ctypes.encode(),
        "word/footer1.xml": footer.encode(),
    })


# --- Workbooks: give answer boxes room to write in ------------------------
SMALL_COLS = {"#", "order", "done", "ready", "round", "version", "lot", "critical?", "any critical?"}
WIDE_WORDS = ("answer", "note", "guidance", "reply", "reason", "what you", "what happened",
              "resolution", "event", "task", "question", "what the image", "weak note", "banner")


TEXT_WIDTH = 10080  # Letter, 0.75in margins, in twips


def col_weight(head: str) -> int:
    h = head.strip().lower()
    if h in SMALL_COLS:
        return 1
    return 5 if any(w in h for w in WIDE_WORDS) else 3


def size_tables(doc: str) -> str:
    """Full-width, fixed-layout tables with column widths by header type."""
    def fix(m):
        tbl = m.group(0)
        first = re.search(r"<w:tr>.*?</w:tr>", tbl, flags=re.S).group(0)
        heads = ["".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", c))
                 for c in re.findall(r"<w:tc>.*?</w:tc>", first, flags=re.S)]
        weights = [col_weight(h) for h in heads] or [1]
        widths = [TEXT_WIDTH * w // sum(weights) for w in weights]
        tbl = re.sub(r"<w:tblW [^>]*/>", f'<w:tblW w:type="dxa" w:w="{TEXT_WIDTH}" /><w:tblLayout w:type="fixed" />', tbl, count=1)
        tbl = re.sub(r"<w:tblGrid>.*?</w:tblGrid>",
                     "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}" />' for w in widths) + "</w:tblGrid>", tbl, count=1, flags=re.S)

        def fix_row(r):
            i = iter(widths + [widths[-1]] * 20)
            return re.sub(r"<w:tcPr />|<w:tcPr>", lambda t: (f'<w:tcPr><w:tcW w:type="dxa" w:w="{next(i)}" />'
                                                             + ("</w:tcPr>" if t.group(0) == "<w:tcPr />" else "")), r.group(0))
        return re.sub(r"<w:tr>.*?</w:tr>", fix_row, tbl, flags=re.S)
    return re.sub(r"<w:tbl>.*?</w:tbl>", fix, doc, flags=re.S)


def tall_answer_rows(doc: str) -> str:
    """Rows with an empty cell are answer rows: give them writing height."""
    def fix(m):
        row = m.group(0)
        cells = re.findall(r"<w:tc>.*?</w:tc>", row, flags=re.S)
        if not cells or all(re.search(r"<w:t[ >]", c) for c in cells):
            return row
        height = 1100 if len(cells) == 1 else 560
        trh = f'<w:trHeight w:val="{height}" w:hRule="atLeast"/>'
        if "<w:trPr>" in row:
            return row.replace("<w:trPr>", "<w:trPr>" + trh, 1)
        return row.replace("<w:tr>", f"<w:tr><w:trPr>{trh}</w:trPr>", 1)
    return re.sub(r"<w:tr>.*?</w:tr>", fix, doc, flags=re.S)


def build(md_name: str, out: Path, ref: Path):
    _build(SRC / md_name, out, ref, "workbook" in md_name)


LOGO_WIDTH_IN = 2.0


def _logo_for_word(tmpdir: Path) -> Path | None:
    """The brand logo with its DPI set so pandoc places it LOGO_WIDTH_IN wide."""
    if not BRAND.get("logo"):
        return None
    from PIL import Image

    im = Image.open(HERE.parent / "brand" / BRAND["logo"])
    im.thumbnail((640, 640))  # plenty for 2 in; keeps the .docx small
    dpi = im.width / LOGO_WIDTH_IN
    path = tmpdir / "docx-logo.png"
    im.save(path, dpi=(dpi, dpi))
    return path


def _build(src: Path, out: Path, ref: Path, workbook: bool):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        md = src.read_text(encoding="utf-8")
        logo = _logo_for_word(Path(tmp))
        if logo:
            # Empty alt text, so no caption under it.
            md = f"![]({logo})\n\n" + md
        # Image alt text doubles as the caption; pandoc puts it under each picture.
        subprocess.run([
            "pandoc", "-", "-f", "gfm+implicit_figures", "-t", "docx",
            "--reference-doc", str(ref), "--resource-path", str(SRC), "-o", str(out),
        ], check=True, cwd=SRC, input=md, text=True)
    # Tidy pandoc 2.17 output: pStyle must come before numPr inside pPr, and
    # declare png as a Default type (pandoc only adds per-file Overrides).
    with zipfile.ZipFile(out) as z:
        doc = z.read("word/document.xml").decode()
        ct = z.read("[Content_Types].xml").decode()
        numbering = z.read("word/numbering.xml").decode()
    # nsid must be 8 hex digits; pandoc 2.17 writes 4.
    numbering = re.sub(r'(<w:nsid w:val=")([0-9A-Fa-f]{1,7})(")', lambda m: m[1] + m[2].rjust(8, "0") + m[3], numbering)
    doc = re.sub(r"<w:pPr>(<w:numPr>.*?</w:numPr>)(<w:pStyle [^>]*/>)", r"<w:pPr>\2\1", doc)
    if 'Extension="png"' not in ct:
        ct = ct.replace('<Default Extension="xml"', '<Default Extension="png" ContentType="image/png" /><Default Extension="xml"', 1)
    # pandoc 2.17's settings.xml has its children out of schema order; use a
    # minimal valid one (Word 2013+ compatibility mode).
    settings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:zoom w:percent="100"/><w:defaultTabStop w:val="720"/>'
                '<w:characterSpacingControl w:val="doNotCompress"/>'
                '<w:compat><w:compatSetting w:name="compatibilityMode" '
                'w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
                '</w:settings>')
    if workbook:
        doc = size_tables(tall_answer_rows(doc))
    tmp = out.with_suffix(".tmp")
    rewrite_zip(out, tmp, {"word/settings.xml": settings.encode(), "word/document.xml": doc.encode(), "[Content_Types].xml": ct.encode(), "word/numbering.xml": numbering.encode()})
    shutil.move(tmp, out)
    print(out, out.stat().st_size)


if __name__ == "__main__":
    # Usage: build_docx.py [outdir] [md ...]; version from $APP_VERSION or ../../VERSION.
    VERSION = os.environ.get("APP_VERSION") or (HERE.parents[1] / "VERSION").read_text().strip()
    ref = HERE / "docmate-reference.docx"
    make_reference(ref)
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "dist" / "docx"
    outdir.mkdir(parents=True, exist_ok=True)
    docs = sys.argv[2:] or ["customer-supervisor-manual.md", "customer-reviewer-manual.md",
                            "customer-supervisor-workbook.md", "customer-reviewer-workbook.md"]
    for md in docs:
        build(md, outdir / md.replace(".md", ".docx"), ref)
