"""Amazon API Gateway REST (v1) and HTTP/WebSocket (v2) APIs and their stages (edge-dns spec §3.2). Stage variables
can hold secrets: they are never read into a resource."""
from decimal import Decimal

from aws95.aws import gate
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


@register
class ApiGatewayCollector:
    name = "apigateway"
    scope = "regional"
    timeout_s = 180
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        rest = gate.bind(session, "apigateway", region, cancel=cancel)
        v2 = gate.bind(session, "apigatewayv2", region, cancel=cancel)
        resources, denials, counts = [], 0, {"rest": 0, "http": 0, "websocket": 0}
        for page in rest.pages("get_rest_apis"):
            for api in page.get("items", []):
                counts["rest"] += 1
                attrs = {"name": api.get("name"), "kind": "rest",
                         "endpoint_type": ",".join(api.get("endpointConfiguration", {}).get("types", []))}
                stages, unreadable = probe(rest.get_stages, restApiId=api["id"])
                denials += unreadable
                if not unreadable and stages is not None:
                    items = stages.get("item", [])
                    attrs["stage_count"] = len(items)
                    for st in items:
                        resources.append(Resource("apigateway.stage", region, f"{api['id']}/{st['stageName']}", {
                            "api_id": api["id"], "kind": "rest", "access_logging": bool(st.get("accessLogSettings")),
                            "tracing": bool(st.get("tracingEnabled")), "waf": bool(st.get("webAclArn"))}))
                resources.append(Resource("apigateway.api", region, api["id"], attrs, unreadable))
        for page in v2.pages("get_apis"):
            for api in page.get("Items", []):
                kind = "websocket" if api.get("ProtocolType") == "WEBSOCKET" else "http"
                counts[kind] += 1
                attrs = {"name": api.get("Name"), "kind": kind}
                stages, unreadable = probe(_v2_stages, v2, api["ApiId"])
                denials += unreadable
                if not unreadable and stages is not None:
                    attrs["stage_count"] = len(stages)
                    for st in stages:
                        resources.append(Resource("apigateway.stage", region, f"{api['ApiId']}/{st['StageName']}", {
                            "api_id": api["ApiId"], "kind": kind,
                            "access_logging": bool(st.get("AccessLogSettings"))}))
                resources.append(Resource("apigateway.api", region, api["ApiId"], attrs, unreadable))
        metrics = [Metric("apigateway.api_count", Decimal(n), dims={"region": region, "kind": k})
                   for k, n in counts.items() if n]
        return CollectorResult(resources, metrics, [region], denials)


def _v2_stages(v2, api_id: str) -> list:
    return [s for page in v2.pages("get_stages", ApiId=api_id) for s in page.get("Items", [])]
