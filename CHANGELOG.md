# Changelog

## 0.1.0 (2026-11-01)

First release.

- Builds a two-column, journal-style PDF of a bioRxiv or medRxiv preprint from its JATS XML,
  with three themes (`default`, `compact`, `sans`) and US Letter or A4 paper.
- Figures and tables are placed after their first citation and sized by their content: table
  screenshots by the height of their text, figures by their lettering. Long tables and the
  figures that follow all the text get pages of their own; the last page's columns are balanced.
- Citations, references with DOI links, equations, back matter (including statements bioRxiv
  keeps only in the front matter), author notes, and a banner on withdrawn preprints.
- Supplementary files are listed and linked; `--supplements` downloads them and
  `--append-supplements` appends the PDFs and images to the end of the PDF.
- Batch mode (`--list`), a download cache that resumes interrupted runs, and polite
  rate-limit handling for bioRxiv's servers.
- Repairs common defects of the source XML (false citation links, doubly escaped characters,
  missing spaces after code) and reports each kind as a `fixed:` line.
