"""The DataHub metadata graph LineageMedic reasons over, as committed fixtures.

This module is the single source of truth for asset URNs, schemas, ownership,
tags, and lineage edges while running in Demo Fixture Mode. It mirrors the graph
that the ingestion scripts create in a real DataHub instance, so the live
adapter can return structurally identical objects and no downstream code has to
change when the two environments are swapped.

The graph has two branches sharing no defect surface:

    raw_patients -> staging_patients -> patient_features
                 -> readmission_risk_model -> production_readmission_endpoint

    raw_billing  -> billing_summary

Both branches are real nodes with real owners and schemas. The billing branch
exists so the Impact Agent has something it must *not* quarantine: selective
containment is only demonstrable if there is an adjacent asset that a naive
"shut everything down" response would wrongly include.

URNs follow DataHub's canonical form so they stay valid verbatim once pointed at
a live instance.

Contact addresses are team distribution lists, never personal addresses.
"""

from __future__ import annotations

from lineagemedic.models import Asset, AssetKind, DataSource, LineageGraph, Owner, SchemaField

PLATFORM_SQLITE = "sqlite"
PLATFORM_FEAST = "feast"
PLATFORM_MLFLOW = "mlflow"
PLATFORM_SAGEMAKER = "sagemaker"

_ENV = "PROD"


def dataset_urn(name: str, platform: str = PLATFORM_SQLITE) -> str:
    """Canonical DataHub dataset URN."""
    return f"urn:li:dataset:(urn:li:dataPlatform:{platform},lineagemedic.{name},{_ENV})"


def model_urn(name: str) -> str:
    """Canonical DataHub ML model URN."""
    return f"urn:li:mlModel:(urn:li:dataPlatform:{PLATFORM_MLFLOW},lineagemedic.{name},{_ENV})"


def endpoint_urn(name: str) -> str:
    """ML deployment endpoint URN."""
    return (
        f"urn:li:mlModelDeployment:(urn:li:dataPlatform:{PLATFORM_SAGEMAKER},"
        f"lineagemedic.{name},{_ENV})"
    )


# Stable URN constants, imported by agents, fixtures, and tests.
URN_RAW_PATIENTS = dataset_urn("raw_patients")
URN_STAGING_PATIENTS = dataset_urn("staging_patients")
URN_PATIENT_FEATURES = dataset_urn("patient_features", PLATFORM_FEAST)
URN_READMISSION_MODEL = model_urn("readmission_risk_model")
URN_PRODUCTION_ENDPOINT = endpoint_urn("production_readmission_endpoint")
URN_RAW_BILLING = dataset_urn("raw_billing")
URN_BILLING_SUMMARY = dataset_urn("billing_summary")

DATAHUB_FRONTEND_DEFAULT = "http://localhost:9002"


# --- Owners (role accounts / distribution lists only) -----------------------

OWNER_DATA_PLATFORM = Owner(
    urn="urn:li:corpGroup:data-platform",
    display_name="Data Platform Team",
    type="TECHNICAL_OWNER",
    contact="data-platform@lineagemedic.example",
)
OWNER_CLINICAL_ANALYTICS = Owner(
    urn="urn:li:corpGroup:clinical-analytics",
    display_name="Clinical Analytics",
    type="BUSINESS_OWNER",
    contact="clinical-analytics@lineagemedic.example",
)
OWNER_ML_PLATFORM = Owner(
    urn="urn:li:corpGroup:ml-platform",
    display_name="ML Platform Team",
    type="TECHNICAL_OWNER",
    contact="ml-platform@lineagemedic.example",
)
OWNER_REVENUE_CYCLE = Owner(
    urn="urn:li:corpGroup:revenue-cycle",
    display_name="Revenue Cycle Team",
    type="TECHNICAL_OWNER",
    contact="revenue-cycle@lineagemedic.example",
)
OWNER_STEWARD = Owner(
    urn="urn:li:corpGroup:health-data-stewards",
    display_name="Health Data Stewards",
    type="DATA_STEWARD",
    contact="data-stewards@lineagemedic.example",
)


def _url(urn: str, frontend: str) -> str:
    """Build a clickable DataHub UI link for an entity."""
    kind = "dataset"
    if urn.startswith("urn:li:mlModelDeployment"):
        kind = "mlModelDeployment"
    elif urn.startswith("urn:li:mlModel"):
        kind = "mlModel"
    return f"{frontend.rstrip('/')}/{kind}/{urn}"


