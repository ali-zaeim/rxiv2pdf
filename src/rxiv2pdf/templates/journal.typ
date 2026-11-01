// rxiv2pdf journal template.
//
// All styling lives here; the generated .typ only passes data and calls the functions
// below. Look-and-feel is driven by a theme dictionary (`default-theme` below, overridden
// by themes/<name>.typ): page geometry, title block, abstract, headings, captions, running
// header. Themes choose among named styles; the template alone keeps the baseline grid.
//
// Layout: full-width front matter (any length - consortium author lists can run past a
// page), then the body in a two-column `columns()` block. Floats placed with
// `scope: "parent"` inside it span both columns (used for wide figures).

#let default-theme = (
  body: ("Source Serif 4 SmText", "Libertinus Serif"),
  sans: ("Source Sans 3", "Libertinus Serif"),
  mono: ("DejaVu Sans Mono",),
  accent: rgb("#1f4e79"),
  muted: luma(95),
  // Page and body text.
  body-size: 9.5pt,
  line-pitch: 1.25, // baseline-to-baseline distance in the body, as a multiple of body-size
  justify: true, // false: ragged right (body, abstract, captions)
  gutter: 5mm,
  margin: (x: 18mm, top: 20mm, bottom: 20mm),
  cite-style: "superscript", // or "brackets"
  // Front matter.
  top-band: none, // height of a full-bleed accent band across the top of page 1
  title-font: "sans", // or "body"
  title-size: 19pt,
  title-weight: "bold",
  author-size: 9.5pt,
  affil-size: 7.5pt,
  affil-style: "lines", // or "run-on" (one paragraph; always used for consortium-size lists)
  abstract-style: "box", // "plain" (bold paragraph, rule below) or "bar" (accent rule on the left)
  abstract-size: 9pt,
  // Section headings.
  heading-numbering: "1.1", // or none
  heading-color: "accent", // level 1: "accent" or "black"
  heading-sizes: (10.5pt, 9.5pt), // level 1, levels 2+
  heading-case: "normal", // level 1: "normal" or "upper" (letterspaced capitals)
  heading-rule: false, // level 1: a hairline in the blank line above it
  subheading-style: "block", // levels 2+: "block", or "run-in" (bold "Title." opening the paragraph)
  // Captions, tables, references.
  caption-size: 8pt,
  caption-label-color: "black", // or "accent"
  table-size: 8pt,
  ref-size: 7.5pt,
  ref-pitch: 0.75, // reference-list line pitch as a fraction of the body's
  // Running header and footer.
  folio: "footer", // page number centred in the footer, or "header" (outer corner of the header)
  header-rule: 0.4pt + luma(170), // stroke under the running header, or none
  // Float rules.
  span-aspect: 1.3, // wider than tall by more than this -> span both columns
  span-caption-words: 80, // or a caption longer than this
  span-image-tables: true, // table screenshots are text-dense: always full width
  figure-text-max: 12pt, // figures are shrunk until their lettering prints at most this size
  float-max: 0.94, // a float (image + caption) may use this fraction of the text height
)

#let _theme = state("rxiv-theme", default-theme)
#let _t(key) = _theme.get().at(key)

// ---- baseline grid ----------------------------------------------------------------------
// Two columns only look typeset if their lines share baselines and both columns end on the
// same line. Typst has no baseline grid, so the body builds one:
//  * every text line box has the same height: fixed top/bottom edges that superscripts and
//    subscripts fit inside (with Typst's default cap-height edges, a line holding a
//    superscript citation came out taller and pushed the rest of its column off the grid);
//  * paragraph spacing equals the leading, so a paragraph break adds no partial line;
//  * headings, display blocks and column floats are padded to whole lines (`_snap`, `_float`);
//  * widow/orphan control is off, so a column never ends a line early.
#let _line-box = 1.1 // em: top-edge (0.8em) minus bottom-edge (-0.3em)
#let _pitch() = _t("body-size") * _t("line-pitch")
#let _lead-for(size) = _pitch() - _line-box * size // leading that keeps `size` text on the grid
// Size of the running text whose lines make up the grid (body, or the smaller back matter).
// Headings need it to put their baseline where a text line's would be, and cannot read it
// from `text.size`: Typst's own heading style has already enlarged that (x1.4 at level 1).
#let _line-size = state("rxiv-line-size", none)

// Pad `body` to a whole number of grid lines (plus `extra` blank lines above it).
#let _snap(body, extra: 0, at: horizon) = context layout(size => {
  let p = _pitch()
  let gap = par.spacing.to-absolute()
  let h = measure(block(width: size.width, body)).height
  let k = calc.max(1, calc.ceil((h + gap) / p - 0.01)) + extra
  block(width: 100%, height: k * p - gap, above: gap, below: gap, breakable: false, align(at, body))
})

