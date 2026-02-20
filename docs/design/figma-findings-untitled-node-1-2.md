# Figma Findings: Untitled (node 1:2)

## Source
- URL: https://www.figma.com/design/TbU9GzU3OEBssNETUoHXr5/Untitled?node-id=1-2&t=bidM9h22c1MSExNc-4
- Retrieved via Figma MCP (`get_design_context`, `get_metadata`, `get_variable_defs`)
- Screenshot tools: not used

## Node Summary
- `1:2` `Frame 1000002352` (`1108x1063`): root canvas with mostly absolute positioning; rounded outer shell (`12px`), not an auto-layout frame.
- `1:5` top bar (`1069x67`): horizontal layout (`justify-between`), padding `14px 24px 10px`, left title stack (`gap 12`), right action row (`gap 16`).
- `1:16` “Start” card (`328x318`): vertical layout, `gap 16`, padding `12`, radius `16`; inner sections use `gap 24/17/16`, chips row `gap 8`.
- `1:58` “External Signal” card (`328x447`): vertical layout, `gap 16`, padding `12`, with 3 nested content blocks (`gap 12` between blocks, each block `gap 20`).
- `1:97` “Media Synthesizer” card (`328x365`): vertical layout, `gap 16`, padding `12`; “Media type” chip row + two info/output blocks.

## Typography Tokens (Inferred)
- `--font-sans`: `Geist`
- `--text-title-lg`: `18px`, weight `536`, tracking `-0.18px`, color `#171717`
- `--text-title-md`: `16px`, weight `536`, tracking `-0.16px`, color `#171717`
- `--text-label-md`: `14px`, weight `477`, tracking `-0.28px`, color `#171717`
- `--text-body-sm`: `13px`, weight `434`, tracking `-0.26px`, color `#676767`
- `--text-body-md`: `14px`, weight `434`, tracking `-0.28px`, color `#676767`

## Color Tokens (Inferred)
- `--color-bg`: `#FFFFFF`
- `--color-surface-muted`: `#F7F7F7`
- `--color-text-primary`: `#171717`
- `--color-text-secondary`: `#676767`
- `--color-border`: `#E6E6E6` (often `rgba(230,230,230,0.6)`)
- `--color-accent`: `#FF6000`
- `--color-on-accent`: `#FFFFFF`
- `--shadow-card`: `1px 1px 16px rgba(51,51,51,0.06)`
- `--shadow-cta`: `2.321px 4.643px 6.19px rgba(51,51,51,0.12)` + inset highlights

## Component Inventory
- App/top header with title, subtitle, icon button, primary CTA (“Share”).
- Node cards (workflow blocks): `Start`, `External Signal`, `Media Synthesizer`.
- Section headers with collapse/expand icon affordance.
- Tag/chip selectors (Audio/Data/Logic, Image/Video).
- Key-value metadata rows.
- Thumbnail strips (32px and 56px variants).
- Connector visuals between cards (line + endpoint dots).
- No table component or full nav/sidebar component in this node.

## Interaction States Inferred
- Visible variants are mostly default/static.
- Required implementation states: hover/active/focus for CTA and chips, selected chip state, collapsed/expanded section state, disabled/loading for workflow cards.
- No explicit hover/pressed mock variants were present in retrieved context.

## Next.js + Tailwind Migration Notes
- Build composable primitives: `FlowCard`, `CardSection`, `ChipSelect`, `MetaList`, `ThumbnailRow`, `FlowConnector`, `TopBar`.
- Keep Figma absolute placement only for diagram mode; for app screens use responsive grid/flex layout (desktop multi-column, mobile stacked).
- Define tokens as CSS variables in `:root` and map to Tailwind theme extension.
- Use `next/font` for Geist and `next/image` for media thumbnails.
- Normalize weights (`536/477/434`) to nearest supported variable-font steps while preserving visual hierarchy.
- Add accessibility states (`focus-visible`, keyboard nav) and contrast checks for `#676767` on light surfaces.
- Redact any token-like sample content before demos.

## Prioritized Checklist (Incident Dashboard Redesign)
1. Establish a design-token layer (typography, color, radius, shadow, spacing) from extracted values.
2. Build a responsive dashboard shell (header + grid zones), replacing absolute-only canvas placement.
3. Implement reusable card system matching `328px` node patterns and section spacing.
4. Implement interactive chips/filters with explicit selected/hover/active/disabled states.
5. Add incident-specific modules: severity badges, timeline/events, owner/status metadata, evidence thumbnails.
6. Wire real data with strict redaction for credentials and sensitive payload fields.
7. Validate accessibility, mobile behavior, and visual parity against Figma before rollout.

## Notes
- `get_variable_defs` returned `{}` at the inspected nodes, so token names above are inferred from concrete style values.
- One deep node read failed due Figma MCP starter-plan call limits; summary is based on successfully retrieved root + key child nodes.
