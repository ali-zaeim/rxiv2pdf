// "sans": a modern open-access look. Teal accent band, large sans title, ragged-right sans
// body with generous spacing, abstract marked by an accent bar, ruled unnumbered headings,
// accent caption labels, bracketed numeric citations, page numbers in the header.
#let accent = rgb("#0b6e69")
#let theme = (
  body: ("Source Sans 3", "Libertinus Serif"),
  accent: accent,
  body-size: 9.5pt,
  line-pitch: 1.32,
  justify: false,
  gutter: 7mm,
  margin: (x: 19mm, top: 22mm, bottom: 20mm),
  top-band: 6mm,
  title-size: 23pt,
  title-weight: "semibold",
  author-size: 10pt,
  abstract-style: "bar",
  abstract-size: 9.5pt,
  heading-numbering: none,
  heading-color: "accent",
  heading-sizes: (11.5pt, 9.5pt),
  heading-rule: true,
  caption-label-color: "accent",
  folio: "header",
  header-rule: 0.8pt + accent,
  cite-style: "brackets",
)
