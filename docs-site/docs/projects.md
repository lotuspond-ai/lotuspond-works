# Projects and scenarios

A project groups scenarios and evaluation history for one agent or workflow boundary. Create separate projects when ownership, data access, release policy, or production evidence differs.

A useful scenario records:

- a stable name and purpose;
- the input or fixture reference;
- observable expected behavior and prohibited behavior;
- criticality and the reason for it;
- evaluator configuration and explicit thresholds;
- the environment and agent revision under test.

Freeze the scenario definition with each run. Editing a scenario should create a new test definition for future runs rather than reinterpret retained evidence. Keep confidential prompts and personal data out of free-text descriptions; use controlled artifact references where the backend supports them.

External execution contracts are versioned separately. Registration of an endpoint records configuration only and is not compatibility evidence.
