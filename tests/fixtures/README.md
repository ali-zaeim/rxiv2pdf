# bioRxiv/medRxiv fixtures

Real responses saved from the bioRxiv/medRxiv API (`api.json`, the `/details` record) and the
preprints' JATS XML (`vN.source.xml`), used by the parser snapshots and the fake server in
`tests/conftest.py`. Each preprint is redistributed here unmodified, as test data, under its own
license, with credit to its authors. Only openly licensed preprints are used; the CC BY-NC-ND ones
are shared verbatim and non-commercially, as that license allows.

| Directory | Preprint | Server | DOI | License |
|---|---|---|---|---|
| `2020.03.22.002386` | Gordon *et al.*, "A SARS-CoV-2-Human Protein-Protein Interaction Map Reveals Drug Targets and Potential Drug-Repurposing" | bioRxiv | [10.1101/2020.03.22.002386](https://doi.org/10.1101/2020.03.22.002386) | CC BY 4.0 |
| `2024.04.17.590000` | Howard *et al.*, "Molecular basis of proton-sensing by G protein-coupled receptors" | bioRxiv | [10.1101/2024.04.17.590000](https://doi.org/10.1101/2024.04.17.590000) | CC BY-NC-ND 4.0 |
| `2025.11.24.687809` | van Haren *et al.*, "Distinct Roles of Deep and Superficial Cortical Layers in Tone Prediction, Comparison, and Adaptation in Human Auditory Cortex" | bioRxiv | [10.1101/2025.11.24.687809](https://doi.org/10.1101/2025.11.24.687809) | CC BY 4.0 |
| `2026.09.25.26364016` | Serman *et al.*, "Age-Stratified Patterns of Diagnostic Delay in Pertussis" | medRxiv | [10.64898/2026.09.25.26364016](https://doi.org/10.64898/2026.09.25.26364016) | CC BY 4.0 |
| `2026.09.25.754430` | Wong *et al.*, "Cross-source proteogenomic profiling of human microglia reveals a conserved core membrane protein and HERV signature" | bioRxiv | [10.64898/2026.09.25.754430](https://doi.org/10.64898/2026.09.25.754430) | CC BY-NC-ND 4.0 |
| `2026.09.29.755279` | Gunduz *et al.*, "methylTFR: Computational quantification of transcription factor activity from DNA methylation" | bioRxiv | [10.64898/2026.09.29.755279](https://doi.org/10.64898/2026.09.29.755279) | CC BY 4.0 |
| `2026.10.02.26364566` | Chen *et al.*, "Prediction of in-hospital mortality at a 72-hour intensive care landmark in patients with sepsis and pre-existing hypertension: development and external validation" | medRxiv | [10.64898/2026.10.02.26364566](https://doi.org/10.64898/2026.10.02.26364566) | CC BY 4.0 |
| `2026.10.06.756929` | Wedler *et al.*, "Cell Painting PLUS reveals phenotypic signatures for nephrotoxicity profiling in human RPTEC/TERT1" | bioRxiv | [10.64898/2026.10.06.756929](https://doi.org/10.64898/2026.10.06.756929) | CC BY 4.0 |
| `2026.10.16.759679` | Ghosh *et al.*, "Astrocytic sEH-EET signaling regulates microglial lysosomal function and suppresses the STING-Type I interferon pathway in Alzheimer's disease" | bioRxiv | [10.64898/2026.10.16.759679](https://doi.org/10.64898/2026.10.16.759679) | CC BY 4.0 |
| `2026.10.22.760766` | Shi *et al.*, "Fine-tuning an intracellular allosteric pocket programs GPCR signaling bias" | bioRxiv | [10.64898/2026.10.22.760766](https://doi.org/10.64898/2026.10.22.760766) | CC BY-NC-ND 4.0 |

`api_no_posts_found.json` is the API's answer for a DOI it doesn't know. The XML tables and
MathML in `external/` come from journal articles; see `external/README.md`.
