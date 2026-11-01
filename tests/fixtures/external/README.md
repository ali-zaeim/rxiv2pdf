# External JATS fixtures (XML tables and MathML)

bioRxiv/medRxiv ship every table and equation as an image, so the XML-table and MathML code
paths are tested against articles from journals that publish native JATS. Full-text XML was
retrieved from the Europe PMC REST API (`/webservices/rest/{PMCID}/fullTextXML`) on 2026-10-31.
All ten articles are open access under CC BY 4.0; they are redistributed here unmodified, as
test data, with credit to their authors:

| File | Article | Journal | DOI | Exercises |
|---|---|---|---|---|
| `PMC12068631` | Qu *et al.*, "Mathematical modeling of malaria vaccination with seasonality and immune feedback" | PLoS Comput Biol | [10.1371/journal.pcbi.1012988](https://doi.org/10.1371/journal.pcbi.1012988) | 377 MathML formulas, 3 XML tables (2 spanning cells) |
| `PMC12370208` | Gill *et al.*, "Mechanistic computational modeling of sFLT1 secretion dynamics" | PLoS Comput Biol | [10.1371/journal.pcbi.1013324](https://doi.org/10.1371/journal.pcbi.1013324) | 310 MathML formulas, 4 XML tables (9 spanning cells) |
| `PMC12204626` | Browning *et al.*, "Identifiability of phenotypic adaptation from low-cell-count experiments and a stochastic model" | PLoS Comput Biol | [10.1371/journal.pcbi.1013202](https://doi.org/10.1371/journal.pcbi.1013202) | 155 MathML formulas |
| `PMC11318869` | Verhellen *et al.*, "Multitask learning of a biophysically-detailed neuron model" | PLoS Comput Biol | [10.1371/journal.pcbi.1011728](https://doi.org/10.1371/journal.pcbi.1011728) | 7 MathML formulas, 1 XML table |
| `PMC11706453` | Jo *et al.*, "Estimating the distribution of parameters in differential equations with repeated cross-sectional data" | PLoS Comput Biol | [10.1371/journal.pcbi.1012696](https://doi.org/10.1371/journal.pcbi.1012696) | 45 MathML formulas, 3 XML tables (32 spanning cells) |
| `PMC11657209` | Westergaard *et al.*, "Immune changes in pregnancy: associations with pre-existing conditions and obstetrical complications at the 20th gestational week—a prospective cohort study" | BMC Med | [10.1186/s12916-024-03797-y](https://doi.org/10.1186/s12916-024-03797-y) | 13 MathML formulas, 13 TeX, 2 XML tables (6 spanning cells) |
| `PMC12625201` | Liu *et al.*, "Sodium-glucose transport protein 2 inhibitor use in diabetes and its association with tuberculosis incidence" | BMC Med | [10.1186/s12916-025-04460-w](https://doi.org/10.1186/s12916-025-04460-w) | 3 XML tables (29 spanning cells) |
| `PMC12306065` | Xiong *et al.*, "Adjuvant therapeutic efficacy of low-dose aspirin on short-term outcomes of patients with cancer-associated venous thromboembolism" | BMC Med | [10.1186/s12916-025-04284-8](https://doi.org/10.1186/s12916-025-04284-8) | 3 XML tables (13 spanning cells) |
| `PMC12459853` | Ho *et al.*, "Reclaiming wellness: Key factors in restoring optimal well-being in the Canadian Longitudinal Study on Aging" | PLoS One | [10.1371/journal.pone.0329800](https://doi.org/10.1371/journal.pone.0329800) | 4 XML tables (57 spanning cells) |
| `PMC12742746` | Salimi Arshad Moghaddam Pishkhani *et al.*, "Comparing the effectiveness of emotion regulation therapy and cognitive behavioral therapy on treatment adherence in hemodialysis patients: A randomized controlled clinical trial" | PLoS One | [10.1371/journal.pone.0339162](https://doi.org/10.1371/journal.pone.0339162) | 2 MathML formulas, 4 XML tables (23 spanning cells) |
