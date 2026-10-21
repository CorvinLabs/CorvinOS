"""Adversarial driver (attacker side), round 2: envelope edges against a peer's A2A ingress.

Builds envelopes with the sender's own helper and posts them raw, so every refusal is the receiver's
verdict on the wire: attachments at the count/size/name/digest edges, the issued_at window (±300 s),
a replay after the envelope's own TTL ran out. Prints one ``label: (http, status, keys, data)`` line
per probe; the fresh-system E2E asserts on them.
"""
import base64
import hashlib
import hmac as _hmac
import json
import sys
import time
import uuid

sys.path.insert(0, "/home/tester/CorvinOS/corvin_operator/bridges/shared")
import httpx  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402

kid = sys.argv[1]
url = sys.argv[2]
cfg = json.load(open(f"/home/tester/CorvinOS/corvin_operator/cowork/remote_endpoints/{kid}.json"))
iid = json.load(open("/home/tester/CorvinOS/.corvin/global/instance_id.json"))["instance_id"]


def att(name, payload=b"x", sha=None):
    return {"name": name, "mime": "application/octet-stream", "content_b64": base64.b64encode(payload).decode(),
            "sha256": sha or hashlib.sha256(payload).hexdigest()}


def build(instruction="Reply with exactly the word EDGE", attachments=None, ttl_s=60):
    return rts.RemoteTriggerSender._build_envelope(
        task_id=str(uuid.uuid4()), nonce=uuid.uuid4().hex, origin_id=cfg["origin_id_for_send"],
        instruction=instruction, result_schema={"output": "string"}, ttl_s=ttl_s,
        hmac_key_hex=cfg["hmac_key"], sender_instance_id=iid, attachments=attachments or [])


def resign(env):
    payload = {k: v for k, v in env.items() if k != "signature"}
    env["signature"] = _hmac.new(bytes.fromhex(cfg["hmac_key"]),
                                 json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
                                 hashlib.sha256).hexdigest()
    return env


def post(e):
    r = httpx.post(url, json=e, timeout=120)
    d = r.json()
    return r.status_code, d.get("status"), sorted(d.keys()), d.get("data")


print("16 files   :", post(build(attachments=[att(f"f{i}.bin") for i in range(16)])))
print("17 files   :", post(build(attachments=[att(f"f{i}.bin") for i in range(17)])))
print("sha mismatch:", post(build(attachments=[att("a.bin", b"real", sha="00" * 32)])))
print("traversal  :", post(build(attachments=[att("../../etc/passwd")])))
print("dup name   :", post(build(attachments=[att("a.bin"), att("a.bin")])))
for label, offset in (("skew -290s", -290), ("skew +290s", 290), ("skew -310s", -310), ("skew +310s", 310)):
    env = build()
    env["issued_at"] = time.time() + offset
    print(f"{label} :", post(resign(env)))
short = build(ttl_s=5)
print("ttl5 first :", post(short))
time.sleep(7)
print("ttl5 replay:", post(short))
