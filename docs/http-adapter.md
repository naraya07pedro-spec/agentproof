# External agent HTTP observation

AgentProof's deterministic evaluators and release gate use local fixtures. The
optional HTTP adapter is a separate Python SDK for retrieving one prepared
observation from an operator-controlled agent in a test environment. Its
`external_http` evidence identifies the source of the observation. A remote
endpoint can provide an incorrect observation, so verify any resulting claim
against the external system.

## Configure an endpoint

Provision a dedicated, read-only HTTPS route that returns a synthetic observation
for a fixed case. Review the service and its dependencies for side effects before
enabling access. Keep real email, payment, CRM, and repository integrations
disabled on the remote service. Supply an exact TLS hostname, a pinned public
IPv4 address for that service, and a literal path. Use a dedicated credential
scoped to reading that route when authentication is required.

The response contract is `200 OK` with `Content-Type: application/json` and the
following UTF-8 body:

```json
{"version":"1","outcome":"completed","answer":"synthetic observation"}
```

The other valid outcome is `refused`. The maximum answer length is 256
characters. Additional fields, compressed responses, trailers, unexpected
statuses, redirects, and invalid response framing are rejected.

Set `AGENTPROOF_HTTP_HOSTNAME`, `AGENTPROOF_HTTP_IPV4`, and
`AGENTPROOF_HTTP_PATH` from trusted operator configuration. Inject an optional
`AGENTPROOF_HTTP_TOKEN` through the process secret store. Keep configuration and
credentials out of suites and agent output.

```python
import asyncio
import os

from pydantic import SecretStr

from agentproof.http_adapter import HttpAgentAdapter, HttpAgentEndpoint

token = os.environ.get("AGENTPROOF_HTTP_TOKEN")
endpoint = HttpAgentEndpoint(
    hostname=os.environ["AGENTPROOF_HTTP_HOSTNAME"],
    ipv4_address=os.environ["AGENTPROOF_HTTP_IPV4"],
    path=os.environ["AGENTPROOF_HTTP_PATH"],
    bearer_token=SecretStr(token) if token else None,
)
adapter = HttpAgentAdapter(endpoint, enabled=True)
observation = asyncio.run(adapter.read_observation())
print(observation.evidence, observation.outcome)
```

## Network and execution limits

- Calls are disabled unless trusted code passes `enabled=True` with an endpoint.
  The adapter is absent from the dashboard, API, suite schema, and release gate.
- Each adapter uses one allowlist entry. It sends `GET` with no request body
  directly to the pinned IPv4 address on port 443. The configured hostname is
  used for the HTTP Host header, TLS SNI, and certificate verification. No DNS
  resolution or proxy discovery takes place. Verify routing and egress controls
  in your own environment before use.
- The request uses TLS 1.2 or later and system trust roots. The SDK rejects
  private, loopback, link-local, multicast, and other special-use addresses.
  Authorized operators must update a pin when their endpoint changes addresses.
- By default, an admitted request has a two-second deadline and an 8,192-byte
  response body limit. The hard maxima are ten seconds and 32,768 body bytes.
  A separate wire limit adds 16,384 bytes for HTTP headers and framing.
- An instance defaults to four lifetime requests and one active request. The
  hard maxima are 32 lifetime requests and four active requests. Each admitted
  request consumes budget, including timeouts and cancellation. Saturation
  returns `busy` immediately unless the lifetime budget is exhausted. The SDK
  does not retry or queue. Operators using multiple instances or processes
  manage their aggregate limits externally.
- The SDK returns fixed error codes and does not generate logs. Treat the
  returned answer as untrusted and keep it out of logs, traces, and exception
  messages. Keep the approved path free of credentials and sensitive data.

The adapter tests simulate the transport and verify exact destination selection,
TLS configuration, request formatting, response parsing, and the failure limits.
They do not require a paid provider or send a real external request.
