## Summary

<!-- What changed and why. One task per PR. -->

## Testing

- [ ] `make check`
- [ ] `make e2e` (UI, routes, auth, or schema changes)

## Shared state this touches

<!-- Tick what applies; see CLAUDE.md "Shared production state". -->

- [ ] Prisma schema: additive and nullable/defaulted only. Reaches the production database when this merges.
- [ ] `backend/`: redeploys the Modal worker when this merges.
- [ ] Worker ↔ frontend contract (stages, callback fields, error prefixes, trigger body): both sides updated here.
