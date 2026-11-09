import ProofCheck.Spec

/-!
# Violation reporting and canonical JSON

Mirrors `proof/check/py_checker.py` and `CheckResult.to_canonical_json` exactly:
same emission order, same message templates, sorted keys, no whitespace,
`ensure_ascii` string escaping.
-/

namespace ProofCheck

structure Violation where
  invariant : String
  lineId : Option String := none
  monthIdx : Option Int := none
  txnIds : List String := []
  expected : Option Int := none
  actual : Option Int := none
  message : String

/-! ## Message formatting -/

def monthNames : Array String :=
  #["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def mon (m : Int) : String :=
  if 0 ≤ m ∧ m < 12 then monthNames.getD m.toNat "" else s!"month {m}"

/-- `1234567` -> `"1,234,567"`. -/
def groupThousands (n : Nat) : String :=
  let digits := (toString n).toList.reverse
  let rec go : List Char → Nat → List Char
    | [], _ => []
    | c :: cs, i => (if i > 0 && i % 3 == 0 then [',', c] else [c]) ++ go cs (i + 1)
  String.ofList (go digits 0).reverse

/-- Absolute value of cents as `1,234.56`. -/
def amt (c : Int) : String :=
  let a := c.natAbs
  let r := a % 100
  s!"{groupThousands (a / 100)}.{if r < 10 then "0" else ""}{r}"

def diff (actual expected : Int) (byMagnitude : Bool) : String :=
  let more := if byMagnitude then actual.natAbs > expected.natAbs else actual > expected
  s!"{amt (actual - expected)} {if more then "more" else "less"}"

/-! ## Violation generators (same emission order as the Python checker) -/

def rollForwardViolations (inp : Input) : List Violation :=
  inp.lines.flatMap fun s =>
    if s.kind = .start then
      inp.lines.flatMap fun e =>
        if e.kind = .end_ then
          (List.range inp.months).flatMap fun m =>
            let exp := s.val m + flowSum (flows inp) m
            let act := e.val m
            let v1 : List Violation :=
              if act ≠ exp then
                [{ invariant := "roll_forward", lineId := some e.id, monthIdx := some m,
                   expected := some exp, actual := some act,
                   message := s!"{e.id} in {mon m} is {diff act exp false} than {s.id} plus net flows" }]
              else []
            let v2 : List Violation :=
              if m ≥ 1 ∧ s.val m ≠ e.val (m - 1) then
                let exp2 := e.val (m - 1)
                let act2 := s.val m
                [{ invariant := "roll_forward", lineId := some s.id, monthIdx := some m,
                   expected := some exp2, actual := some act2,
                   message := s!"{s.id} in {mon m} is {diff act2 exp2 false} than end of {mon (m - 1 : Nat)}" }]
              else []
            v1 ++ v2
        else []
    else []

def arithmeticViolations (inp : Input) : List Violation :=
  let go (k : Kind) (parts : List Line) (what : String) : List Violation :=
    inp.lines.flatMap fun l =>
      if l.kind = k then
        (List.range inp.months).flatMap fun m =>
          let exp := flowSum parts m
          let act := l.val m
          if act ≠ exp then
            [{ invariant := "arithmetic", lineId := some l.id, monthIdx := some m,
               expected := some exp, actual := some act,
               message := s!"{l.id} in {mon m} is {diff act exp false} than the sum of its {what}" }]
          else []
      else []
  go .subtotal (outflows inp) "outflow lines" ++ go .net (flows inp) "inflow and outflow lines"

/-- Python lists inflow lines first, then outflow lines. -/
def orderedFlows (inp : Input) : List Line :=
  inp.lines.filter (fun l => decide (l.kind = .inflow)) ++ outflows inp

def txnsSortedById (ts : List Txn) : List Txn :=
  ts.mergeSort fun a b => compare a.id b.id != .gt

def traceabilityViolations (inp : Input) : List Violation :=
  (orderedFlows inp).flatMap fun l =>
    (List.range inp.months).flatMap fun m =>
      let matched := txnsSortedById (matchedTxns inp l.id m)
      let exp := matchedSum inp l.id m
      let act := l.val m
      if act ≠ exp then
        [{ invariant := "traceability", lineId := some l.id, monthIdx := some m,
           txnIds := matched.map (·.id), expected := some exp, actual := some act,
           message := s!"{l.id} in {mon m} is {diff act exp true} than its matched transactions" }]
      else []

def noDoubleCountViolations (inp : Input) : List Violation :=
  ((txnsSortedById inp.txns).filter fun t => decide (1 < t.assigned.length)).map fun t =>
    { invariant := "no_double_count", monthIdx := some t.month, txnIds := [t.id],
      message := s!"transaction {t.id} ({amt t.amount}) in {mon t.month} is assigned to {t.assigned.length} lines: {", ".intercalate (t.assigned.mergeSort fun a b => compare a b != .gt)}" }

