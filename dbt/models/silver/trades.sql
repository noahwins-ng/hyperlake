{{ config(**materialization_for_target(kind='merge', unique_key='tid')) }}

-- G5 (PRD): silver is a governed contract, not an inferred shape -- every returned
-- column must be declared with its type in _silver.yml (QNT-464 AC1). `on_schema_change:
-- fail` so a dropped/retyped/reordered column breaks the build loudly instead of the
-- incremental merge silently drifting from the contract.
{{ config(contract={'enforced': true}, on_schema_change='fail') }}

{% if target.type == 'athena' %}
{{ config(
    partitioned_by=['coin', "day(time)"],
    incremental_predicates=[
        "target.time >= cast(date_add('day', -" ~ var('silver_lookback_days') ~ ", current_date) as timestamp)"
    ],
    merge_exclude_columns=['first_seen_source'],
    update_condition='src.source_rank >= target.source_rank',
) }}
{% endif %}

-- Source-side pre-dedup runs on both targets: an Athena MERGE errors if `src` matches a
-- target row more than once, and the incoming batch re-reads the whole lookback window on
-- every run, so it commonly holds more than one row per `tid` (a prior ws row plus a later
-- backfill row for the same trade). ADR-005: backfill outranks ws (source_rank); ties break
-- on the most recently ingested row. Select body: macros/silver_trades_select.sql (shared
-- with the Athena seam test, QNT-462, so both run the identical merge logic).
{% set bronze_source %}
    (
        select *
        from {{ source('bronze', 'trades_raw') }}
        {% if target.type == 'athena' %}
        where dt >= date_add('day', -{{ var('silver_lookback_days') }}, current_date)
        {% endif %}
    )
{% endset %}

{{ silver_trades_select(bronze_source) }}
