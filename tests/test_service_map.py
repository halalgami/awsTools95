from aws95.contracts.service_map import (META_COLLECTORS, NO_COLLECTOR, NOT_RESOURCE, SERVICE_COLLECTORS,
                                         uncovered_reason)


def test_every_shipped_collector_maps_to_a_service():
    import aws95.aws.engines.labsnap.collect.collectors  # noqa: F401 - registers them
    from aws95.aws.engines.labsnap.collect.base import all_collectors
    mapped = {c for names in SERVICE_COLLECTORS.values() for c in names}
    shipped = {getattr(c, "name", c) for c in all_collectors()} - META_COLLECTORS
    assert shipped <= mapped, shipped - mapped


def test_uncovered_reasons():
    """Review Focus 4."""
    assert uncovered_reason("AWS Lambda") is None
    assert uncovered_reason("Amazon Route 53") == NO_COLLECTOR
    assert uncovered_reason("Tax") == NOT_RESOURCE
    assert uncovered_reason("AWS Support (Business)") == NOT_RESOURCE
    assert uncovered_reason("Something AWS invents next year") == NO_COLLECTOR


def test_catalog_still_offers_service_rtypes():
    from aws95.programs.resources.catalog import SERVICE_RTYPES, rtypes_for_service
    assert rtypes_for_service("AWS Lambda") == ("lambda.function",) and SERVICE_RTYPES


def test_bedrock_model_lines_are_not_resources():
    assert uncovered_reason("Claude Opus 5.5 (Amazon Bedrock Edition)") == NOT_RESOURCE
    assert uncovered_reason("Cohere Embed Model 3 - Multilingual (Amazon Bedrock Edition)") == NOT_RESOURCE
