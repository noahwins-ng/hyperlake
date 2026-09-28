{#
  Teardown for seam-pr.yml's per-PR schema (ADR-002 amendment): drops every table in
  `schema` through adapter.drop_relation (Glue entry and its S3 files under warehouse/),
  then the schema itself. A plain `drop schema ... cascade` would remove only the Glue
  entries and leave the Iceberg files behind. Refuses anything outside the seam_test_pr
  prefix so it can never be pointed at silver/gold; a missing schema (a run cancelled
  before the seed step) is a no-op.
#}
{% macro drop_seam_schema(schema) %}
  {% if not schema.startswith('seam_test_pr') %}
    {{ exceptions.raise_compiler_error("drop_seam_schema: refusing to drop '" ~ schema ~ "', only seam_test_pr* schemas") }}
  {% endif %}
  {% if not adapter.check_schema_exists(target.database, schema) %}
    {{ log("drop_seam_schema: " ~ schema ~ " does not exist, nothing to drop", info=true) }}
    {{ return(none) }}
  {% endif %}
  {% set schema_relation = api.Relation.create(database=target.database, schema=schema) %}
  {% for relation in adapter.list_relations_without_caching(schema_relation) %}
    {{ log("drop_seam_schema: dropping " ~ relation, info=true) }}
    {% do adapter.drop_relation(relation) %}
  {% endfor %}
  {% do adapter.drop_schema(schema_relation) %}
  {{ log("drop_seam_schema: dropped " ~ schema, info=true) }}
{% endmacro %}
