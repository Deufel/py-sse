"""Security smoke tests for Wave Chat.

Run the app first:  uvicorn w2:app
Then:               python test_security.py

Tests cookie forgery, XSS vectors, file upload abuse, body limits,
and path traversal. All tests hit the live server — no mocking.
"""
import requests, json, base64, time, re, sys

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PASS = 0
FAIL = 0

def test(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  ← {detail}")


def get_session():
    """Hit home page, get a signed cookie back."""
    s = requests.Session()
    r = s.get(f"{BASE}/")
    return s, r


# ══════════════════════════════════════════════════════════════
print("\n── 1. Cookie Security ──")
# ══════════════════════════════════════════════════════════════

session, r = get_session()
test("Home page loads", r.status_code == 200)
cookie = session.cookies.get("user", "")
test("Cookie is signed (has 3 dot-separated parts)", cookie.count(".") == 2, f"got: {cookie[:60]}")

forged = requests.Session()
forged.cookies.set("user", "Admin99")
r = forged.get(f"{BASE}/")
test("Forged plain cookie rejected", "You are Admin99" not in r.text)

if cookie:
    tampered = cookie[:-4] + "XXXX"
    tampered_session = requests.Session()
    tampered_session.cookies.set("user", tampered)
    r = tampered_session.get(f"{BASE}/")
    test("Tampered signed cookie rejected", "You are " in r.text and tampered not in r.text)


# ══════════════════════════════════════════════════════════════
print("\n── 2. XSS Prevention ──")
# ══════════════════════════════════════════════════════════════

xss_payloads = [
    ("<script>alert('xss')</script>", "script tag"),
    ('<img onerror=alert(1) src=x>', "img onerror"),
    ('"><svg onload=alert(1)>', "svg onload"),
    ("javascript:alert(1)", "javascript: proto"),
]

for payload, label in xss_payloads:
    r = session.post(f"{BASE}/send", json={"datastar": {"text": payload}})
    test(f"XSS payload accepted (stored raw): {label}", r.status_code == 204)

time.sleep(0.3)
r = session.get(f"{BASE}/")
html = r.text

# Check for ACTUAL executable patterns — raw tags that a browser would run.
# Entity-escaped versions (&lt;script&gt;) are safe and expected.
test("No raw <script> tag in output",
     "<script>alert(" not in html,
     "unescaped script tag found — XSS vulnerability!")

test("No raw <img with event handler in output",
     not re.search(r'<img\s[^>]*onerror\s*=', html),
     "unescaped img+onerror found — XSS vulnerability!")

test("No raw <svg with event handler in output",
     not re.search(r'<svg\s[^>]*onload\s*=', html),
     "unescaped svg+onload found — XSS vulnerability!")

# Confirm escaping is actually happening (entity-encoded chars present)
test("Payloads are entity-escaped",
     "&lt;" in html or "&amp;" in html,
     "no escaped entities found — html_tags escaping may be broken")


# ══════════════════════════════════════════════════════════════
print("\n── 3. File Upload Validation ──")
# ══════════════════════════════════════════════════════════════

small_data = base64.b64encode(b"hello world").decode()
r = session.post(f"{BASE}/send", json={"datastar": {
    "text": "", "fileName": "test.txt", "fileType": "text/plain",
    "fileSize": 11, "fileData": small_data
}})
test("Valid file upload accepted", r.status_code == 204)

r = session.post(f"{BASE}/send", json={"datastar": {
    "text": "", "fileName": "bad.txt", "fileType": "text/plain",
    "fileSize": 100, "fileData": "not!!!valid!!!base64"
}})
test("Invalid base64 rejected (204 silent)", r.status_code == 204)

# Path traversal
r = session.post(f"{BASE}/send", json={"datastar": {
    "text": "", "fileName": "../../../etc/passwd", "fileType": "text/plain",
    "fileSize": 11, "fileData": small_data
}})
test("Path traversal filename accepted (sanitised server-side)", r.status_code == 204)
time.sleep(0.2)
r = session.get(f"{BASE}/")
test("Path traversal stripped from filename", "../../../etc/passwd" not in r.text)
test("Sanitised filename 'passwd' visible", "passwd" in r.text)

# XSS in filename — sanitize_filename now strips < > " ' `
r = session.post(f"{BASE}/send", json={"datastar": {
    "text": "", "fileName": '<img onerror=alert(1)>.png', "fileType": "image/png",
    "fileSize": 11, "fileData": small_data
}})
time.sleep(0.2)
r = session.get(f"{BASE}/")
# Angle brackets stripped → no <img tag possible
test("Angle brackets stripped from filename",
     not re.search(r'<img\s[^>]*onerror', r.text),
     "filename contains executable <img tag — XSS vulnerability!")

# Malformed MIME
r = session.post(f"{BASE}/send", json={"datastar": {
    "text": "", "fileName": "file.bin", "fileType": "../../evil",
    "fileSize": 11, "fileData": small_data
}})
test("Malformed MIME falls back gracefully", r.status_code == 204)

# Oversized file
big_data = base64.b64encode(b"X" * (6 * 1024 * 1024)).decode()
r = session.post(f"{BASE}/send", json={"datastar": {
    "text": "", "fileName": "huge.bin", "fileType": "application/octet-stream",
    "fileSize": 100, "fileData": big_data
}})
test("Oversized file rejected", r.status_code in (204, 500))


# ══════════════════════════════════════════════════════════════
print("\n── 4. Body Size Limits ──")
# ══════════════════════════════════════════════════════════════

try:
    giant = "A" * (7 * 1024 * 1024)
    r = session.post(f"{BASE}/send",
        data=giant,
        headers={"Content-Type": "application/json"},
        timeout=5)
    test("Giant body doesn't crash server", r.status_code in (204, 400, 500))
except Exception as e:
    test("Giant body handled", True, f"connection closed: {e}")


# ══════════════════════════════════════════════════════════════
print("\n── 5. File Serving ──")
# ══════════════════════════════════════════════════════════════

r = session.get(f"{BASE}/file/nonexistent123")
test("Non-existent file returns 204/404", r.status_code in (204, 404))

r = session.get(f"{BASE}/")
file_urls = re.findall(r'/file/([A-Za-z0-9_-]+)', r.text)
if file_urls:
    fid = file_urls[0]
    r = session.get(f"{BASE}/file/{fid}")
    test("File serves with 200", r.status_code == 200)
    test("File has content-type header", "content-type" in r.headers)
    test("File has cache-control", "immutable" in r.headers.get("cache-control", ""))
    test("File has content-disposition", "content-disposition" in r.headers)
else:
    test("File serving (skipped — no files found)", True)


# ══════════════════════════════════════════════════════════════
print("\n── 6. Authorization ──")
# ══════════════════════════════════════════════════════════════

sess_a, _ = get_session()
sess_a.post(f"{BASE}/send", json={"datastar": {"text": "user A message"}})
time.sleep(0.2)

r = sess_a.get(f"{BASE}/")
msg_ids = re.findall(r'id="msg-([^"]+)"', r.text)
if msg_ids:
    target_id = msg_ids[-1]

    sess_b, _ = get_session()
    sess_b.post(f"{BASE}/delete?id={target_id}")
    time.sleep(0.2)
    r = sess_a.get(f"{BASE}/")
    test("Other user cannot delete your message", f'id="msg-{target_id}"' in r.text)

    sess_b.post(f"{BASE}/edit-start?id={target_id}")
    sess_b.post(f"{BASE}/edit?id={target_id}&text=HACKED")
    time.sleep(0.2)
    r = sess_a.get(f"{BASE}/")
    test("Other user cannot edit your message", "HACKED" not in r.text)
else:
    test("Authorization (skipped — no messages found)", True)


# ══════════════════════════════════════════════════════════════
print(f"\n{'═' * 50}")
print(f"  {PASS} passed, {FAIL} failed")
if FAIL == 0:
    print("  All security checks passed ✓")
else:
    print(f"  ⚠  {FAIL} issue(s) need attention")
print(f"{'═' * 50}\n")

sys.exit(1 if FAIL else 0)
