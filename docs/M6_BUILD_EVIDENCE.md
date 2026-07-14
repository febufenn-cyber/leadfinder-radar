# M6 autonomous build evidence

Build trigger: `build` on 2026-07-14. Planning baseline: `main` at `26fc429693767bb168b59b1c3fecd38e8ce8e43b`.

M6 was delivered as five sequential pull requests. Each slice started from the preceding merge on `main`, passed frozen dependency installation, Ruff, Alembic verification, and the PostgreSQL test suite before merge.

| Slice | PR | Primary implementation | Final tested head | Merge on `main` | Migration |
|---|---:|---|---|---|---|
| M6A | #8 | `6119b1fdf523fb58673e827e3074688a191d19fd` | `6761d9d35ba88640e51625195bbbc4f0b8565cb6` | `edcfd21c7b6d84ebe4aaacd8fd64f3e50a41bb37` | none |
| M6B | #9 | `5b0d23cb093fc5a72afcb3ee995eebe1372d1f0f` | same | `d6f26e41949bd1bf6cbbd109d2ab7426766155cd` | none |
| M6C | #10 | `0e94cb0a977ad9a684d1664a6a9d676a49bdd41f` | `246b07de4ecbd79e7ad15aa7efc626f5588c1be4` | `ed7a0a393fb7d86c72fbc6e172dc0aa4b7073e10` | `008` |
| M6D | #11 | `6c823c8e6504d0946c557047ae307f67a2f6694e` | `ee1dedd8a8b72e124ca7b56a766c7df3169207a5` | `0ffa7f071bc668596a26a4513bb4bfbb3a4c1015` | `009` |
| M6E | #12 | `705ea91e091a0d09f4cbafc4387efd1964d96f35` | final PR head recorded by GitHub | final PR merge recorded by GitHub | none |

## Permanent validation gate

The final CI workflow runs:

1. frozen dependency installation
2. Ruff
3. clean Alembic upgrade to head
4. explicit `008 → 009` upgrade
5. in-memory MCP smoke: exact eight-tool inventory plus database health
6. full PostgreSQL pytest suite
7. retained MCP smoke and JUnit artifacts

The final M6E candidate passed the complete gate in GitHub Actions run `29312122392` before temporary documentation-finalization machinery was removed.

## Security invariants retained

- No unattended or batch send path exists.
- Approval remains one owner action for one draft.
- MCP approval requires a short-lived value delivered only to owner Telegram.
- Only an HMAC is persisted; challenge binding includes lead, draft, variant, and exact draft-text digest.
- Existing copy/API approval services enforce approval events and all send guardrails.
- Read tools are bounded and typed; no arbitrary SQL, shell, filesystem-write, direct platform-post, prompt-edit, or generic lead-state tool exists.
- Every MCP call is timed, rate-limited, sanitized, and audited with redacted arguments.

## Operational status

Code and GitHub merge completion are separate from production deployment. The VPS was not accessed during this build. Apply migration `009`, configure the independent approval secret, and run `scripts/mcp_smoke.py` during deployment. The roadmap's 48-hour unattended soak remains pending until production telemetry demonstrates it.
