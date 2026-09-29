# Seed companies

`companies.csv` is the fixed set of companies the pipeline ingests (138 on Companies House).

- **ftse100**: FTSE 100 constituents, approximately as of 2026. Ten are incorporated outside
  the UK (Jersey, Switzerland, Spain, …) and are kept as `not_uk_registered` rows so that is
  visible rather than silently missing.
- **notable**: well-known private, recently delisted, and failed companies (Wilko, Cazoo,
  BrewDog, Thames Water). These give the agent insolvency and ownership questions to answer.

`resolution` column:
| value | meaning |
|---|---|
| `exact` | normalised name matched a search result exactly |
| `manual` | verified by hand via the company's `previous_company_names`; reason in `note` |
| `not_uk_registered` | not on Companies House |

**Caveat learned the hard way:** an exact *current* name match can be the wrong company.
"WILKO LIMITED" today is The Range's entity; the retailer that collapsed in 2023 is
00365335, now "WL REALISATIONS (2023) LIMITED". Check `previous_company_names` for
companies known to have failed or been acquired.

Regenerate (keeps `manual` and `exact` rows): `uv run python scripts/resolve_seed.py`
