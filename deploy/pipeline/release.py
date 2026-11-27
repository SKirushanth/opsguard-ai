"""Single-revision deployment to the existing apps only."""
import copy
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import smoke

RG="opsguard-rg"
ACR="opsguardacr39424.azurecr.io"
SHA=os.getenv("GITHUB_SHA","")
BOOT_TAG="demo-20261127t130811"
BOOT_SHA="7daf9eaf02921d5579564ff9928ae0a7dad5eaee"
AGENT=["agent","investigation.py","backend/requirements.txt","requirements.txt"]
PATHS={"backend":["backend","investigation.py",".dockerignore"],"frontend":["frontend"]}
NAMES={"backend":"opsguard-api","frontend":"opsguard-web"}


def run(*args):
    return subprocess.check_output(args,text=True).strip()


def az(*args):
    output=run("az",*args,"--only-show-errors","-o","json")
    return json.loads(output) if output else {}


def summary(message):
    print(message,flush=True)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a") as f:
            f.write(message+"\n\n")


def changed(start,paths):
    assert re.fullmatch("[a-f0-9]{40}",start)
    return bool(run("git","diff","--name-only",start,SHA,"--",*paths))


def snapshot(component):
    name=NAMES[component]
    a=az("containerapp","show","-g",RG,"-n",name)
    props=a["properties"];cfg=props["configuration"];ing=cfg["ingress"]
    revision=props["latestReadyRevisionName"]
    assert props["latestRevisionName"]==revision and cfg["activeRevisionsMode"]=="Single"
    assert ing["external"]==(component=="frontend") and not ing.get("allowInsecure")
    r = az("containerapp","revision","show","-g",RG,"-n",name,"--revision",revision)["properties"]
    assert r["healthState"]=="Healthy" and r["trafficWeight"]==100
    t=r["template"];c=t["containers"][0]
    assert len(t["containers"])==1 and not t.get("volumes") and not c.get("volumeMounts")
    if component=="backend":
        assert t["scale"]["minReplicas"]==t["scale"]["maxReplicas"]==1
        assert any(e["name"]=="PROTECT_READS" and e.get("value")=="true" for e in c["env"])
    repo=ACR+"/opsguard-"+component
    image=c["image"]
    if image.startswith(repo+"@sha256:"):
        source=t["revisionSuffix"].split("-")[-1]
        digest=image.split("@")[1]
    else:
        assert image.startswith(repo+":")
        tag=image.split(":")[-1]; source=BOOT_SHA if tag==BOOT_TAG else tag
        digest=re.search(r"(?m)^Digest:\s+(sha256:[0-9a-f]{64})",run("docker","buildx","imagetools","inspect",image))[1]
    assert re.fullmatch("[0-9a-f]{40}",source)
    summary(f"Baseline {revision}: {repo}@{digest}, source {source}")
    return dict(name=name,id=a["id"],revision=revision,template=t,source=source,repo=repo,digest=digest)


def activate(base,image,suffix):
    t=copy.deepcopy(base["template"]);t["revisionSuffix"]=suffix;t["containers"][0]["image"]=image
    uri="https://management.azure.com"+base["id"]+"?api-version=2024-03-01"
    with tempfile.NamedTemporaryFile(mode="w",suffix=".json") as body:
        json.dump({"properties":{"template":t}},body);body.flush()
        az("rest","--method","patch","--url",uri,"--body","@"+body.name,"--output","none")
    expected=base["name"]+"--"+suffix
    for _ in range(60):
        p=az("containerapp","show","-g",RG,"-n",base["name"])["properties"]
        if p["latestReadyRevisionName"]==expected:
            r=az("containerapp","revision","show","-g",RG,"-n",base["name"],"--revision",expected)["properties"]
            if r["healthState"]=="Healthy" and r["trafficWeight"]==100:
                assert r["template"]["containers"][0]["image"]==image
                summary("Healthy and serving: "+expected)
                return
        time.sleep(5)
    raise RuntimeError("Readiness timeout: "+expected)


def build(base,component):
    image=base["repo"]+":"+SHA
    # A retry reuses the image already published for this commit.
    probe=subprocess.run(["docker","buildx","imagetools","inspect",image],capture_output=True,text=True)
    if probe.returncode:
        subprocess.run(["docker","buildx","build","--platform","linux/amd64","--push",
            "--cache-from","type=registry,ref="+base["repo"]+"@"+base["digest"],"--cache-to","type=inline",
            "-t",image,"-f",component+"/Dockerfile","." if component=="backend" else "frontend"],check=True)
    run("docker","buildx","imagetools","inspect",image)
    return image


def transaction(plan,verify):
    touched=[]
    try:
        for component,base,image in plan:
            if component=="backend":
                smoke.guard()
            touched.append((component,base))
            activate(base,image,"ci-"+SHA[:12]+"-"+os.environ["GITHUB_RUN_ATTEMPT"])
            smoke.demo()
        verify()
    except Exception:
        for component,base in reversed(touched):
            if component=="backend":
                smoke.guard()
            activate(base,base["repo"]+"@"+base["digest"],"rb-"+os.environ["GITHUB_RUN_ID"][-4:]+"-"+base["source"])
        if touched:
            smoke.demo()
            smoke.browser()
        summary("Release failed. Completed rollback checks for previously touched apps.")
        raise


def main():
    assert re.fullmatch("[a-f0-9]{40}",SHA)
    latest=run("git","ls-remote","origin","refs/heads/main").split()[0]
    if latest!=SHA:
        run("git","fetch","--quiet","origin","refs/heads/main")
    if latest!=SHA and run("git","diff","--name-only",SHA,"FETCH_HEAD","--",*PATHS["frontend"],*PATHS["backend"],*AGENT):
        summary("Superseded by a newer main commit; skipping activation.")
        return
    verified=os.environ["WSO2_VERIFIED_SHA"]
    pending=changed(verified,AGENT)
    plan=[]
    snapshots={c:snapshot(c) for c in PATHS}
    for component,base in snapshots.items():
        if not changed(base["source"],PATHS[component]):
            continue
        if component=="backend" and (changed(base["source"],AGENT) or changed(base["source"],["backend/app/routes/chat.py"])):
            if pending:
                raise RuntimeError("Deploy and attest the matching WSO2 version before backend activation.")
            smoke.ai(True)
        plan.append((component,base,build(base,component)))
    if pending:
        summary("WSO2 checkpoint pending. Agent changes require manual deployment and trace verification.")
    def verify():
        smoke.demo()
        smoke.browser()
        smoke.ai(any(c=="backend" and changed(b["source"],["backend/app/routes/chat.py",*AGENT]) for c,b,_ in plan))
    transaction(plan,verify)


if __name__=="__main__":
    main()
