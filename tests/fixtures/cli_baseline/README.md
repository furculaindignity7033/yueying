# CLI baseline (captured at v0.1.3, before the 0.2.0 refactor)

Captured on 2026-09-09 with:

    python -m yueying.cli <REPO>/test-media/test.mp4    --no-asr --out <OUT> --json   -> test/
    python -m yueying.cli <REPO>/test-media/test_zh.mp4 --no-asr --out <OUT> --json   -> test_zh/

Files per folder: report.md, transcript.txt, transcript.srt, manifest.json (0.1.x schema),
json_line.txt (the `--json` stdout line), log.txt (the Chinese progress log lines).

Placeholders: `<OUT>` = the absolute output folder given to `--out`, `<REPO>` = the repo root.
`tests/test_cli_compat.py` asserts the 0.2.0 CLI still produces byte-identical
report.md / transcript.* / `--json` line after the same normalisation.
