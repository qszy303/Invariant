/-!
# Verified core: model, specs (`Prop`), checkers (`Bool`), and soundness/completeness proofs.

Everything here is core Lean 4 (no Mathlib). Money is `Int` cents, exact equality.
-/

namespace ProofCheck

inductive Kind where
  | start | inflow | outflow | subtotal | net | end_
  deriving DecidableEq, Repr, Inhabited

def Kind.isFlow : Kind → Bool
  | .inflow | .outflow => true
  | _ => false

structure Line where
  id : String
  kind : Kind
  values : List Int

structure Txn where
  id : String
  month : Int
  amount : Int
  assigned : List String

structure Input where
  months : Nat
  lines : List Line
  txns : List Txn

/-- Value of a line in month `m` (months are list positions; out of range reads as 0). -/
def Line.val (l : Line) (m : Nat) : Int := l.values.getD m 0

def sumInt (xs : List Int) : Int := xs.foldr (· + ·) 0

def flowSum (ls : List Line) (m : Nat) : Int := sumInt (ls.map (·.val m))

def flows (inp : Input) : List Line := inp.lines.filter (·.kind.isFlow)
def outflows (inp : Input) : List Line := inp.lines.filter (fun l => decide (l.kind = .outflow))

/-- Sum of the amounts of transactions in month `m` assigned to line `lid`. -/
def matchedTxns (inp : Input) (lid : String) (m : Nat) : List Txn :=
  inp.txns.filter fun t => decide (t.month = (m : Int) ∧ lid ∈ t.assigned)

def matchedSum (inp : Input) (lid : String) (m : Nat) : Int :=
  sumInt ((matchedTxns inp lid m).map (·.amount))

/-! ## Specifications -/

def RollForwardHolds (inp : Input) : Prop :=
  ∀ s ∈ inp.lines, s.kind = .start → ∀ e ∈ inp.lines, e.kind = .end_ →
    ∀ m, m < inp.months →
      e.val m = s.val m + flowSum (flows inp) m ∧
      (1 ≤ m → s.val m = e.val (m - 1))

def ArithmeticHolds (inp : Input) : Prop :=
  ∀ l ∈ inp.lines, ∀ m, m < inp.months →
    (l.kind = .subtotal → l.val m = flowSum (outflows inp) m) ∧
    (l.kind = .net → l.val m = flowSum (flows inp) m)

def TraceabilityHolds (inp : Input) : Prop :=
  ∀ l ∈ inp.lines, l.kind.isFlow = true → ∀ m, m < inp.months →
    l.val m = matchedSum inp l.id m

def NoDoubleCountHolds (inp : Input) : Prop :=
  ∀ t ∈ inp.txns, t.assigned.length ≤ 1

def CoverageHolds (inp : Input) : Prop :=
  ∀ t ∈ inp.txns, 1 ≤ t.assigned.length

/-! ## Boolean checkers -/

def checkRollForward (inp : Input) : Bool :=
  inp.lines.all fun s => decide (s.kind ≠ .start) ||
    inp.lines.all fun e => decide (e.kind ≠ .end_) ||
      (List.range inp.months).all fun m =>
        decide (e.val m = s.val m + flowSum (flows inp) m ∧
                (1 ≤ m → s.val m = e.val (m - 1)))

def checkArithmetic (inp : Input) : Bool :=
  inp.lines.all fun l => (List.range inp.months).all fun m =>
    decide ((l.kind = .subtotal → l.val m = flowSum (outflows inp) m) ∧
            (l.kind = .net → l.val m = flowSum (flows inp) m))

def checkTraceability (inp : Input) : Bool :=
  inp.lines.all fun l => !l.kind.isFlow ||
    (List.range inp.months).all fun m => decide (l.val m = matchedSum inp l.id m)

def checkNoDoubleCount (inp : Input) : Bool :=
  inp.txns.all fun t => decide (t.assigned.length ≤ 1)

def checkCoverage (inp : Input) : Bool :=
  inp.txns.all fun t => decide (1 ≤ t.assigned.length)

/-! ## Soundness and completeness -/

