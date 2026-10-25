"""LIVE: what does a REAL A2A worker do with each media type? (PLAN-0965 F-04)

The protocol carries attachments of any media type as bytes (`a2a_attachments.py`: the media type is a label, not checked).
What the receiving worker can DO with a file depends on its engine and tools, and was never measured per type. This test
sends one small file of each type through a real HTTP receiver and a real `claude` worker (read access granted, nothing else)
and records what came back, in `$CORVIN_A2A_MEDIA_OUT` (default: the pytest tmp dir, printed at the end).

Gated: `CORVIN_A2A_MEDIA_LIVE=1`. Spends real tokens (one worker run per media type) and needs the `claude` CLI signed in.

Assertions encode only what must hold for the protocol and what a text-bearing file proves:
  * the envelope with the attachment is accepted and answered (every type);
  * for png, pdf and csv the worker's answer quotes the marker text that exists ONLY inside the file;
  * for docx, wav, mp3 and mp4 nothing is asserted about content: the result is recorded as a finding, because an answer of
    "could not inspect" is the honest outcome with a read-only grant and the test must not turn it into a failure or a pretence.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("CORVIN_A2A_MEDIA_LIVE") != "1",
                                reason="set CORVIN_A2A_MEDIA_LIVE=1 (real claude worker, spends tokens)")

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

import a2a_attachments as att  # noqa: E402


def _png(marker: str) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (480, 120), "white")
    ImageDraw.Draw(im).text((20, 45), marker, fill="black")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _pdf(marker: str) -> bytes:
    stream = f"BT /F1 18 Tf 40 100 Td ({marker}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 160] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _docx(marker: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   f'<w:p><w:r><w:t>{marker}</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * ((i // 18) % 2 * 2 - 1))) for i in range(8000)))
    return buf.getvalue()


def _ffmpeg(args: list[str], suffix: str) -> bytes:
    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("ffmpeg not installed")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / f"o{suffix}"
        subprocess.run([exe, "-y", "-v", "error", *args, str(out)], check=True, timeout=60)
        return out.read_bytes()


def _cases() -> list[dict]:
    return [
        {"name": "image.png", "mime": "image/png", "marker": "CORVIN-PNG-4711", "bytes": _png("CORVIN-PNG-4711"), "must_quote": True},
        {"name": "document.pdf", "mime": "application/pdf", "marker": "CORVIN-PDF-7788", "bytes": _pdf("CORVIN-PDF-7788"), "must_quote": True},
        {"name": "document.docx", "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         # Measured 2026-10-25: with read access only the worker has no unzip tool, so the deflate-compressed
         # document text is out of reach. Recorded as a finding, not asserted either way.
         "marker": "CORVIN-DOCX-5521", "bytes": _docx("CORVIN-DOCX-5521"), "must_quote": False},
        {"name": "table.csv", "mime": "text/csv", "marker": "CORVIN-CSV-9034", "bytes": b"CORVIN-CSV-9034,b\n1,2\n", "must_quote": True},
        {"name": "clip.wav", "mime": "audio/wav", "marker": "", "bytes": _wav(), "must_quote": False},
        {"name": "clip.mp3", "mime": "audio/mpeg", "marker": "",
         "bytes": _ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-b:a", "32k"], ".mp3"), "must_quote": False},
        {"name": "clip.mp4", "mime": "video/mp4", "marker": "",
         "bytes": _ffmpeg(["-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10", "-pix_fmt", "yuv420p", "-b:v", "60k"], ".mp4"), "must_quote": False},
    ]


def test_each_media_type_through_a_real_worker(tmp_path):
    import a2a_http_server
    import remote_trigger_sender as rts
    import a2a_test_tier

    a2a_test_tier.pin_member_tier()
    os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"
    origins, endpoints = tmp_path / "origins", tmp_path / "endpoints"
    origins.mkdir()
    endpoints.mkdir()
    k_hmac, k_recv = secrets.token_hex(32), secrets.token_hex(32)
    for d, name, cfg in ((origins, "req", {
            "origin_id": "req", "hmac_key": k_hmac, "recv_key": k_recv, "enabled": True, "max_ttl_s": 300,
            "allowed_personas": [], "spawn_worker": True, "allow_read_files": True}),):
        (d / f"{name}.json").write_text(json.dumps(cfg))
        (d / f"{name}.json").chmod(0o600)
    import unittest.mock as mock
    se = mock.MagicMock()
    se.write_event = mock.MagicMock(return_value={"hash": "abc"})
    server = a2a_http_server.build_server(host="127.0.0.1", port=0, origins_dir=origins, engine_factory=None,
                                          instance_id="iid-media-receiver", forge_se=se)
    a2a_http_server.serve_in_thread(server)
    try:
        h, p = server.server_address[:2]
        ep = {"endpoint_id": "peer", "url": f"http://{h}:{p}/v1/a2a/receive", "hmac_key": k_hmac, "recv_key": k_recv,
              "instance_id": "iid-media-receiver", "enabled": True, "default_ttl_s": 240, "our_origin_id": "req"}
        (endpoints / "peer.json").write_text(json.dumps(ep))
        (endpoints / "peer.json").chmod(0o600)
        sender = rts.RemoteTriggerSender(endpoints_dir=endpoints, instance_id="iid-media-sender", forge_se=se)
        results = []
        for case in _cases():
            a = att.Attachment.from_bytes(name=case["name"], mime=case["mime"], content=case["bytes"])
            res = sender.send(
                "peer",
                instruction=("The folder named in your working directory 'in/' holds exactly one file. Open it with your tools. "
                             "Answer with ONE JSON object and nothing else: "
                             '{"kind": "<what the file is>", "marker": "<the text CORVIN-... that is written inside the file, or empty>", '
                             '"inspected": true or false, "how": "<one short sentence: how you inspected it or why you could not>"}'),
                # "output" is the receiver's fallback key for a reply that is not one JSON object; declaring it keeps a
                # prose answer visible (and recorded) instead of filtering it to nothing.
                result_schema={"properties": {"kind": {"type": "string"}, "marker": {"type": "string"},
                                              "inspected": {"type": "boolean"}, "how": {"type": "string"},
                                              "output": {"type": "string"}}},
                attachments=[a], ttl_s=200, timeout_s=230)
            row = {"file": case["name"], "mime": case["mime"], "bytes": len(case["bytes"]), "ok": res.ok, "status": res.status,
                   "answer": res.data, "expected_marker": case["marker"]}
            results.append(row)
            assert res.ok, f"{case['name']}: the envelope with the attachment was not answered ok: {row}"
            if case["must_quote"]:
                got = str((res.data or {}).get("marker", "")) + str((res.data or {}).get("output", ""))
                assert case["marker"] in got, f"{case['name']}: the worker did not read the marker out of the file: {row}"
        out = Path(os.environ.get("CORVIN_A2A_MEDIA_OUT") or (tmp_path / "media_matrix.json"))
        out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        print("MEDIA MATRIX ->", out)
        for r in results:
            print(f"  {r['file']:14} inspected={r['answer'].get('inspected')} marker={r['answer'].get('marker')!r} how={r['answer'].get('how')!r}")
    finally:
        server.shutdown()
        server.server_close()
        os.environ.pop("CORVIN_A2A_ATTESTATION_DISABLED", None)
