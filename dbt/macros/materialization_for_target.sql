{#
  ADR-002: the one place the duckdb/athena targets diverge. duckdb builds a plain table
  (no Iceberg/merge support); athena builds an incremental Iceberg merge. Every model
  configures itself with `{{ config(**materialization_for_target(unique_key=...)) }}`
  rather than branching on target.type itself.
#}
{% macro materialization_for_target(unique_key=none) %}
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
{% endmacro %}
