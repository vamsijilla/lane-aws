# Baselines

`v1.db` is the full database after the v1 extraction pipeline ran on the Cobalt Mesa sample,
frozen at the `v1-baseline` tag. It is gitignored (it holds page images and model calls).
The extraction v1-vs-v2 report reads it; tests load the much smaller fixture slice in
`backend/tests/fixtures/sample_submission.json` instead.
