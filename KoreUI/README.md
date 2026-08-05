# KoreUI

KoreUI is the shared frontend source tree for the suite's browser experiences. It contains service-specific templates, static assets, and UI implementation files that are served by the runnable subsystems.

## Why it exists

The suite uses a common shell from `KoreUI/UIElements/`, but each service still needs its own HTML templates, JavaScript modules, and CSS. KoreUI owns both layers.

## What it includes

- `KoreAgent/` browser UI assets
- `KoreCode/` static editor UI files
- `KoreComms/` templates for conversations, connections, and activity
- `KoreLiveWeb/` templates and static assets for the live web tool UI
- `KoreStack/` dashboard and endpoint explorer frontend files
- Service-specific UI folders under `KoreData/` where needed

## How to use it

KoreUI is not started directly. The runnable services import or mount these assets when they serve their own browser interfaces.

When changing browser behavior or HTML structure for a service, this folder is often the correct place to start.

## Troubleshooting

| Problem | What to check |
|---|---|
| UI page renders without expected controls | Confirm the service is mounting the correct templates or static directory |
| Styles appear inconsistent across services | Check whether the service is using `KoreUI/UIElements/` for shared chrome and its service folder for service-specific assets |
| A frontend change has no visible effect | Verify you edited the service-owned file under `KoreUI/`, not an older duplicated asset elsewhere |

## Related docs

- Shared shell and tokens: [UIElements/README.md](UIElements/README.md)
- Root overview: [../README.md](../README.md)
