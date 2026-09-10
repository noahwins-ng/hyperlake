# Column schema comes from the shared package (src/hyperlake/envelope.py), not typed here
# twice — `data.external` calls the same `glue_columns()` function the unit tests pin
# (tests/test_envelope.py::test_glue_columns_cover_every_field_with_a_glue_type).
data "external" "envelope_schema" {
  program = [
    "uv", "run", "--project", "${path.module}/../../..",
    "python", "-c",
    "import json\nfrom hyperlake.envelope import glue_columns\nprint(json.dumps({'columns': json.dumps(glue_columns())}))"
  ]
}

locals {
  envelope_columns = jsondecode(data.external.envelope_schema.result.columns)

  # coin and source are envelope columns but become Glue partition keys, so they're
  # excluded from the table's regular column list (Athena/Glue reject a column that's
  # both a partition key and a data column).
  table_columns = [for c in local.envelope_columns : c if !contains(["coin", "source"], c.Name)]

  # Single source of truth for the watchlist (CLAUDE.md) -- no market list typed into
  # .tf. Colon normalisation mirrors hyperlake.partitions.coin_partition_value; HCL can't
  # call that Python helper directly, so the one-line replace() here is this stack's only
  # normalisation call site.
  watchlist_markets     = yamldecode(file("${path.module}/../../../config/watchlist.yaml"))["markets"]
  coin_partition_values = [for m in local.watchlist_markets : replace(m, ":", "_")]
}

resource "aws_glue_catalog_database" "bronze" {
  name = "bronze"
}

# Written only by dbt-athena (ADR-002/CLAUDE.md: Iceberg exists only at silver/gold, no other
# writer). Declared here anyway, not left hand-created, per "everything is Terraform" -- it
# already existed live (created by an earlier developer-credentialed `dbt build --target
# athena` run) and was imported into this state rather than recreated (QNT-473).
resource "aws_glue_catalog_database" "silver" {
  name = "silver"
}

# Auto-created by dbt-athena on the seam job's first run (QNT-462, ADR-002 amendment) --
# same hand-created-then-imported story as `silver` above, not a fresh mistake.
resource "aws_glue_catalog_database" "seam_test" {
  name = "seam_test"
}

# Partition projection (no crawler, no MSCK REPAIR): new objects under bronze/ are
# queryable the moment they land (PRD Landing -> Partition registration).
resource "aws_glue_catalog_table" "trades_raw" {
  name          = "trades_raw"
  database_name = aws_glue_catalog_database.bronze.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                      = "TRUE"
    "parquet.compression"         = "SNAPPY"
    "projection.enabled"          = "true"
    "projection.coin.type"        = "enum"
    "projection.coin.values"      = join(",", local.coin_partition_values)
    "projection.dt.type"          = "date"
    "projection.dt.range"         = "2025-07-27,NOW"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
    "projection.source.type"      = "enum"
    "projection.source.values"    = "ws,backfill"
    "storage.location.template"   = "s3://${aws_s3_bucket.data.bucket}/bronze/coin=$${coin}/dt=$${dt}/source=$${source}/"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.data.bucket}/bronze/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = local.table_columns
      content {
        name = columns.value.Name
        type = columns.value.Type
      }
    }
  }

  partition_keys {
    name = "coin"
    type = "string"
  }
  partition_keys {
    name = "dt"
    type = "date"
  }
  partition_keys {
    name = "source"
    type = "string"
  }
}
