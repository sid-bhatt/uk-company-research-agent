# Companies House fixtures

Real responses for GREGGS PLC (00502851), recorded from the live API. Officers'
dates of birth and addresses are stripped by the recorder.

Re-record (needs `COMPANIES_HOUSE_API_KEY` in `.env`):

    cd backend && uv run python scripts/record_ch_fixtures.py

Tests assert on stable fields and structure, so re-recording should not break them.
