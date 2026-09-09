{{ config(schema='seam_test', **materialization_for_target(kind='merge', unique_key='tid')) }}

{% if target.type == 'athena' %}
{{ config(
    merge_exclude_columns=['first_seen_source'],
    update_condition='src.source_rank >= target.source_rank',
) }}
{% endif %}

-- ADR-002 amendment (QNT-462): a `seam_test.trades` instance of silver's real merge
-- (macros/silver_trades_select.sql), run twice per dbt-run.yml `seam` job invocation
-- against two fixture seeds picked by `var('seam_round')` -- round 1 resets to
-- seam_fixture_round1's values (a plain create on the very first-ever run, an idempotent
-- MERGE back to those values on every later one), round 2 is the MERGE that exercises the
-- three ordering cases (late feed duplicate, backfill-after-feed, feed-after-backfill).
-- No dt lookback here: the fixture is a handful of rows, so every run scans the whole
-- (tiny) source and target -- AC4's <10MB bound, not partition pruning.
{{ silver_trades_select(ref('seam_fixture_round' ~ var('seam_round', 1))) }}
