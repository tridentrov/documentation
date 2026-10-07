# Immutable source snapshot

This directory is populated by `scripts/import_docs.py` from the sibling
`ardupilot_wiki`, `ardupilot`, and `mavlink` checkouts. `inventory.json` records
the selected tracked inputs and `checksums.json` records SHA-256 hashes for every
snapshot file. Normal imports never overwrite these files; use
`--refresh-sources` only when intentionally updating the snapshot.
