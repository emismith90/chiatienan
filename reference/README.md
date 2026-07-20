# Reference material

Read-only reference — **not** the build base for `chiatienan`.

## `sample-cursor-sdk-with-image/`

A vendored copy of the Niteco `sample-cursor-sdk-with-image` boilerplate
(originally from Azure DevOps: `Niteco-Vietnam.0IN-ChampionAndTechnicalLeadership`).

It is kept here **only** as a reference for how to drive the Python `cursor-sdk`
(the Cursor Agent SDK). See the design spec §8
([docs/superpowers/specs/2026-07-20-chiatienan-teams-lunch-bot-design.md](../docs/superpowers/specs/2026-07-20-chiatienan-teams-lunch-bot-design.md))
for the specific patterns we replicate:

- `AsyncClient.launch_bridge → agents.create(AgentOptions) → agent.send(...)`
- Model resolution (`resolve_model_selection`) — bare `composer-2.5` fails
- `CustomTool(execute, description, input_schema)` registration
- Multimodal image send (`UserMessage` + `SDKImage.data_image`) + `sanitize_images`
- Turn-storm cap / interrupt close-out

`chiatienan` does **not** fork or extend this sample: it is a Teams bot with a
different architecture (no Next.js/AG-UI/SSE), built fresh per the design spec.
