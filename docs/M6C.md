# M6C — safe redraft and mute tools

M6C adds two audited mutation tools without exposing approval or send behavior.

## `redraft(lead_id, guidance)`

- accepts only a lead currently awaiting approval
- bounds guidance to 500 characters
- treats guidance as untrusted preference data
- reuses the existing persona, community-rule, truthfulness, language, and word-limit prompt path
- archives every previous active draft in `draft_revisions`
- keeps one active row per A/B/C variant
- rechecks the lead and active draft IDs after generation to prevent stale concurrent replacement
- stores only a SHA-256 guidance digest in provenance events
- never creates an approval event or send row

Migration `008` creates `draft_revisions`.

## `mute(kind, value, pack)`

Only `keyword` and `community` are accepted. Values are stripped, lowercased, length-bounded, and passed through the existing `add_mute` deduplication and event path. A named pack must currently be enabled.
