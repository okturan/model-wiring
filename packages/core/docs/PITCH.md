# Pitch: provider choice as infrastructure

Every AI application needs roughly the same unglamorous plumbing: enumerate
providers, search thousands of models, filter capabilities, choose a reasoning
mode, distinguish API billing from subscriptions, locate a credential, refresh
OAuth safely, and show the result in a UI. Agent harnesses repeatedly implement
this inside their own runtime, making it hard for unrelated applications to
reuse.

Model Wiring extracts only that shared seam.

```text
                   one selection contract
                            |
       +--------------------+--------------------+
       |                    |                    |
       v                    v                    v
  data pipeline        coding harness       desktop app
  own executor         own agent loop       own workflow
       |                    |                    |
       +------------ provider/model/auth plan --+
                            |
                    model-wiring
```

The useful unit is not an agent. It is a portable, credential-free plan plus a
short-lived credential lease at the execution boundary. That keeps the module
Unix-like, composable, and safe to embed in apps that already know how to run
their work.
