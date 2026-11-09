#!/usr/bin/env python3
"""Install staged E5 designs (build/e5_stage/<case>/) into the repository before any recovery run:
spec -> bench2/specs/e5/<case>.json, ref.py -> c3/e5_refs/<case>.py, observe.json -> c3/observe.json[<case>],
expected.txt -> bench2/e5_expected/<case>.txt.   usage: e5_install.py CASE [CASE ...]"""
import json, os, shutil, sys
E = "/home/jiahao/hal/algorithm_recovery/evaluation"
S = "/home/jiahao/hal/build/e5_stage"
obs_path = f"{E}/program_recovery_python/observe.json"
obs = json.load(open(obs_path))
for case in sys.argv[1:]:
    spec = json.load(open(f"{S}/{case}/spec.json"))
    assert spec["case"] == case and os.path.exists(spec["synth"]), case
    os.makedirs(f"{E}/library_designs/specs/e5", exist_ok=True); os.makedirs(f"{E}/program_recovery_python/e5_refs", exist_ok=True)
    os.makedirs(f"{E}/library_designs/e5_expected", exist_ok=True)
    json.dump(spec, open(f"{E}/library_designs/specs/e5/{case}.json", "w"), indent=1)
    shutil.copy(f"{S}/{case}/ref.py", f"{E}/program_recovery_python/e5_refs/{case}.py")
    shutil.copy(f"{S}/{case}/expected.txt", f"{E}/library_designs/e5_expected/{case}.txt")
    assert case not in obs or obs[case] == json.load(open(f"{S}/{case}/observe.json")), f"observe.json already has {case}"
    obs[case] = json.load(open(f"{S}/{case}/observe.json"))
    print("installed", case)
json.dump(obs, open(obs_path, "w"), indent=1)
