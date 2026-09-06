# User-study analysis snapshot — N=10 (2026-09-06)

This folder is a **versioned interim snapshot** of the CHI user-study analysis.

- Formal statistics in this snapshot use **U01–U10 only**.
- U0 and U11 were incomplete at the time of this snapshot.
- Previously generated supervisor-preview mock questionnaires are **not human-subject data and must never be included in formal analysis**.
- When additional real participants are collected, rerun `analyze_user_study_results.py` with an explicit `--include-ids` list and regenerate the tables/figures.
- Study A compares Direct Generation with the Editable Performance Plan workflow.
- Study B compares Dialogue-Only with Full-Context plans; anonymous A/B order must be decoded with the assignment manifest.
- The collected Study A workflow order was Direct Generation followed by Editable Performance Plan, so workflow contrasts can contain an order/practice effect.

`results_for_paper.md` and `paper_user_study_update.tex` are interim N=10 writing snapshots and must be regenerated after the final sample is collected.

The spreadsheet workbook and rendered PNG figures are stored with the Google Drive snapshot; GitHub keeps the analysis code and aggregate outputs so the analysis remains diffable and reproducible.