def coverageViolations (inp : Input) : List Violation :=
  ((txnsSortedById inp.txns).filter fun t => decide (t.assigned.length = 0)).map fun t =>
    { invariant := "coverage", monthIdx := some t.month, txnIds := [t.id],
      message := s!"transaction {t.id} ({amt t.amount}) in {mon t.month} is not assigned to any line" }

/-- Python's sort key: (invariant, line_id or "", month_idx or -1), stable. -/
def violationLe (v w : Violation) : Bool :=
  match compare v.invariant w.invariant with
  | .lt => true
  | .gt => false
  | .eq =>
    match compare (v.lineId.getD "") (w.lineId.getD "") with
    | .lt => true
    | .gt => false
    | .eq => decide (v.monthIdx.getD (-1) ≤ w.monthIdx.getD (-1))

def allViolations (inp : Input) : List Violation :=
  (rollForwardViolations inp ++ arithmeticViolations inp ++ traceabilityViolations inp ++
    noDoubleCountViolations inp ++ coverageViolations inp).mergeSort violationLe

/-! ## Reporting is faithful for the per-transaction checks -/

theorem noDoubleCountViolations_sound (inp : Input) (v : Violation)
    (h : v ∈ noDoubleCountViolations inp) :
    ∃ t ∈ inp.txns, ¬ (t.assigned.length ≤ 1) ∧ v.txnIds = [t.id] := by
  unfold noDoubleCountViolations at h
  simp only [List.mem_map, List.mem_filter, decide_eq_true_eq] at h
  obtain ⟨t, ⟨ht, hlen⟩, rfl⟩ := h
  have hmem : t ∈ inp.txns := by
    have := (List.mem_mergeSort (l := inp.txns)).mp ht
    exact this
  exact ⟨t, hmem, by omega, rfl⟩

theorem noDoubleCountViolations_nil_iff (inp : Input) :
    noDoubleCountViolations inp = [] ↔ NoDoubleCountHolds inp := by
  unfold noDoubleCountViolations NoDoubleCountHolds
  rw [List.map_eq_nil_iff, List.filter_eq_nil_iff]
  constructor
  · intro h t ht
    have := h t ((List.mem_mergeSort).mpr ht)
    simp at this; omega
  · intro h t ht
    have := h t ((List.mem_mergeSort).mp ht)
    simp; omega

theorem coverageViolations_nil_iff (inp : Input) :
    coverageViolations inp = [] ↔ CoverageHolds inp := by
  unfold coverageViolations CoverageHolds
  rw [List.map_eq_nil_iff, List.filter_eq_nil_iff]
  constructor
  · intro h t ht
    have h1 := h t ((List.mem_mergeSort).mpr ht)
    have h2 : ¬ t.assigned.length = 0 := fun e => h1 (decide_eq_true e)
    omega
  · intro h t ht
    have h1 := h t ((List.mem_mergeSort).mp ht)
    intro hd
    have h2 : t.assigned.length = 0 := of_decide_eq_true hd
    omega

/-! ## Canonical JSON -/

def hex4 (n : Nat) : String :=
  let d (k : Nat) : Char := Nat.digitChar (k % 16)
  String.ofList [d (n / 4096), d (n / 256), d (n / 16), d n]

def escChar (c : Char) : String :=
  let n := c.toNat
  if c = '"' then "\\\""
  else if c = '\\' then "\\\\"
  else if n = 10 then "\\n"
  else if n = 13 then "\\r"
  else if n = 9 then "\\t"
  else if n = 8 then "\\b"
  else if n = 12 then "\\f"
  else if n < 0x20 then "\\u" ++ hex4 n
  else if n < 0x7f then c.toString
  else if n < 0x10000 then "\\u" ++ hex4 n
  else
    let k := n - 0x10000
    "\\u" ++ hex4 (0xD800 + k / 1024) ++ "\\u" ++ hex4 (0xDC00 + k % 1024)

def jstr (s : String) : String := "\"" ++ s.foldl (fun acc c => acc ++ escChar c) "" ++ "\""
def jopt {α} (f : α → String) : Option α → String
  | none => "null"
  | some a => f a
def jlist (xs : List String) : String := "[" ++ ",".intercalate xs ++ "]"

/-- Keys in sorted order, as `json.dumps(sort_keys=True)`. -/
def Violation.toJson (v : Violation) : String :=
  "{\"actual_cents\":" ++ jopt toString v.actual ++
  ",\"expected_cents\":" ++ jopt toString v.expected ++
  ",\"invariant\":" ++ jstr v.invariant ++
  ",\"line_id\":" ++ jopt jstr v.lineId ++
  ",\"message\":" ++ jstr v.message ++
  ",\"month_idx\":" ++ jopt toString v.monthIdx ++
  ",\"txn_ids\":" ++ jlist (v.txnIds.map jstr) ++ "}"

def canonicalJson (inp : Input) : String :=
  let vs := allViolations inp
  "{\"ok\":" ++ (if vs.isEmpty then "true" else "false") ++
  ",\"violations\":" ++ jlist (vs.map Violation.toJson) ++ "}"

end ProofCheck
