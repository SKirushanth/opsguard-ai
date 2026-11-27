"""Black-box checks against the public origin. Never log bodies or credentials."""
import os
import uuid
from contextlib import contextmanager

import httpx

BASE = "https://opsguard-web.calmdesert-948602bd.centralindia.azurecontainerapps.io"
PUBLIC = "/api/public-demo"
client = httpx.Client(base_url=BASE, timeout=80)


def call(method, path, headers=None, data=None):
    r = client.request(method, path, headers=headers, json=data)
    r.raise_for_status()
    return r.json() if r.content else None


@contextmanager
def operator():
    password = os.environ["OPERATOR_PASSWORD"]
    if not password:
        raise RuntimeError("Configure the protected operator smoke credential.")
    token = call("POST", "/api/session", data={"password": password})["token"]
    h = {"Authorization": "Bearer " + token}
    try:
        yield h
    finally:
        call("DELETE", "/api/session", h)


def guard():
    with operator() as h:
        if any(a["id"] != "demo" for a in call("GET", "/api/applications", h)) or call("GET", "/api/incidents", h):
            raise RuntimeError("Operator records present. Preserve them before replacing local SQLite.")


def ai(required):
    with operator() as h:
        health = call("GET", "/api/services/orders/health", h)
        r = client.post("/api/chat", headers=h, json={"service_id":"orders", "session_id":str(uuid.uuid4()), "message":"Check Orders health"})
        if r.status_code in (502,503,504) and not required:
            print("::warning::Live operator AI unavailable; public deterministic analysis verified.")
            return
        r.raise_for_status()
        assert "Monitored health for orders: " + health["status"] in r.json()["response"]


def demo():
    assert client.get("/healthz").is_success
    def visitor():
        return {"Authorization":"Bearer " + call("POST", PUBLIC+"/session")["token"]}
    a,b = visitor(),visitor()
    def check(method,path,h=a,data=None):
        return call(method,PUBLIC+path,h,data)
    assert {s["id"] for s in check("GET","/services")["services"]} == {"auth","orders","notifications"}
    check("POST","/demo/scenario",data={"service_id":"auth","scenario":"token_validation_failure"})
    q = {"service_id":"auth","session_id":"smoke","message":"Check health"}
    # Previous release has deterministic /analysis but no explicit local route.
    local_path = "/analysis/local"
    if client.post(PUBLIC+local_path, headers=a, json=q).status_code == 404:
        local_path = "/analysis"
    answer = check("POST",local_path,data=q)["response"]
    assert "HTTP 401" in answer and "HTTP 200" in check("POST",local_path,b,q)["response"]
    assert check("GET","/services/auth/logs")["logs"] and check("GET","/services/auth/metrics")["metrics"]
    draft=check("POST","/incidents/draft",data=dict(application_id="demo",service_id="auth",session_id="smoke",investigation=answer))
    row=check("POST","/incidents",data=dict(draft,approval_id=str(uuid.uuid4()),confirmed=True))
    assert check("PATCH","/incidents/"+row["id"],data={"status":"resolved"})["status"]=="resolved"
    assert not check("GET","/incidents",b)
    check("POST","/demo/reset",data={})
    assert not check("GET","/incidents") and check("GET","/services/auth/health")["status"]=="healthy"
    assert client.get("/api/incidents",headers=a).status_code==401 and client.post("/api/chat",headers=a,json=q).status_code==401


def browser():
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    options=webdriver.ChromeOptions()
    for arg in ("--headless=new","--no-sandbox","--disable-dev-shm-usage","--window-size=1440,1000"):
        options.add_argument(arg)
    with webdriver.Chrome(options=options) as driver:
        driver.get(BASE)
        wait=WebDriverWait(driver,45)
        wait.until(lambda d:"Demo Mode" in d.find_element(By.TAG_NAME,"body").text and "Order Service" in d.find_element(By.TAG_NAME,"body").text)
        for label in ("Order Service","Investigate"):
            wait.until(lambda d:next((b for b in d.find_elements(By.TAG_NAME,"button") if b.is_displayed() and b.text.strip()==label),False)).click()
        wait.until(lambda d:"Demo analysis" in d.find_element(By.TAG_NAME,"body").text)
