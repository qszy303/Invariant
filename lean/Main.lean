import Lean.Data.Json
import ProofCheck

open Lean ProofCheck

def parseKind : String → Except String Kind
  | "start" => .ok .start
  | "inflow" => .ok .inflow
  | "outflow" => .ok .outflow
  | "subtotal" => .ok .subtotal
  | "net" => .ok .net
  | "end" => .ok .end_
  | k => .error s!"unknown line kind: {k}"

def parseLine (j : Json) : Except String Line := do
  let id ← j.getObjValAs? String "line_id"
  let kind ← parseKind (← j.getObjValAs? String "kind")
  let values ← j.getObjValAs? (List Int) "values_cents"
  return { id, kind, values }

def parseTxn (j : Json) : Except String Txn := do
  let id ← j.getObjValAs? String "id"
  let month ← j.getObjValAs? Int "month_idx"
  let amount ← j.getObjValAs? Int "amount_cents"
  let assigned ← j.getObjValAs? (List String) "assigned_line_ids"
  return { id, month, amount, assigned }

def parseInput (j : Json) : Except String Input := do
  let months ← j.getObjValAs? Int "months"
  if months < 0 then throw "months must be non-negative"
  let lines ← (← j.getObjValAs? (Array Json) "lines").toList.mapM parseLine
  let txns ← (← j.getObjValAs? (Array Json) "txns").toList.mapM parseTxn
  for l in lines do
    if l.values.length ≠ months.toNat then
      throw s!"line {l.id} has {l.values.length} values, expected {months}"
  return { months := months.toNat, lines, txns }

partial def readAll (h : IO.FS.Stream) (acc : String := "") : IO String := do
  let chunk ← h.getLine
  if chunk.isEmpty then return acc else readAll h (acc ++ chunk)

/-- `proofcheck [checkinput.json]`: reads CheckInput JSON (file or stdin), prints canonical
CheckResult JSON (no trailing newline) to stdout. -/
def main (args : List String) : IO UInt32 := do
  let text ← match args with
    | [path] => IO.FS.readFile path
    | [] => do readAll (← IO.getStdin)
    | _ => do IO.eprintln "usage: proofcheck [checkinput.json]"; return 2
  match Json.parse text >>= parseInput with
  | .error e => IO.eprintln s!"proofcheck: invalid input: {e}"; return 2
  | .ok inp =>
    IO.print (canonicalJson inp)
    return 0