// ---- citations ------------------------------------------------------------------------
// `segments` are (first, last, first-label, last-label), already collapsed by the renderer:
// ((1, 5, "ref-c1", "ref-c5"), (7, 7, "ref-c7", "ref-c7")) -> 1–5, 7, each linked to its entry.
#let _num(n, lbl) = link(label(lbl), str(n))
#let cites(segments) = context {
  let parts = segments.map(((a, b, la, lb)) => if a == b { _num(a, la) } else if b == a + 1 { [#_num(a, la),#_num(b, lb)] } else { [#_num(a, la)–#_num(b, lb)] })
  if _t("cite-style") == "brackets" {
    [~#box[\[#parts.join([, ])\]]]
  } else {
    // word joiner + box: never split a citation, never separate it from the preceding word
    [#sym.wj#box(super(typographic: false, parts.join([,])))]
  }
}

// ---- inline helpers ---------------------------------------------------------------------
#let mono(body) = context text(font: _t("mono"), size: 0.88em, body)

// The text of a piece of content (enough to see how a heading title ends).
#let _plain(c) = if type(c) == str { c } else if c.has("text") { _plain(c.text) } else if c.has("children") {
  c.children.map(_plain).sum(default: "")
} else if c.has("body") { _plain(c.body) } else if c == [ ] { " " } else { "" }

// bioRxiv formula images (Cambria-style math, ~16px type) look ~1.25x too big next to our
// serif at 1px = 1/16 em; 1/20 em matches the glyph sizes (checked on real inline and
// display formulas), at whatever text size the theme uses.
#let _px = 1em / 20
// Inline formula images are centred on the math axis (~0.3em above the baseline), so a
// stacked fraction straddles the text line the way typeset math does.
// An "inline" formula wider than 70% of a column is really a display formula (seen: a 719px
// one glued to the next word, which can only overflow); it is set on its own line instead.
#let inline-image(path, px-height: none, px-width: none) = context {
  let h = if px-height != none { (px-height * _px).to-absolute() } else { 1em.to-absolute() }
  let m = page.margin
  let side(k) = if type(m) == dictionary { m.at(k, default: 0pt) } else { m }
  let col = (page.width - side("left") - side("right") - _t("gutter")) / 2
  let w = if px-width != none and px-height != none { h * px-width / px-height } else { none }
  if w != none and w > col * 0.7 {
    _snap(align(center, image(path, width: calc.min(w, col))))
  } else {
    box(baseline: h / 2 - 0.3em.to-absolute(), image(path, height: h))
  }
}

// A paragraph resuming after a display element (equation): no first-line indent.
#let cont-par(body) = par(first-line-indent: 0pt, body)

// ---- figures and tables -----------------------------------------------------------------
// Placement: every figure/table is a float at the top of the column or page, emitted
// by the renderer right after the paragraph that first cites it. Width rule: images wider
// than tall by more than `span-aspect`, or captions over `span-caption-words`, span both
// columns. Image heights are fitted so image + measured caption always fit on one page.
// Widths are also capped by the figure's lettering (see `figure-block`).

// page.margin is a dictionary when set per side (as here), a single length otherwise.
#let _margin(side) = if type(page.margin) == dictionary { page.margin.at(side, default: 0pt) } else { page.margin }
#let _text-height() = page.height - _margin("top") - _margin("bottom")
#let _full-width() = page.width - _margin("left") - _margin("right")

// A float is a figure with native placement, so it stays locatable/queryable and labels
// attach to it. `body` is a function of the available size (for fitting images). Its height
// is padded so float + clearance is a whole number of grid lines; otherwise the text under a
// one-column figure would sit off the other column's baselines.
//
// Typst 0.15 places a column float that it moves from the left to the right column without
// checking that it fits under a page-wide float already on that page, so the float runs off
// the page (seen: a tall supplementary figure under a full-width one, its caption cut off).
// Floats moved to a new page, and page-wide floats, are placed correctly. So a column float
// checks where its end landed; if past the bottom margin it marks itself (metadata), and on
// the next layout pass it becomes page-wide and goes where it fits. The mark is re-emitted
// once promoted, so the decision sticks and the layout converges.
#let _float-count = counter("rxiv-float")
// Floats already known to need promoting (passed back in by compile.py when a cascade of
// promotions needed more layout passes than Typst makes).
#let _pre-promoted = sys.inputs.at("rxiv-promote", default: "").split(",").filter(s => s != "").map(int)
// A section holding nothing but floats (e.g. "Supplementary tables") would leave its heading
// behind in the text while the floats go to the tops of pages: the renderer passes the
// heading as `header` of the first float instead, set above its caption.
#let _header-block(header) = if header != none { align(left, block(width: 100%, below: 1em, header)) }
#let _float(span, kind, cap, cap-pos, body, header: none) = [#_float-count.step()#context {
  let key = _float-count.get().first()
  let promoted = not span and (key in _pre-promoted or query(<rxiv-promote>).any(m => m.value == key))
  let mark = if span { none } else {
    context if promoted or here().position().y > page.height - _margin("bottom") + 0.5pt {
      [#metadata(key)<rxiv-promote>]
    }
  }
  figure(
  placement: top,
  scope: if span or promoted { "parent" } else { "column" },
  kind: kind, supplement: none, numbering: none, gap: 0.6em,
  layout(size => context {
    set text(top-edge: "cap-height", bottom-edge: "baseline") // floats are not on the grid
    let content = block(width: size.width, body(size))
    let p = _pitch()
    let hdr = _header-block(header)
    let total = measure(content).height + 0.6em.to-absolute() + measure(block(width: size.width, cap)).height + measure(block(width: size.width, hdr)).height
    let pad = calc.ceil((total + p) / p - 0.01) * p - p - total
    if cap-pos == bottom { hdr; v(pad); content } else { content; v(pad); mark }
  }),
  caption: figure.caption(position: cap-pos, if cap-pos == bottom [#cap#mark] else [#_header-block(header)#cap]),
  )
}]

#let _fitted(path, width, max-h) = {
  let img = image(path, width: width)
  if measure(img).height > max-h { image(path, height: max-h) } else { img }
}

#let _placeholder(what) = context rect(
  width: 100%, height: 2.5cm, stroke: 0.5pt + luma(165), fill: luma(246),
  align(center + horizon, text(font: _t("sans"), fill: _t("muted"), size: 8pt)[#what not available]),
)

#let _caption-text(lbl, title, caption) = context {
  set text(font: _t("sans"), size: _t("caption-size"), top-edge: "cap-height", bottom-edge: "baseline")
  set par(justify: _t("justify"), first-line-indent: 0pt, leading: 0.5em, spacing: 0.5em)
  if lbl != none {
    text(weight: "bold", fill: if _t("caption-label-color") == "accent" { _t("accent") } else { black })[#lbl.]
    [ ]
  }
  // A title without closing punctuation would run straight into the caption text.
  if title != none and title != [] {
    let ends = _plain(title).trim().ends-with(regex("[.?!:]['\"’”)]*"))
    [*#title#if not ends [.]* ]
  }
  caption
}

// Images stacked under/over a caption, each fitted so the whole float fits on a page.
#let _image-stack(images, size, reserved, spacing: 0.3em, max-widths: none) = context {
  let t = _theme.get()
  let n = calc.max(images.len(), 1)
  let avail = _text-height() * t.float-max - reserved - spacing.to-absolute() * (n - 1)
  let max-h = calc.max(avail / n, _text-height() * 0.2)
  let widths = if max-widths == none { images.map(_ => size.width) } else { max-widths.map(w => if w == none { size.width } else { calc.min(w, size.width) }) }
  stack(spacing: spacing, ..images.zip(widths).map(((p, w)) => align(center, _fitted(p, w, max-h))))
}

// bioRxiv serves figures ~1280 px wide whatever their content, so a simple chart that is wider
// than tall would span the page with lettering at twice the body size. `sizes` gives, per
// image, (px-width, px-height, glyph-px): the median height of its lettering's glyphs (none if
// it has no clear lettering). Each image is printed at most so wide that its lettering comes
// out at `figure-text-max` (a glyph is ~0.53 em), never under half a column; a figure that
// then fits in a column stays in it.
#let _glyph-per-em = 0.53
#let figure-block(lbl: none, title: none, caption: [], images: (), sizes: (), aspect: none, caption-words: 0, supplementary: false, header: none) = context {
  let t = _theme.get()
  let col = (_full-width() - t.gutter) / 2
  let caps = if sizes.len() == images.len() {
    sizes.map(s => if s == none or s.at(2) == none { none } else {
      calc.max(s.at(0) * _glyph-per-em * t.figure-text-max / s.at(2), col / 2)
    })
  } else { images.map(_ => none) }
  let narrow = caps.len() > 0 and caps.all(c => c != none and c <= col)
  let span = (not narrow and aspect != none and aspect > t.span-aspect) or caption-words > t.span-caption-words
  let cap = _caption-text(lbl, title, caption)
  _float(span, image, cap, bottom, header: header, size => {
    let reserved = measure(block(width: size.width, cap)).height + measure(block(width: size.width, _header-block(header))).height + 1.4em.to-absolute()
    if images.len() == 0 { _placeholder("Figure") } else { _image-stack(images, size, reserved, max-widths: caps) }
  })
}

// Booktabs: rules above and below the table and under the header; no vertical rules.
// `groups` are runs of body rows that a rowspan ties together (never split across chunks).
#let _booktabs(xml, groups, foot: true) = context {
  let t = _theme.get()
  show table.cell: it => if it.y < xml.nhead { text(font: t.sans, weight: "semibold", it) } else { it }
  set text(hyphenate: false)
  table(
    columns: xml.ncols,
    stroke: none,
    inset: (x: 4pt, y: 2.6pt),
    align: left + horizon,
    table.hline(stroke: 0.8pt),
    ..if xml.head.len() > 0 { (table.header(..xml.head), table.hline(stroke: 0.45pt)) } else { () },
    ..groups.flatten(),
    ..if foot and xml.foot.len() > 0 { (table.hline(stroke: 0.3pt), ..xml.foot) } else { () },
    table.hline(stroke: 0.8pt),
  )
}

// XML tables are floats. A table wider than a column spans both (and shrinks if wider than
// the page); one taller than a page is cut into page-sized chunks of row groups, each its own
// full-width float with the header repeated and "(continued)" on all but the first.
#let _xml-table-block(lbl, title, caption, footer, xml, header: none) = context {
  let t = _theme.get()
  set text(size: t.table-size, top-edge: "cap-height", bottom-edge: "baseline")
  set par(justify: false, first-line-indent: 0pt, leading: 0.45em)
  let full = _full-width()
  let col-w = (full - t.gutter) / 2
  let natural = measure(_booktabs(xml, xml.groups))
  let scale-f = calc.min(1, full / natural.width)
  let fitted(groups, last) = {
    let tb = _booktabs(xml, groups, foot: last)
    if scale-f < 1 { scale(scale-f * 100%, reflow: true, tb) } else { tb }
  }
  let cap-first = [#_header-block(header)#_caption-text(lbl, title, caption)] // measured with its header
  let cap-cont = _caption-text(if lbl != none [#lbl (continued)] else [Table (continued)], none, [])
  let foot-block = if footer != [] { block(above: 0.5em, text(size: t.table-size - 1pt, footer)) } else { none }
  let room = _text-height() * t.float-max
  let height(groups, cap, last) = measure(block(width: full, {
    cap
    fitted(groups, last)
    if last { foot-block }
  })).height + 1em.to-absolute()

  if height(xml.groups, cap-first, true) <= room {
    let span = natural.width > col-w
    return _float(span, table, cap-first, top, size => { fitted(xml.groups, true); foot-block })
  }
  // Greedy chunking by row group.
  let chunks = ()
  let current = ()
  for g in xml.groups {
    let cap = if chunks.len() == 0 { cap-first } else { cap-cont }
    if current.len() > 0 and height(current + (g,), cap, false) > room {
      chunks.push(current)
      current = ()
    }
    current.push(g)
  }
  chunks.push(current)
  for (i, chunk) in chunks.enumerate() {
    let last = i == chunks.len() - 1
    _float(true, table, if i == 0 { cap-first } else { cap-cont }, top, size => {
      fitted(chunk, last)
      if last { foot-block }
    })
  }
}

// Stand-in for a table that was moved to the end of the article (estimated > 2 pages).
#let deferred-note(lbl: none, target: none, pages: none) = _snap(at: top, context {
  set par(first-line-indent: 0pt)
  set text(font: _t("sans"), size: 8pt, fill: _t("muted"), style: "italic")
  let name = if lbl != none { lbl } else [This table]
  [#if target != none { link(label(target), name) } else { name } (about #pages pages) is placed at the end of the article.]
})

#let table-block(lbl: none, title: none, caption: [], images: (), sizes: (), aspect: none, footer: [], note: none, xml: none, header: none) = context {
  let t = _theme.get()
  if xml != none { return _xml-table-block(lbl, title, caption, footer, xml, header: header) }
  if images.len() == 0 {
    return _float(false, table, [#_header-block(header)#_caption-text(lbl, title, caption)], top, size => _placeholder(if note != none { note } else { "Table" }))
  }
  // Image tables (bioRxiv's screenshots of the authors' tables). bioRxiv renders them all
  // 1280 px wide, so full width can mean table text at 1.5-3x the body size. When the height
  // of their text lines is known (`sizes`: (px-width, px-height, text-px) per image), they are
  // scaled so that text comes out at the table size, at most the full width; one that is then
  // no wider than a column stays in the column. Unknown: full width, as before.
  // A table that bioRxiv cut into slices (one per page of the original) is re-joined: slices
  // are stacked in one float while they fit, and only a float that starts a new piece is
  // marked "(continued)".
  let full = _full-width()
  let col = (full - t.gutter) / 2
  let known = sizes.len() == images.len() and sizes.all(s => s != none and s.at(2) != none)
  let scale = if known {
    let line = sizes.map(s => s.at(2)).sorted().at(calc.quo(sizes.len(), 2)) // median text-px
    calc.min(0.9 * t.table-size / line, full / calc.max(..sizes.map(s => s.at(0))))
  }
  let widths = if scale != none { sizes.map(s => s.at(0) * scale) } else { images.map(_ => full) }
  let span = if scale != none { calc.max(..widths) > col } else {
    t.span-image-tables or (aspect != none and aspect > t.span-aspect)
  }
  let width = if span { full } else { col }
  let head-first = [#_header-block(header)#_caption-text(lbl, title, caption)]
  let head-cont = _caption-text(if lbl != none [#lbl (continued)] else [Table (continued)], none, [])
  let foot = if footer != [] { block(above: 0.4em, text(font: t.sans, size: t.table-size - 1pt, footer)) } else { none }
  let room = _text-height() * t.float-max
  let reserved(head, f) = measure(block(width: width, head)).height + measure(block(width: width, f)).height + 1.4em.to-absolute()
  let heights = images.zip(widths).map(((p, w)) => measure(image(p, width: calc.min(w, width))).height)
  let groups = ()
  let current = ()
  let used = 0pt
  for (i, h) in heights.enumerate() {
    let head = if groups.len() == 0 { head-first } else { head-cont }
    if current.len() > 0 and used + h > room - reserved(head, foot) {
      groups.push(current)
      current = ()
      used = 0pt
    }
    current.push(i)
    used += h
  }
  groups.push(current)
  for (g, members) in groups.enumerate() {
    let head = if g == 0 { head-first } else { head-cont }
    let f = if g == groups.len() - 1 { foot } else { none }
    _float(span, table, head, top, size => {
      let max-h = calc.max(room - reserved(head, f), _text-height() * 0.2)
      stack(..members.map(i => align(center, _fitted(images.at(i), calc.min(widths.at(i), size.width), max-h))))
      f
    })
  }
}

#let display-image(path, px-width: none) = _snap(align(center, layout(size => {
  let w = if px-width != none { calc.min(px-width * _px.to-absolute(), size.width) } else { size.width }
  image(path, width: w)
})))
// Typst cannot break a display equation by itself. One wider than the column is split
// between its top-level pieces (terms, operators; `segments` is Typst math source) into lines
// that fit, continuation lines indented; a single piece still too wide is scaled to fit.
#let _fit-eq(eq, width) = {
  let w = measure(eq).width
  if w > width { scale(width / w * 100%, reflow: true, eq) } else { eq }
}
#let display-math(eq, segments: ()) = _snap(layout(size => {
  if measure(eq).width <= size.width or segments.len() < 2 { return _fit-eq(eq, size.width) }
  let as-eq(src) = math.equation(block: true, eval(src, mode: "math"))
  let indent = 2em.to-absolute()
  let lines = ()
  let current = ""
  for seg in segments {
    let trial = if current == "" { seg } else { current + " " + seg }
    let room = if lines.len() == 0 { size.width } else { size.width - indent }
    if current != "" and measure(as-eq(trial)).width > room {
      lines.push(current)
      current = seg
    } else {
      current = trial
    }
  }
  lines.push(current)
  stack(spacing: 0.5em, ..lines.enumerate().map(((i, src)) => {
    let room = if i == 0 { size.width } else { size.width - indent }
    let line = align(left, _fit-eq(as-eq(src), room))
    if i == 0 { line } else { pad(left: indent, line) }
  }))
}))
#let display-text(body) = _snap(align(center, body))

