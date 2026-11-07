# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.387 | +14.8% | 0.263 | +8.1% | 0.521 | +5.0% | 35% | 414 (409) |
| arav_kyc_1 | 0.483 | +34.6% | 0.265 | +23.2% | 0.627 | +4.4% | 31% | 441 (352) |
| arav_renewal_0 | 0.474 | +16.9% | 0.285 | +8.0% | 0.588 | +7.3% | 41% | 466 (408) |
| kest_cancel_2 | 0.390 | +15.9% | 0.264 | +8.8% | 0.522 | +4.7% | 36% | 449 (418) |
| kest_kyc_1 | 0.454 | +27.6% | 0.251 | +17.1% | 0.628 | +5.1% | 31% | 442 (370) |
| kest_renewal_0 | 0.470 | +17.0% | 0.284 | +7.3% | 0.587 | +7.8% | 40% | 460 (403) |
| nort_cancel_2 | 0.397 | +16.9% | 0.264 | +8.3% | 0.529 | +6.1% | 41% | 444 (368) |
| nort_kyc_1 | 0.485 | +36.2% | 0.267 | +24.4% | 0.622 | +4.5% | 31% | 479 (318) |
| nort_renewal_0 | 0.467 | +15.1% | 0.284 | +7.4% | 0.584 | +6.6% | 39% | 454 (419) |
| tide_cancel_2 | 0.387 | +12.9% | 0.264 | +7.9% | 0.521 | +3.9% | 31% | 460 (401) |
| tide_kyc_1 | 0.455 | +27.7% | 0.251 | +16.9% | 0.627 | +4.8% | 31% | 383 (376) |
| tide_renewal_0 | 0.470 | +16.2% | 0.285 | +8.0% | 0.584 | +6.9% | 33% | 480 (425) |
| ALL | 0.443 | +20.9% | 0.269 | +11.6% | 0.578 | +6.0% | 35% | 5372 (4667) |

## Changes applied by the controller

56 applied — neutral: 19, beneficial: 37

- nort_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.015 → **neutral**
- nort_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- kest_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.015 → **neutral**
- kest_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- arav_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.015 → **neutral**
- arav_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- tide_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.015 → **neutral**
- tide_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.016 → **neutral**
- nort_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.017 → **neutral**
- nort_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- kest_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.017 → **neutral**
- kest_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- arav_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.033 → **beneficial**
- arav_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- tide_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.060 → **beneficial**
- tide_cancel_2 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.023 → **beneficial**
- nort_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.043 → **beneficial**
- nort_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.004 → **neutral**
- kest_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.043 → **beneficial**
- kest_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.004 → **neutral**
- arav_renewal_0 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.028 → **beneficial**
- arav_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.004 → **neutral**
- tide_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.004 → **neutral**
- nort_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.060 didΔ=+0.083 → **beneficial**
- nort_cancel_2 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nort_renewal_0 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- tide_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.060 didΔ=+0.090 → **beneficial**
- nort_cancel_2 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.035 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.060 didΔ=+0.098 → **beneficial**
- arav_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.060 didΔ=+0.098 → **beneficial**
- arav_cancel_2 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- tide_cancel_2 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- tide_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.061 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.035 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.039 didΔ=+0.000 → **beneficial**
- arav_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.285 → **beneficial**
- arav_cancel_2 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.035 didΔ=+0.000 → **beneficial**
- nort_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.316 → **beneficial**
- kest_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.237 → **beneficial**
- nort_cancel_2 v7 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.062 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v6 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.080 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v7 (controller_approved) dims=['cohorts', 'voice', 'schedule'] engageΔ=+0.051 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v7 (controller_approved) dims=['voice'] engageΔ=+0.046 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v6 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.080 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v7 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.062 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.056 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v6 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.080 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v3 (controller_approved) dims=['welcome_message', 'voice', 'caller_id'] engageΔ=+0.056 didΔ=+0.274 → **beneficial**
- tide_cancel_2 v8 (controller_approved) dims=['cohorts', 'welcome_message', 'schedule'] engageΔ=-0.009 didΔ=+0.000 → **neutral**
- nort_kyc_1 v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.056 didΔ=+0.000 → **beneficial**
- arav_kyc_1 v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.057 didΔ=+0.000 → **beneficial**
- arav_cancel_2 v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.011 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v5 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.080 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 37,
  "decisions": {
    "rejected": 4,
    "approved": 33,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 4
  },
  "applied_via_approval": 33,
  "auto_applied": 23,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nort_kyc_1": 1,
    "kest_kyc_1": 1,
    "arav_kyc_1": 1,
    "tide_renewal_0": 2,
    "tide_kyc_1": 1,
    "nort_renewal_0": 1,
    "nort_cancel_2": 2,
    "kest_renewal_0": 1,
    "kest_cancel_2": 2,
    "arav_renewal_0": 1,
    "arav_cancel_2": 2,
    "tide_cancel_2": 2
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nort_renewal_0:welcome_message=W3": {
    "3": 0.8,
    "4": 0.799
  },
  "kest_renewal_0:welcome_message=W3": {
    "3": 0.802,
    "4": 0.801
  },
  "arav_renewal_0:welcome_message=W3": {
    "3": 0.8,
    "4": 0.804
  },
  "tide_renewal_0:welcome_message=W3": {
    "4": 0.798
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0