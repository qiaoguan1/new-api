# Implementation plan

Follow the established Spec Kit spec/plan/tasks structure; `specify check` has
confirmed the CLI is available. No unrelated template initialization or overwrite.

1. Freeze current production container identities, source hashes, persistence
   mounts, patrol error evidence and per-model route/usage metadata. Use bounded
   read-only queries and private temporary artifacts; no credentials in messages.
2. Parallelize191 image/callability,192 pricing/patrol,193 recovery changes with
   disjoint file ownership. Investigate before changing intentional safety gates.
3. Write failing regressions, implement minimal safe changes and run full impacted
   suites. Source drift is reconciled explicitly, never overwritten blindly.
4. Perform independent seven-criterion/security review and address every finding
   or track the real blocker. Publish sanitized artifacts to the relevant issues.
5. Take private rollback copies and use scoped immutable candidates. Verify no
   unknown active job can be replayed by restart/replacement. Retain old instances
   and data as needed; do not restore a database over current financial state.
6. Use free discovery, capability and invalid-input rejection checks first. Root
   allocates approvedCNY20 paid tests only to precisely quoted current routes and
   stores outcomes without leaking provider credentials.
7. Read back model availability/evidence levels, actual price actions, patrol
   coverage and backup integrity. Publish truthful source/PR and operator handoff;
   do not claim unreported CI or untested generation success.