def build_graph(
    *,
    source: DataSource = DataSource.FIXTURE,
    frontend_url: str = DATAHUB_FRONTEND_DEFAULT,
) -> LineageGraph:
    """Construct the full seven-asset lineage graph.

    ``source`` is threaded onto every asset so a caller can never lose track of
    whether these objects came from a live DataHub or from this file.
    """
    assets = [
        Asset(
            urn=URN_RAW_PATIENTS,
            name="raw_patients",
            kind=AssetKind.DATASET,
            platform=PLATFORM_SQLITE,
            description=(
                "Raw patient admissions landed from the hospital EHR export. "
                "No validation is applied at this layer."
            ),
            tags=["healthcare", "phi", "bronze", "source-of-truth"],
            owners=[OWNER_DATA_PLATFORM, OWNER_STEWARD],
            schema_fields=[
                SchemaField(name="patient_id", native_type="TEXT", nullable=False,
                            description="Pseudonymised patient identifier."),
                SchemaField(name="age", native_type="INTEGER", nullable=True,
                            description="Patient age in years at admission. Expected range 0-130."),
                SchemaField(name="sex", native_type="TEXT", nullable=True),
                SchemaField(name="admission_date", native_type="TEXT", nullable=True,
                            description="Admission timestamp. Required by the feature pipeline."),
                SchemaField(name="discharge_date", native_type="TEXT", nullable=True),
                SchemaField(name="primary_dx", native_type="TEXT", nullable=True),
                SchemaField(name="ingested_at", native_type="TEXT", nullable=False),
            ],
            upstreams=[],
            downstreams=[URN_STAGING_PATIENTS],
            source=source,
            datahub_url=_url(URN_RAW_PATIENTS, frontend_url),
        ),
        Asset(
            urn=URN_STAGING_PATIENTS,
            name="staging_patients",
            kind=AssetKind.DATASET,
            platform=PLATFORM_SQLITE,
            description=(
                "Cleaned patient admissions. Drops rows with a NULL admission_date "
                "but performs no range validation on age."
            ),
            tags=["healthcare", "phi", "silver"],
            owners=[OWNER_DATA_PLATFORM],
            schema_fields=[
                SchemaField(name="patient_id", native_type="TEXT", nullable=False),
                SchemaField(name="age", native_type="INTEGER", nullable=True,
                            description="Passed through from raw_patients without validation."),
                SchemaField(name="sex", native_type="TEXT", nullable=True),
                SchemaField(name="admission_date", native_type="TEXT", nullable=False),
                SchemaField(name="length_of_stay", native_type="INTEGER", nullable=True),
                SchemaField(name="primary_dx", native_type="TEXT", nullable=True),
                SchemaField(name="last_refreshed_at", native_type="TEXT", nullable=False,
                            description="Watermark written by the staging job."),
            ],
            upstreams=[URN_RAW_PATIENTS],
            downstreams=[URN_PATIENT_FEATURES],
            source=source,
            datahub_url=_url(URN_STAGING_PATIENTS, frontend_url),
        ),
        Asset(
            urn=URN_PATIENT_FEATURES,
            name="patient_features",
            kind=AssetKind.FEATURE_TABLE,
            platform=PLATFORM_FEAST,
            description=(
                "Model-ready features for readmission risk. age_bucket is derived "
                "from staging_patients.age and falls back to 'unknown' when the "
                "value is out of range."
            ),
            tags=["ml-feature", "healthcare", "gold", "serving"],
            owners=[OWNER_ML_PLATFORM, OWNER_CLINICAL_ANALYTICS],
            schema_fields=[
                SchemaField(name="patient_id", native_type="TEXT", nullable=False),
                SchemaField(name="age_bucket", native_type="TEXT", nullable=True,
                            description="18-34 / 35-54 / 55-74 / 75+ / unknown."),
                SchemaField(name="prior_admissions", native_type="INTEGER", nullable=True),
                SchemaField(name="length_of_stay", native_type="INTEGER", nullable=True),
                SchemaField(name="chronic_flag", native_type="INTEGER", nullable=True),
                SchemaField(name="computed_at", native_type="TEXT", nullable=False),
            ],
            upstreams=[URN_STAGING_PATIENTS],
            downstreams=[URN_READMISSION_MODEL],
            source=source,
            datahub_url=_url(URN_PATIENT_FEATURES, frontend_url),
        ),
        Asset(
            urn=URN_READMISSION_MODEL,
            name="readmission_risk_model",
            kind=AssetKind.ML_MODEL,
            platform=PLATFORM_MLFLOW,
            description=(
                "Gradient-boosted classifier predicting 30-day readmission risk. "
                "Consumes patient_features; age_bucket is a top-3 predictor."
            ),
            tags=["ml-model", "healthcare", "production", "clinical-decision-support"],
            owners=[OWNER_ML_PLATFORM, OWNER_CLINICAL_ANALYTICS],
            schema_fields=[],
            upstreams=[URN_PATIENT_FEATURES],
            downstreams=[URN_PRODUCTION_ENDPOINT],
            source=source,
            datahub_url=_url(URN_READMISSION_MODEL, frontend_url),
        ),
        Asset(
            urn=URN_PRODUCTION_ENDPOINT,
            name="production_readmission_endpoint",
            kind=AssetKind.ENDPOINT,
            platform=PLATFORM_SAGEMAKER,
            description=(
                "Live inference endpoint serving readmission scores to the "
                "discharge planning application."
            ),
            tags=["production", "endpoint", "clinical-decision-support", "tier-1"],
            owners=[OWNER_ML_PLATFORM],
            schema_fields=[],
            upstreams=[URN_READMISSION_MODEL],
            downstreams=[],
            source=source,
            datahub_url=_url(URN_PRODUCTION_ENDPOINT, frontend_url),
        ),
        # --- Billing control branch: adjacent, independently owned, clean ---
        Asset(
            urn=URN_RAW_BILLING,
            name="raw_billing",
            kind=AssetKind.DATASET,
            platform=PLATFORM_SQLITE,
            description="Raw claims extracted from the billing system.",
            tags=["billing", "finance", "bronze"],
            owners=[OWNER_REVENUE_CYCLE],
            schema_fields=[
                SchemaField(name="claim_id", native_type="TEXT", nullable=False),
                SchemaField(name="patient_id", native_type="TEXT", nullable=True,
                            description="Soft reference to a patient. Not a lineage edge: "
                                        "billing does not read patient attributes."),
                SchemaField(name="amount_cents", native_type="INTEGER", nullable=False),
                SchemaField(name="payer", native_type="TEXT", nullable=False),
                SchemaField(name="claim_date", native_type="TEXT", nullable=False),
                SchemaField(name="ingested_at", native_type="TEXT", nullable=False),
            ],
            upstreams=[],
            downstreams=[URN_BILLING_SUMMARY],
            source=source,
            datahub_url=_url(URN_RAW_BILLING, frontend_url),
        ),
        Asset(
            urn=URN_BILLING_SUMMARY,
            name="billing_summary",
            kind=AssetKind.DATASET,
            platform=PLATFORM_SQLITE,
            description="Claim counts and totals per payer. Feeds finance reporting only.",
            tags=["billing", "finance", "gold", "reporting"],
            owners=[OWNER_REVENUE_CYCLE],
            schema_fields=[
                SchemaField(name="payer", native_type="TEXT", nullable=False),
                SchemaField(name="claim_count", native_type="INTEGER", nullable=False),
                SchemaField(name="total_cents", native_type="INTEGER", nullable=False),
                SchemaField(name="last_refreshed_at", native_type="TEXT", nullable=False),
            ],
            upstreams=[URN_RAW_BILLING],
            downstreams=[],
            source=source,
            datahub_url=_url(URN_BILLING_SUMMARY, frontend_url),
        ),
    ]
    return LineageGraph(assets=assets, source=source)


#: Maps a SQLite table name to the URN of the asset that represents it.
TABLE_TO_URN = {
    "raw_patients": URN_RAW_PATIENTS,
    "staging_patients": URN_STAGING_PATIENTS,
    "patient_features": URN_PATIENT_FEATURES,
    "raw_billing": URN_RAW_BILLING,
    "billing_summary": URN_BILLING_SUMMARY,
}
