# ADR-003: OCR is required for filing documents

- **Status:** Accepted
- **Date:** 2026-09-28
- **Supersedes:** "skip scanned PDFs in v1" (PLAN decision #8)

## Context
The plan assumed most filing PDFs would have a text layer and scanned ones could be skipped.
Measured on the seed corpus (Day 3):

| Check | Result |
|---|---|
| Queued annual-accounts documents (last 3 years) | 470 (84,386 pages, avg 180) |
| Offered as iXBRL (machine-readable) | **1 / 470** |
| Text layer present (6 sampled recent reports: Tesco, Greggs, Monzo, Revolut, Thames Water, BrewDog) | **0 / 120 pages** |

Companies House serves filings as page images, so large companies' annual reports have no
extractable text. Skipping image-only PDFs would skip essentially the whole corpus.

## Options
| Option | Cost for 84k pages | Quality | Notes |
|---|---|---|---|
| **Tesseract (local / in container)** | £0; ~3 h on a 10-core laptop (7.3 pages/s measured) | Good on prose; headers garbled; table numbers occasionally corrupted | Runs anywhere; deterministic; open source |
| AWS Textract DetectDocumentText | ~$125 | Better than Tesseract | Blows the $50/month budget; good AWS story |
| Textract AnalyzeDocument (tables) | ~$1,250+ | Best for tables | Out of budget |
| Vision LLM per page | Highest | Very good | Out of budget at this scale |

## Decision
- **Tesseract via PyMuPDF** (`get_textpage_ocr`, 200 dpi, parallel per page) for all documents.
- Order: the **latest report per company first** (136 docs, 25k pages, ~1 h), check quality,
  then backfill the rest. It is the same idempotent command re-run.
- Store OCR text **per page** so citations stay page-accurate and OCR never needs re-running
  when chunking or embeddings change.
- Strip running headers and footers that repeat across pages before chunking.

## Consequences
- **Numeric answers are the risk.** The golden set will include figure questions (turnover,
  profit, headcount) scored separately, so OCR error is measured instead of hidden.
- If figure accuracy is poor, the upgrade path is targeted: re-OCR only the financial statement
  pages with Textract or a vision model. That's a few pages per report, not 84k.
- The container image needs `tesseract-ocr` (apt) for the ECS ingestion task.
