# Implementation Plan

1. Extend the authenticated `/channels` search schema with `action=create` and initialize the existing create dialog from it.
2. Add add/manage/upstream-collection actions and two-step guidance to the integrated Channel Monitor.
3. Add a deterministic, atomic deployment patch for the standalone monitor header.
4. Cover the standalone transform and query-to-dialog mapping with focused tests.
5. Back up production assets, deploy the reviewed frontend/static patch, and verify permissions and navigation.
