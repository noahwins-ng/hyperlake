{#
  ADR-002: the one place the duckdb/athena targets diverge. `kind='merge'` (default): duckdb
  builds a plain table (no Iceberg/merge support), athena builds an incremental Iceberg
  merge. `kind='table'`: a plain table on both targets (Iceberg on athena, for gold/recon,
  which have no merge/dedup concern of their own). Every model configures
  itself with `{{ config(**materialization_for_target(kind=..., unique_key=...)) }}` rather
  than branching on target.type itself.
#}
{% macro materialization_for_target(kind='merge', unique_key=none) %}
  {% if kind == 'table' %}
    {% if target.type == 'duckdb' %}
      {{ return({'materialized': 'table'}) }}
    {% elif target.type == 'athena' %}
      {{ return({'materialized': 'table', 'table_type': 'iceberg'}) }}
    {% else %}
      {{ exceptions.raise_compiler_error('materialization_for_target: unsupported target.type ' ~ target.type) }}
    {% endif %}
  {% elif kind == 'merge' %}
    {% if target.type == 'duckdb' %}
      {{ return({'materialized': 'table'}) }}
    {% elif target.type == 'athena' %}
      {{ return({
          'materialized': 'incremental',
          'incremental_strategy': 'merge',
          'table_type': 'iceberg',
          'unique_key': unique_key,
      }) }}
    {% else %}
      {{ exceptions.raise_compiler_error('materialization_for_target: unsupported target.type ' ~ target.type) }}
    {% endif %}
  {% else %}
    {{ exceptions.raise_compiler_error('materialization_for_target: unsupported kind ' ~ kind) }}
  {% endif %}
{% endmacro %}
