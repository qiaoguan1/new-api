# Tasks: All-Channel, All-Model Automatic Pricing

- [x] T001 Create Issue #3, add it to the project, and set `In Progress`
- [x] T002 Create the issue branch from the consolidated canonical source
- [x] T003 Document requirements, safety constraints, and verification plan
- [x] T004 Import the current production pricing worker without runtime data
- [x] T005 Add failing Python tests for dynamic discovery and safe pricing
- [x] T006 Add failing Go tests for configured completion-ratio precedence
- [x] T007 Implement dynamic model discovery and per-model audit gating
- [x] T008 Implement text and fixed-per-call 1.5x price calculations
- [x] T009 Implement group-ratio validation, movement limits, backup, and atomic updates
- [x] T010 Implement completion-ratio override semantics in NewAPI
- [x] T011 Run focused and full relevant tests
- [x] T012 Perform comprehensive and security review; fix every finding
- [x] T013 Run production dry-run and verify all decisions without database writes
- [x] T014 Back up and deploy the reviewed worker and NewAPI build with rollback verification
- [x] T015 Verify public runtime prices and the next scheduled execution path
- [x] T016 Commit, push, open the linked PR, and update project state
