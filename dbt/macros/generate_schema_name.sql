{#
  QNT-462: standard dbt override so a model's `schema:` config resolves to exactly that
  name (dbt's default behavior prefixes it with the target schema, e.g. `silver_seam_test`
  instead of `seam_test`) -- ADR-002's amendment names the seam schema literally.
#}
{% macro generate_schema_name(custom_schema_name, node) %}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{% endmacro %}
