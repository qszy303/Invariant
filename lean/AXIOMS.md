# Axioms

Output of `lake env lean AxiomsCheck.lean` (also printed by `make lean`). Only standard axioms; no `sorry`, no new `axiom`.

```
'ProofCheck.checkRollForward_iff' depends on axioms: [propext, Quot.sound]
'ProofCheck.checkArithmetic_iff' depends on axioms: [propext, Quot.sound]
'ProofCheck.checkTraceability_iff' depends on axioms: [propext, Quot.sound]
'ProofCheck.checkNoDoubleCount_iff' depends on axioms: [propext, Quot.sound]
'ProofCheck.checkCoverage_iff' depends on axioms: [propext, Quot.sound]
'ProofCheck.noDoubleCountWitness_sound' depends on axioms: [propext, Quot.sound]
'ProofCheck.noDoubleCountWitness_complete' depends on axioms: [propext, Quot.sound]
'ProofCheck.coverageWitness_sound' depends on axioms: [propext, Quot.sound]
'ProofCheck.coverageWitness_complete' depends on axioms: [propext, Quot.sound]
'ProofCheck.noDoubleCountViolations_sound' depends on axioms: [propext, Classical.choice, Quot.sound]
'ProofCheck.noDoubleCountViolations_nil_iff' depends on axioms: [propext, Classical.choice, Quot.sound]
'ProofCheck.coverageViolations_nil_iff' depends on axioms: [propext, Classical.choice, Quot.sound]
```
