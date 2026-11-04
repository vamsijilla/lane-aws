#!/usr/bin/env bash
# Copy the local model cache into RDS, so the sample replays on AWS for $0 with the same result.
#   deploy/copy-cache.sh [source database url]
# The source is the local rehearsal's database by default: the rehearsal runs the same Linux
# arm64 images as the EC2, so every page image, and so every cache key, matches. (The Mac's own
# database would miss on every PDF page: macOS and Linux encode page images differently.)
# Copies only cached model answers (and the calls they came from, with their run ids cleared),
# cached OCR reads and confirmed layouts. No submissions. Rows already there are kept.
source "$(dirname "$0")/lib.sh"
cd "$DEPLOY_DIR"
load_outputs

src="${1:-postgresql://lane:lane@127.0.0.1:5434/lane}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

say "Reading the cache from $src"
psql "$src" -v ON_ERROR_STOP=1 -q <<EOF
\copy (select * from llm_calls where id in (select source_call_id from llm_cache)) to '$work/llm_calls.csv' csv
\copy (select * from llm_cache) to '$work/llm_cache.csv' csv
\copy (select * from ocr_cache) to '$work/ocr_cache.csv' csv
\copy (select * from layouts) to '$work/layouts.csv' csv
EOF
wc -l "$work"/*.csv

cat > "$work/load.sql" <<'EOF'
\set ON_ERROR_STOP on
begin;
create temp table t_llm_calls (like llm_calls including defaults) on commit drop;
create temp table t_llm_cache (like llm_cache including defaults) on commit drop;
create temp table t_ocr_cache (like ocr_cache including defaults) on commit drop;
create temp table t_layouts (like layouts including defaults) on commit drop;
\copy t_llm_calls from 'llm_calls.csv' csv
\copy t_llm_cache from 'llm_cache.csv' csv
\copy t_ocr_cache from 'ocr_cache.csv' csv
\copy t_layouts from 'layouts.csv' csv
-- The runs these calls belonged to are not copied
update t_llm_calls set run_id = null;
insert into llm_calls select * from t_llm_calls on conflict do nothing;
insert into llm_cache select * from t_llm_cache on conflict do nothing;
insert into ocr_cache select * from t_ocr_cache on conflict do nothing;
insert into layouts select * from t_layouts on conflict do nothing;
select 'llm_cache', count(*) from llm_cache
union all select 'ocr_cache', count(*) from ocr_cache
union all select 'layouts', count(*) from layouts;
commit;
EOF

handoff="s3://$LANE_OUT_BucketName/deploy/cache"
aws s3 cp --quiet "$work/" "$handoff/" --recursive

say "Loading into RDS"
on_host "set -e
mkdir -p /opt/lane/cache && cd /opt/lane/cache
aws s3 cp --quiet $handoff/ . --recursive --region $REGION
aws s3 rm --quiet $handoff/ --recursive --region $REGION
url=\$(sed -n 's/^LANE_DATABASE_URL=postgresql+psycopg:/postgresql:/p' /opt/lane/.env)
docker run --rm -v /opt/lane/cache:/cache -w /cache postgres:16-alpine psql \"\$url\" -q -f load.sql
cd / && rm -rf /opt/lane/cache"
say "Cache copied"
