"""Adversarial driver (attacker side): replay and tamper against a peer's A2A ingress.

Builds ONE valid envelope with the sender's own helper, posts it (valid), posts the same bytes again
(replay) and a byte-flipped copy (tamper). Expects: ok / rejected / rejected — and the two refusals
must be byte-identical in shape (opaque), never naming the failed check.
"""
import json
import sys
import uuid

sys.path.insert(0, "/home/tester/CorvinOS/corvin_operator/bridges/shared")
import httpx
import remote_trigger_sender as rts

kid = sys.argv[1]
url = sys.argv[2]
cfg = json.load(open(f"/home/tester/CorvinOS/corvin_operator/cowork/remote_endpoints/{kid}.json"))
iid = json.load(open("/home/tester/CorvinOS/.corvin/global/instance_id.json"))["instance_id"]
env = rts.RemoteTriggerSender._build_envelope(
    task_id=str(uuid.uuid4()), nonce=uuid.uuid4().hex, origin_id=cfg["origin_id_for_send"],
    instruction="Reply with exactly the word REPLAYTEST", result_schema={"output": "string"}, ttl_s=60,
    hmac_key_hex=cfg["hmac_key"], sender_instance_id=iid, attachments=[])
def post(e):
    r = httpx.post(url, json=e, timeout=120)
    d = r.json()
    return r.status_code, d.get("status"), sorted(d.keys()), d.get("data")
print("valid :", post(env))
print("replay:", post(env))
bad = dict(env)
bad["instruction"] = bad["instruction"].replace("REPLAYTEST", "REPLAYTESX")
print("tamper:", post(bad))
unknown = dict(env)
unknown["origin_id"] = "ffffffff-0000-0000-0000-000000000000"
unknown["nonce"] = uuid.uuid4().hex
print("unknown origin:", post(unknown))
