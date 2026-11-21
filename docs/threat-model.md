# Threat model

AgentProof handles user-submitted test suites and evaluates structured agent responses. The deterministic and HTTP workflows execute only simulated tools on synthetic data. The system assumes developers control the host, database, Compose configuration, workspace tokens, and fixture code. Deployment to a multitenant or internet-facing environment requires a separate security review.

## Trust boundaries and controls

| Boundary | Risk | Control |
| --- | --- | --- |
| Submitted suite and agent input | Untrusted instructions, oversized structures, malformed or unforeseen input | Typed versioned schema and bounded inputs. The bundled fixture agents interpret a small declarative vocabulary. |
| Agent to tool | Unauthorized use, permission escalation, repeated side effect | Default-deny simulated tools. Policy assertions track attempted actions and fake provider effects separately. |
| V1 fixture to fake provider | Timeout, malformed data, retry exhaustion, interrupted worker | Scripted responses and virtual time and attempt limits. |
| Workspace to API | Unauthorized read, list, or replay of another workspace's records | Bearer token authentication and workspace-scoped queries. Resource lookups respond with 404 outside the caller's scope. |
| Trace and assertion output | Accidental disclosure of prompts or responses | Traces use stable event kinds, sequence numbers, attempt counts, symbolic fixture tools, and virtual time. Output equality assertions expose match status. |
| HTTP agent response to benchmark tool broker | Untrusted tool names or arguments and misleading labels | Checks constrain the permitted synthetic tool and ticket inputs. Policy violations are scored independently of task correctness. The evaluator handles structured values and has no shell or arbitrary tool execution path. |
| Model adapter to external provider | Unapproved egress, credential leakage, oversized requests or responses | An external provider requires explicit operator opt-in and an exact destination. API access also requires one operator-approved workspace. Transport adds limits and keeps raw provider text out of stored evidence. See [HTTP evaluation guide](v2-evaluation.md). |
| External adapter to HTTP endpoint | Unexpected destinations, credential forwarding, unbounded requests, provider-side effects | Separate opt-in adapter with host-controlled destination and credential configuration and explicit request bounds. Review endpoint behavior before enabling it. See [adapter contract](http-adapter.md). |

Use synthetic suite input. The database contains submitted suite specifications, including fixture input and expected outputs. Restrict database access and avoid putting credentials, sensitive prompts, or production data in suites. Treat workspace tokens as secrets and avoid sharing screenshots or shell history that contain them. The dashboard holds the token in page memory and drops it on disconnect or reload. Its same-origin proxy forwards only the authorization and content type headers to the configured API URL for supported routes. The proxy rejects redirects and oversized requests and caps upstream requests at 15 seconds for V1 and 40 seconds for V2.

## Residual risks

The evaluator does not execute submitted programs. Docker Compose helps reproduce the local service configuration and provides no independent evidence of hostile-code isolation. The deterministic and reference HTTP checks establish behavior for modeled actions and inputs. They cannot generalize to arbitrary agent implementations or live model behavior.

Local workspace tokens are coarse-grained development credentials. A production authentication design would need issuance, revocation, individual identity, transport security, abuse limits, and a retention policy. Backend, database, and browser controls need separate review before hosting for untrusted users.

An enabled HTTP adapter can enforce its local protocol and resource bounds. One caller can submit successive runs, and an operator must set any aggregate service quota or cost ceiling with the provider. An operator must control the configured endpoint and ensure its server-side handler has no unintended effects. Endpoint transport limits do not establish data residency, service identity beyond ordinary TLS, or safe execution of provider-returned code.
