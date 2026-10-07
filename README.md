# Unified ArduPilot Documentation

This is a Fumadocs site that brings the ArduSub, ArduPilot developer, MAVProxy,
MAVLink, project, and generated API references together under `/docs`.

The source snapshots in `sources/` are immutable inputs. Regenerate the MDX,
assets, API pages, and navigation metadata with:

```bash
npm run import
npm run verify:docs
```

`npm run import` refuses to replace an existing snapshot. To intentionally take
a new snapshot from the three sibling checkouts, use
`npm run import -- --refresh-sources` and review `sources/checksums.json`.

Run the site locally with `npm run dev`; production validation is `npm run build`.
The generated Fumadocs `.source/` directory remains tool-owned and is ignored by
git.