// `url`: where the server serves the file, so the listing links to it. `appended`: index of
// the file among the supplements appended after the article (`appendix`), linked by page.
#let supplement-item(lbl: none, file: none, url: none, appended: none, caption: []) = _snap(at: top, context {
  set par(first-line-indent: 0pt)
  if lbl != none { text(font: _t("sans"), weight: "semibold", lbl) }
  if file != none {
    let name = raw(file)
    [ #text(fill: _t("muted"), size: 0.9em, if url != none { link(url, name) } else { name })]
  }
  if appended != none {
    let at = query(<rxiv-appended>).filter(m => m.value == appended)
    if at.len() > 0 {
      let loc = at.first().location()
      [ #box(text(size: 0.9em, link(loc)[(appended, p.~#counter(page).at(loc).first())]))]
    }
  }
  if caption != [] { linebreak(); text(size: 0.9em, caption) }
})

// Supplementary files appended after the article: each starts a page; every page of a PDF is
// placed whole, scaled to fit, under a band naming the file. Each gets a PDF bookmark.

// ---- last-page balancing ------------------------------------------------------------------
// Typst does not balance columns, so the last page of an article often has the end of the
// reference list in the left column and an empty right column. compile.py reads where the
// body ends (`<rxiv-end>`: page, column, height used) and compiles again with `rxiv-balance`
// set to the height of an empty page-wide float to put at the bottom of that page: the two
// columns then end at the same height. It keeps the result only if the end stayed on that page.
#let _balance-height = {
  let v = sys.inputs.at("rxiv-balance", default: "")
  if v == "" { none } else { float(v) * 1pt }
}
#let _end-probe() = context {
  let pos = here().position()
  let floats = query(figure).filter(f => f.location().page() == pos.page).len()
  [#metadata((
    page: pos.page,
    col: if pos.x > page.width / 2 { 1 } else { 0 },
    used: (pos.y - _margin("top")) / 1pt,
    avail: _text-height() / 1pt,
    pitch: _pitch() / 1pt,
    floats: floats,
  ))<rxiv-end>]
}
#let _balance-spacer() = if _balance-height != none {
  place(bottom, float: true, scope: "parent", clearance: 0pt,
    block(width: 100%, height: _balance-height, context [#metadata(here().page())<rxiv-spacer>]))
}

#let _appendix(items, t) = {
  for (i, item) in items.enumerate() {
    set page(
      margin: (x: 10mm, top: 15mm, bottom: 13mm),
      background: none,
      header: {
        set text(font: t.sans, size: 7.5pt, fill: t.muted)
        grid(columns: (1fr, auto), column-gutter: 1em, text(weight: "bold", fill: black, item.label), raw(item.file))
        v(-0.35em)
        line(length: 100%, stroke: 0.4pt + luma(170))
      },
    )
    place(hide(heading(level: 1, numbering: none, outlined: false, bookmarked: true, item.label)))
    [#metadata(i)<rxiv-appended>]
    for k in range(1, item.pages + 1) {
      if k > 1 { pagebreak() }
      let img = if item.pdf { image(item.path, page: k, width: 100%, height: 100%, fit: "contain") } else {
        image(item.path, width: 100%, height: 100%, fit: "contain")
      }
      block(width: 100%, height: 100%, align(center + horizon, img))
    }
  }
}

#let def-list(..items) = terms(separator: [: ], hanging-indent: 1em, ..items.pos().map(((t, d)) => terms.item(strong(t), d)))

#let abstract-section(title, body) = block(spacing: 0.6em)[*#title.* #body]

// ---- references -------------------------------------------------------------------------
#let ref-entry(num, body) = block(
  breakable: true,
  if num == none { par(hanging-indent: 1.2em, body) } // author-year: alphabetical, unnumbered
  else { grid(columns: (1.9em, 1fr), column-gutter: 0.5em, align(right, [#num.]), body) },
)

// Back matter: smaller text on the same grid (leading grows to keep the body's pitch).
#let back-matter(body) = context {
  let size = 0.9 * _t("body-size")
  set heading(numbering: none)
  set text(size: size)
  set par(leading: _lead-for(size), spacing: _lead-for(size))
  set block(spacing: _lead-for(size))
  _line-size.update(size)
  body
}

// References: smaller type on a tighter pitch of their own (`ref-pitch` of the body's). A
// reference list is therefore off the body grid, the one place where columns can end at
// different heights. compile.py scales them down a little (`rxiv-ref-scale`, at most 10%) when
// that pulls a last page holding only a few lines back onto the page before.
#let _ref-scale = float(sys.inputs.at("rxiv-ref-scale", default: "1"))
#let references-list(body) = context {
  let size = _t("ref-size") * _ref-scale
  let lead = (_t("ref-pitch") * _pitch() - _line-box * _t("ref-size")) * _ref-scale
  set text(size: size)
  set par(justify: false, first-line-indent: 0pt, leading: lead, spacing: lead)
  set block(spacing: lead)
  body
}

// ---- helpers for the front matter --------------------------------------------------------
#let _months = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
#let _date(iso) = {
  let p = iso.split("-")
  if p.len() == 3 { [#int(p.at(2)) #_months.at(int(p.at(1)) - 1) #p.at(0)] } else { iso }
}

// ---- document ---------------------------------------------------------------------------
#let article(
  title: [],
  title-text: "",
  short-title: none,
  running-author: none,
  authors: (),
  affiliations: (),
  corresp: (),
  equal-note: none,
  author-notes: (), // ((mark, [text]), ...): present addresses, "lead contact", ...
  abstract: none,
  keywords: (),
  doi: none,
  server: "bioRxiv",
  version: none,
  posted: none,
  category: none,
  license: none,
  license-url: none,
  withdrawn: none, // (date, version, statement) when the authors withdrew the preprint
  section-numbering: true, // false when the source already numbers its sections
  paper: "us-letter",
  cite-style: none,
  theme: (:),
  appendix: (), // supplementary files appended after the article (see `_appendix`)
  tail: none, // floats after all the text: set on pages of their own after it
  body,
) = {
  let t = default-theme + theme
  if cite-style != none { t.insert("cite-style", cite-style) }
  _theme.update(t)

  set document(title: title-text, author: authors.map(a => a.name-text))
  set text(font: t.body, size: t.body-size, lang: "en", hyphenate: true)
  // Synthesized sub/superscripts throughout: the font's own glyphs exist for digits but not
  // for "-" or letters, so "ParA₂" and "ParB₁₋₂₀" would otherwise come out in two sizes.
  set sub(typographic: false)
  set super(typographic: false)
  set par(justify: t.justify, leading: 0.58em, spacing: 0.73em, first-line-indent: 1em) // front matter
  show raw: set text(font: t.mono)
  show link: it => if type(it.dest) == str { text(fill: t.accent, it) } else { it }

  // Headings: sans, bold, optionally numbered 1 / 1.1 / 1.1.1. A block heading takes whole
  // grid lines (one blank line above), with its baseline on the grid. A run-in heading is
  // body-size text opening the paragraph that follows it, so it is on the grid by construction
  // (the renderer puts no paragraph break between a subsection title and its first paragraph).
  set heading(numbering: if section-numbering { t.heading-numbering } else { none })
  show heading: it => context {
    let num = if it.numbering != none [#counter(heading).display(it.numbering)#h(0.55em)]
    let style = if it.level >= 3 { "italic" } else { "normal" }
    if it.level >= 2 and t.subheading-style == "run-in" {
      let ends-in-stop = _plain(it.body).trim().match(regex("[.?!:]$")) != none
      // Everything at body size, the trailing space included: Typst's own heading styles
      // enlarge the text, and one larger glyph would make the line taller and leave the grid.
      return text(size: t.body-size)[#text(font: t.sans, weight: "bold", style: style, hyphenate: false)[#num#it.body#if not ends-in-stop [.]] ]
    }
    let upper-case = it.level == 1 and t.heading-case == "upper"
    let hsize = if it.level == 1 { t.heading-sizes.at(0) } else { t.heading-sizes.at(1) }
    let color = if it.level == 1 and t.heading-color == "accent" { t.accent } else { black }
    let content = {
      set text(
        font: t.sans, weight: "bold", hyphenate: false, top-edge: "cap-height", bottom-edge: "baseline",
        size: hsize, style: style, fill: color, tracking: if upper-case { 0.07em } else { 0pt },
      )
      set par(justify: false, first-line-indent: 0pt, leading: 0.4em)
      [#num#if upper-case { upper(it.body) } else { it.body }]
    }
    let gap = par.spacing.to-absolute()
    // where a text line's baseline sits above its box bottom (the body's bottom-edge, -0.3em)
    let line-size = _line-size.get()
    let below-baseline = 0.3 * if line-size != none { line-size } else { t.body-size }
    block(above: gap, below: gap, sticky: true, layout(size => {
      let p = _pitch()
      let h = measure(block(width: size.width, content)).height + below-baseline
      let k = calc.max(1, calc.ceil((h + gap) / p - 0.01)) + 1
      block(width: 100%, height: k * p - gap, {
        if it.level == 1 and t.heading-rule {
          // a little under half a line above the heading's cap height (in the blank line above)
          place(bottom, dy: -(h + 0.45 * p), line(length: 100%, stroke: 0.6pt + color))
        }
        align(bottom, pad(bottom: below-baseline, content))
      })
    }))
  }
  show figure: set block(above: 1.1em, below: 1.1em)
  show figure.caption: set align(left)

  // Running elements. Page 1: no header; footer carries the review/license note.
  let short = if short-title != none { short-title } else { title-text }
  // Page numbers sit either centred in the footer or at the outer corner of the header.
  set page(
    paper: paper,
    margin: t.margin,
    background: if t.top-band != none {
      context if here().page() == 1 { place(top, rect(width: 100%, height: t.top-band, fill: t.accent)) }
    },
    header: context if here().page() > 1 {
      set text(font: t.sans, size: 7.5pt, fill: t.muted)
      let left = if withdrawn != none [#text(fill: rgb("#b03a2e"), weight: "bold")[WITHDRAWN] · #short] else { short }
      let right = running-author
      if t.folio == "header" {
        let n = text(weight: "bold", fill: black, counter(page).display())
        if calc.odd(here().page()) { right = [#right#h(1.2em)#n] } else { left = [#n#h(1.2em)#left] }
      }
      grid(columns: (1fr, auto), column-gutter: 1em, left, right)
      if t.header-rule != none {
        v(-0.35em)
        line(length: 100%, stroke: t.header-rule)
      }
    },
    footer: context {
      set text(font: t.sans, size: 7.5pt, fill: t.muted)
      set align(center)
      if here().page() == 1 {
        [This preprint has not been peer reviewed.]
        if license != none [ License: #if license-url != none { link(license-url, license) } else { license }.]
        if t.folio == "footer" { linebreak() }
      }
      if t.folio == "footer" { counter(page).display() }
    },
  )

  // ---- front matter (full width) ----
  if withdrawn != none {
    block(width: 100%, inset: (x: 9pt, y: 7pt), below: 0.9em, fill: rgb("#fbeae8"), stroke: (left: 3pt + rgb("#b03a2e")), {
      set text(font: t.sans, size: 8.5pt)
      set par(justify: false, first-line-indent: 0pt)
      text(weight: "bold", fill: rgb("#b03a2e"))[WITHDRAWN]
      [ — the authors withdrew this preprint on #_date(withdrawn.date) (v#withdrawn.version). The version shown here is kept for reference only.]
      if withdrawn.statement != [] { parbreak(); emph(withdrawn.statement) }
    })
  }
  {
    set text(font: t.sans, size: 8pt, fill: t.muted)
    set par(first-line-indent: 0pt, justify: false)
    let bits = (text(weight: "semibold", fill: t.accent)[#server preprint],)
    if category != none and category != "" { bits.push(upper(category.first()) + category.slice(1)) }
    if posted != none { bits.push([posted #_date(posted)]) }
    if version != none { bits.push([v#version]) }
    if doi != none { bits.push(link("https://doi.org/" + doi)[doi.org/#doi]) }
    bits.join([ #sym.dot.c ])
  }
  v(0.9em)
  block({
    set par(justify: false, first-line-indent: 0pt, leading: 0.32em)
    let font = if t.title-font == "body" { t.body } else { t.sans }
    text(font: font, size: t.title-size, weight: t.title-weight, hyphenate: false, title)
  })
  v(0.5em)
  {
    set text(font: t.sans, size: t.author-size)
    set par(justify: false, first-line-indent: 0pt, leading: 0.5em)
    authors.map(a => {
      let marks = a.affs.map(str)
      if a.equal { marks.push("†") }
      if a.corresponding { marks.push("*") }
      marks += a.at("notes", default: ())
      box[#a.name#if marks.len() > 0 { super(marks.join(",")) }]
    }).join([, ])
  }
  v(0.45em)
  {
    set text(size: t.affil-size)
    set par(spacing: 0.3em, leading: 0.42em, justify: false, first-line-indent: 0pt)
    // Run-on style, and consortium-size author lists: affiliations as one compact paragraph.
    if authors.len() > 40 or t.affil-style == "run-on" {
      affiliations.enumerate().map(((i, aff)) => [#super(str(i + 1))#aff]).join([; ])
      linebreak()
    } else {
      for (i, aff) in affiliations.enumerate() [#super(str(i + 1))#aff \ ]
    }
    if equal-note != none [#super[†]#equal-note \ ]
    for c in corresp [#super[\*]#c \ ]
    for (mark, note) in author-notes [#super(mark)#note \ ]
  }

  if abstract != none and t.abstract-style == "plain" {
    v(0.6em)
    block(width: 100%, below: 0.4em, {
      set par(justify: t.justify, first-line-indent: 0pt, leading: 0.52em, spacing: 0.6em)
      set text(size: t.abstract-size, weight: "bold")
      abstract
      if keywords.len() > 0 {
        parbreak()
        text(font: t.sans, size: 8pt, weight: "regular")[*Keywords:* #keywords.join(", ")]
      }
    })
    line(length: 100%, stroke: 0.5pt + luma(150))
  } else if abstract != none and t.abstract-style == "bar" {
    v(0.8em)
    block(width: 100%, inset: (left: 11pt, y: 2pt), stroke: (left: 2.5pt + t.accent), {
      set par(justify: t.justify, first-line-indent: 0pt, leading: 0.55em, spacing: 0.65em)
      set text(size: t.abstract-size)
      text(font: t.sans, size: 7.5pt, weight: "bold", tracking: 0.1em, fill: t.accent, upper[Abstract])
      parbreak()
      abstract
      if keywords.len() > 0 {
        parbreak()
        text(font: t.sans, size: 8pt)[#text(weight: "bold", fill: t.accent)[Keywords] #h(0.3em) #keywords.join(", ")]
      }
    })
  } else if abstract != none {
    v(0.5em)
    block(
      width: 100%, inset: (x: 9pt, y: 8pt), fill: t.accent.lighten(94%),
      stroke: (top: 1pt + t.accent, bottom: 0.5pt + t.accent),
      {
        set par(justify: t.justify, first-line-indent: 0pt, leading: 0.52em, spacing: 0.6em)
        set text(size: t.abstract-size)
        text(font: t.sans, weight: "bold", fill: t.accent)[Abstract]
        parbreak()
        abstract
        if keywords.len() > 0 {
          parbreak()
          text(font: t.sans, size: 8pt)[*Keywords:* #keywords.join(", ")]
        }
      },
    )
  }
  v(1em)

  // ---- body (two columns, on the baseline grid) ----
  let lead = t.body-size * t.line-pitch - _line-box * t.body-size // = _lead-for(body-size)
  let column-flow(content) = columns(2, gutter: t.gutter, {
    set text(top-edge: 0.8em, bottom-edge: -0.3em, costs: (widow: 0%, orphan: 0%))
    set par(leading: lead, spacing: lead, justify: t.justify)
    set block(spacing: lead)
    set list(spacing: lead)
    set enum(spacing: lead)
    set terms(spacing: lead)
    set place(clearance: t.body-size * t.line-pitch) // _float pads float + clearance to the grid
    show raw.where(block: true): it => _snap(it, at: top)
    _line-size.update(t.body-size)
    content
  })
  column-flow({
    body
    _end-probe()
    _balance-spacer()
  })
  // Figures and tables that follow all the text (supplementary figures after the references,
  // tables deferred to the end): anchored in the text they would go to the tops of its last
  // pages and push its end (the last references) under or past them. They start a fresh page.
  if tail != none {
    pagebreak(weak: true)
    column-flow(tail)
  }
  _appendix(appendix, t)
}
