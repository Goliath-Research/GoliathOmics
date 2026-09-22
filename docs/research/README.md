# Research notes

Informal background research used during pipeline design (analyte choice, MCED landscape, buffy-coat vs cfDNA tradeoffs). These are **not** canonical user or operator documentation; see [ANALYTE_PROFILES.md](../ANALYTE_PROFILES.md) and the user manual for project-facing guidance.

| Note | Topic |
|------|--------|
| [BuffyCoat_vs_cfDNA_for_Cancer_Detection.md](BuffyCoat_vs_cfDNA_for_Cancer_Detection.md) | Buffy vs cfDNA as MethylPipeline analytes; CHIP/paired designs; synthesizes Gemini + Grok landscape notes |
| [omega-cluster-detection.md](omega-cluster-detection.md) | Train-healthy Ω clustering → matched-stratum vs all-pairs detection; Buffy results + DomainProgram gate |
| [buffy-mvalue-residualization.md](buffy-mvalue-residualization.md) | Train-only M-value residualization for confounder-aware buffy DMPs; isolation contract, leakage table, Ω caveat |
| [Prostate Cancer Detection.md](Prostate Cancer Detection.md) | Gatekeeper / csPCa clinical framing and wet-lab SOW (source note) |
| [Prostate_Cancer_Detection_MethylPipeline_Fitness.md](Prostate_Cancer_Detection_MethylPipeline_Fitness.md) | Fitness analysis of that note vs MethylPipeline (adopt-with-caveats; extensions) |
| [Prostate_Cancer_Application_Deep_Dive.md](Prostate_Cancer_Application_Deep_Dive.md) | Application synthesis: buffy/plasma WGBS, EM-Seq GRAIL-style gatekeeper, alignment compare gate, informME/deconv, physician/payer validation story. Operator/researcher options: [usage ch.25](../usage/25-prostate-cancer-pack.md) |
| [Wong2026_EMSeq_mCRPC_MethylPipeline_Gaps.md](Wong2026_EMSeq_mCRPC_MethylPipeline_Gaps.md) | Wong et al. 2026 (npj Precis Oncol): targeted EM-Seq + MHB/MHL mCRPC survival vs shipped `cfdna_emseq_targeted` — what blocks a repeat |
| [florida-community-access-partnership-brief.md](florida-community-access-partnership-brief.md) | External Florida community-access brief (UF/Sylvester HPC + AdventHealth; not Moffitt OCI) |
| [florida-hub-partnership-assessment.md](florida-hub-partnership-assessment.md) | **Internal:** why Moffitt is the wrong IT partner; Winter Haven hub; UF / Sylvester / AdventHealth targeting |
| [GoliathOmics Gaps.md](GoliathOmics Gaps.md) | Completeness vs Wang/Moffitt techniques: cfDNA MHL, clinico-genomic scores, exosomal miRNA/protein, long-read ASM |
| [Gemini_on_Cancer_Detection.md](Gemini_on_Cancer_Detection.md) | MCED competitors, multiomics platforms, buffy-coat as CHIP filter |
| [Grok_on_Gemini_conclusions.md](Grok_on_Gemini_conclusions.md) | Verification of Gemini MCED landscape note; performance and regulatory caveats |
| [methylpipeline_informme_integration.md](methylpipeline_informme_integration.md) | Using informME information-theoretic measures (Ising NME/JSD) in MethylPipeline; read-level data gap and integration options |
