# Layer 4 Federated Data Fusion Packs

This folder contains starter sample datasets for production connector onboarding.

## Source files
- `layer4_secc.sample.json`
- `layer4_census.sample.csv`
- `layer4_nfhs.sample.json`
- `layer4_sdg_india_index.sample.json`
- `layer4_niti_mpi.sample.json`
- `layer4_aspirational_districts.sample.json`
- `layer4_pm_gati_shakti.sample.json`
- `layer4_budget_outlays.sample.json`

## Runtime env mapping
- `L4_SECC_DATA_PATH`
- `L4_CENSUS_DATA_PATH`
- `L4_NFHS_DATA_PATH`
- `L4_SDG_DATA_PATH`
- `L4_NITI_MPI_DATA_PATH`
- `L4_ASPIRATIONAL_DATA_PATH`
- `L4_GATI_SHAKTI_DATA_PATH`
- `L4_BUDGET_OUTLAY_DATA_PATH`

Use `/api/v1/layer4/fusion/sources` to check readiness and `/api/v1/layer4/fusion` to verify governed joins.
