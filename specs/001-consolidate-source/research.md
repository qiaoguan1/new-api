# Research: Consolidate Server Source

## Decision 1: Canonical repository

- **Decision**: `/root/new-api-build/new-api` remains the canonical repository.
- **Rationale**: It is the only Git worktree, contains 28 pre-existing tracked/untracked customizations, and corresponds to the running project's source lineage.
- **Alternatives considered**: Replacing it with `wechatpay-final` would discard unrelated custom work and provenance; maintaining three parallel trees would leave the original operational ambiguity unresolved.

## Decision 2: Reconciliation precedence

- **Decision**: Existing canonical changes win by default. `wechatpay-final` wins only for explicitly reviewed WeChat Pay and compatibility paths.
- **Rationale**: This prevents a directory-level overwrite while retaining the most complete payment implementation.
- **Alternatives considered**: Blind recursive copy was rejected because it would include backup artifacts and could overwrite unrelated customizations.

## Decision 3: Source classification

- **Decision**: Integrate the seven final-only functional paths and the reviewed set of eighteen differing functional paths. Preserve canonical-only migration utilities. Exclude all `.bak`, `.backup-*`, and UI backup artifacts.
- **Rationale**: Hash comparison established that most files are identical and narrowed the meaningful delta to a reviewable set.
- **Observed comparison**: Base/final: 1,968 identical, 18 different, 4 base-only, 10 final-only. Of the 10 final-only paths, 7 are functional and 3 are backups.

## Decision 4: WeChat Pay validation

- **Decision**: Preserve strict runtime validation requiring merchant/private-key and platform-public-key settings; repair test fixtures to supply the required public key material.
- **Rationale**: The imported test fixtures lag behind the implementation contract. Weakening validation would produce a production security regression.
- **Alternatives considered**: Making public-key fields optional was rejected because notification signature verification depends on them.

## Decision 5: Compatibility changes

- **Decision**: Include both slash/no-slash channel routing support, frontend API normalization, Docker context node-module exclusions, the legacy payment-method adjustment, and the completion-ratio override.
- **Rationale**: These changes are small, isolated, and part of the final source lineage; each has a compatibility or build-hygiene purpose.

## Decision 6: Deployment boundary

- **Decision**: Do not build production images, restart containers, migrate the database, or alter live configuration in this issue.
- **Rationale**: The requested phase is source consolidation. Deployment requires a separate explicit approval and rollback plan.
