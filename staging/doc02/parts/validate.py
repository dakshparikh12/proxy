#!/usr/bin/env python3
import os
import yaml

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "acceptance", "doc02")
r = yaml.safe_load(open(os.path.join(OUT, "requirements", "requirements.yaml")))
c = yaml.safe_load(open(os.path.join(OUT, "criteria", "criteria.yaml")))
assert isinstance(r, list) and isinstance(c, list)
rids = {x["requirement_id"] for x in r}
cids = {x["criterion_id"] for x in c}
assert len(rids) == len(r), "duplicate requirement ids"
assert len(cids) == len(c), "duplicate criterion ids"
# fields present
for x in r:
    for k in ("requirement_id", "authority_type", "source_location", "source_quote",
              "normalized_statement", "criticality", "status"):
        assert k in x, f"req {x.get('requirement_id')} missing {k}"
missing_oracle = [x["criterion_id"] for x in c if not x.get("primary_oracle")]
missing_thresh = [x["criterion_id"] for x in c if not x.get("thresholds")]
no_authority = [x["criterion_id"] for x in c if not x.get("authority_refs")]
crit_p0 = sum(1 for x in c if x.get("criticality") == "P0")
crit_p1 = sum(1 for x in c if x.get("criticality") == "P1")
crit_p2 = sum(1 for x in c if x.get("criticality") == "P2")
blocking = sum(1 for x in c if x.get("blocking") is True)
print(f"requirements={len(r)} unique_ids={len(rids)}")
print(f"criteria={len(c)} unique_ids={len(cids)}")
print(f"criteria criticality: P0={crit_p0} P1={crit_p1} P2={crit_p2}  blocking={blocking}")
print(f"criteria missing primary_oracle: {missing_oracle}")
print(f"criteria missing thresholds: {missing_thresh}")
print(f"criteria missing authority_refs: {no_authority}")
print("VALID" if not (missing_oracle or no_authority) else "CHECK ABOVE")