theorem checkNoDoubleCount_iff (inp : Input) :
    checkNoDoubleCount inp = true ↔ NoDoubleCountHolds inp := by
  simp [checkNoDoubleCount, NoDoubleCountHolds, List.all_eq_true]

theorem checkCoverage_iff (inp : Input) :
    checkCoverage inp = true ↔ CoverageHolds inp := by
  simp [checkCoverage, CoverageHolds, List.all_eq_true]

theorem checkRollForward_iff (inp : Input) :
    checkRollForward inp = true ↔ RollForwardHolds inp := by
  simp only [checkRollForward, RollForwardHolds, List.all_eq_true, List.mem_range,
    Bool.or_eq_true, decide_eq_true_eq, ne_eq]
  constructor
  · intro h s hs hk e he hke m hm
    rcases h s hs with h1 | h1
    · exact absurd hk h1
    · rcases h1 e he with h2 | h2
      · exact absurd hke h2
      · exact h2 m hm
  · intro h s hs
    by_cases hk : s.kind = .start
    · right
      intro e he
      by_cases hke : e.kind = .end_
      · right
        intro m hm
        exact h s hs hk e he hke m hm
      · left; exact hke
    · left; exact hk

theorem checkArithmetic_iff (inp : Input) :
    checkArithmetic inp = true ↔ ArithmeticHolds inp := by
  simp only [checkArithmetic, ArithmeticHolds, List.all_eq_true, List.mem_range,
    decide_eq_true_eq]

theorem checkTraceability_iff (inp : Input) :
    checkTraceability inp = true ↔ TraceabilityHolds inp := by
  simp only [checkTraceability, TraceabilityHolds, List.all_eq_true, List.mem_range,
    Bool.or_eq_true, Bool.not_eq_true', decide_eq_true_eq]
  constructor
  · intro h l hl hf m hm
    rcases h l hl with h1 | h1
    · rw [hf] at h1; exact absurd h1 (by decide)
    · exact h1 m hm
  · intro h l hl
    cases hf : l.kind.isFlow
    · left; rfl
    · right; intro m hm; exact h l hl hf m hm

/-! ## Witnesses -/

/-- First transaction assigned to more than one line, if any. -/
def noDoubleCountWitness (inp : Input) : Option Txn :=
  inp.txns.find? fun t => decide (1 < t.assigned.length)

theorem noDoubleCountWitness_sound (inp : Input) (t : Txn)
    (h : noDoubleCountWitness inp = some t) :
    t ∈ inp.txns ∧ ¬ (t.assigned.length ≤ 1) := by
  unfold noDoubleCountWitness at h
  have h1 := List.mem_of_find?_eq_some h
  have h2 : 1 < t.assigned.length := of_decide_eq_true (by simpa using List.find?_some h)
  exact ⟨h1, by omega⟩

theorem noDoubleCountWitness_complete (inp : Input) :
    noDoubleCountWitness inp = none ↔ NoDoubleCountHolds inp := by
  unfold noDoubleCountWitness NoDoubleCountHolds
  rw [List.find?_eq_none]
  simp

/-- First transaction assigned to no line, if any. -/
def coverageWitness (inp : Input) : Option Txn :=
  inp.txns.find? fun t => decide (t.assigned.length = 0)

theorem coverageWitness_sound (inp : Input) (t : Txn)
    (h : coverageWitness inp = some t) :
    t ∈ inp.txns ∧ ¬ (1 ≤ t.assigned.length) := by
  unfold coverageWitness at h
  have h1 := List.mem_of_find?_eq_some h
  have h2 : t.assigned.length = 0 := of_decide_eq_true (by simpa using List.find?_some h)
  exact ⟨h1, by omega⟩

theorem coverageWitness_complete (inp : Input) :
    coverageWitness inp = none ↔ CoverageHolds inp := by
  unfold coverageWitness CoverageHolds
  rw [List.find?_eq_none]
  constructor
  · intro h t ht
    have := h t ht
    have : ¬ t.assigned.length = 0 := fun e => this (decide_eq_true e)
    omega
  · intro h t ht
    have := h t ht
    simp only [decide_eq_true_eq]
    omega

end ProofCheck
