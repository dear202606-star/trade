import json,io
for n in ["curve.json","strategy_history.json","signal.json","equity.json"]:
    p="docs/data/"+n
    d=json.load(open(p,encoding="utf-8"))
    print("==",n, "keys:", list(d.keys())[:12])
    if "summary" in d: print("summary:", d["summary"])
    if "years" in d: print("years n=",len(d["years"]), "years:", [(y["year"], round(y.get("ret",0)*100,1) if y.get("ret") is not None else None) for y in d["years"]])
    if "points" in d: print("points n=",len(d["points"]), "first",d["points"][0], "last",d["points"][-1])
    if "start" in d: print("start/end", d.get("start"), d.get("end"))
