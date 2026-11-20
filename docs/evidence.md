# Evidence and limitations

| Evidence class | Meaning |
| --- | --- |
| Deterministic simulation | Scripted fixture, fake tools and provider responses, stable time and trace data. An API run created from the bundled suite is stored with `evidence: deterministic_simulation`. |
| Integration test | A real component boundary is exercised, such as the API talking to PostgreSQL or the browser driving the dashboard. This describes the verification method, while the agent behavior in that workflow is still simulated. |
| Real provider test | An evaluation backed by a real external provider, with its version, endpoint, credentials scope, and environmental dependencies identified. The deterministic workflow uses no such provider. |
| Actual production use | Observed use in a deployed system with a documented operational context. Local and CI validation do not establish this status. |

The demo shows how assertion failures and traces explain a fixture outcome. It provides no benchmark for model reliability, attack resistance, or production capacity. Simulated time and interruption are reproducible inputs. They do not measure service latency or guarantee recovery after a process kill.

## Acceptance evidence

Use [the local commands](getting-started.md) to reproduce the demo. A verification record should include the exact checked-out revision, commands and results, whether PostgreSQL and a browser were available, and any skipped or unavailable checks. A passing local check establishes local evidence, even if a hosted CI result is unavailable.
