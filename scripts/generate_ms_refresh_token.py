import argparse
import base64
import hashlib
import json
import secrets
import sys
import time
import webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


AUTHORITY = "https://login.microsoftonline.com/consumers"
DEVICE_CODE_URL = f"{AUTHORITY}/oauth2/v2.0/devicecode"
TOKEN_URL = f"{AUTHORITY}/oauth2/v2.0/token"
SCOPES = "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read"
REQUEST_TIMEOUT_SECONDS = 45
LOCAL_REDIRECT_HOST = "localhost"
LOCAL_REDIRECT_PORT = 17374


def post_form(url, data):
    encoded = urlencode(data).encode("utf-8")
    request = Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.getcode(), json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"error": f"HTTP {exc.code}", "error_description": body or str(exc)}
        return exc.code, payload
    except URLError as exc:
        raise RuntimeError(f"request failed: {exc}") from exc


def parse_import_line(line):
    parts = [part.strip() for part in str(line or "").strip().split("----")]
    if len(parts) < 3:
        raise ValueError("line format must be email----password----client_id----refresh_token")
    return {
        "email": parts[0],
        "password": parts[1],
        "client_id": parts[2],
    }


def request_device_code(client_id):
    status, payload = post_form(DEVICE_CODE_URL, {
        "client_id": client_id,
        "scope": SCOPES,
    })
    if status >= 400 or not payload.get("device_code"):
        detail = payload.get("error_description") or payload.get("error") or payload
        raise RuntimeError(f"device code request failed: {detail}")
    return payload


def poll_token(client_id, device_code, expires_in, interval):
    deadline = time.monotonic() + int(expires_in)
    poll_interval = max(5, int(interval or 5))
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        status, payload = post_form(TOKEN_URL, {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": device_code,
        })
        if status < 400 and payload.get("refresh_token"):
            return payload

        error = str(payload.get("error") or "")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            poll_interval += 5
            continue
        detail = payload.get("error_description") or error or payload
        raise RuntimeError(f"token request failed: {detail}")
    raise TimeoutError("device code expired before authorization completed")


def b64url_no_padding(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")


def make_pkce_pair():
    verifier = b64url_no_padding(secrets.token_bytes(48))
    challenge = b64url_no_padding(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    server_version = "GuJumpgateTokenCallback/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        self.server.callback_params = params
        if "error" in params:
            message = params.get("error_description", params.get("error", ["authorization failed"]))[0]
            body = f"Authorization failed: {message}"
            status = 400
        else:
            body = "Authorization completed. You can close this tab and return to Codex."
            status = 200
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format, *_args):
        return


def wait_for_authorization_code(client_id, email_addr, timeout_seconds=600):
    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer((LOCAL_REDIRECT_HOST, LOCAL_REDIRECT_PORT), OAuthCallbackHandler)
    server.callback_params = None
    redirect_uri = f"http://{LOCAL_REDIRECT_HOST}:{LOCAL_REDIRECT_PORT}"
    auth_params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'response_mode': 'query',
        'scope': SCOPES,
        'state': state,
        'login_hint': email_addr,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    auth_url = f"{AUTHORITY}/oauth2/v2.0/authorize?{urlencode(auth_params)}"

    print("", flush=True)
    print(f"账号: {email_addr}", flush=True)
    print("请在浏览器中完成 Microsoft 登录和授权。", flush=True)
    print(auth_url, flush=True)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            server.handle_request()
            params = server.callback_params
            if not params:
                continue
            if params.get("state", [""])[0] != state:
                raise RuntimeError("authorization callback state mismatch")
            if params.get("error"):
                detail = params.get("error_description", params.get("error", ["authorization failed"]))[0]
                raise RuntimeError(f"authorization failed: {detail}")
            code = params.get("code", [""])[0]
            if not code:
                raise RuntimeError("authorization callback missing code")
            return code, verifier, redirect_uri
    finally:
        server.server_close()
    raise TimeoutError("authorization timed out")


def request_token_by_auth_code(client_id, code, verifier, redirect_uri):
    status, payload = post_form(TOKEN_URL, {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    })
    if status >= 400 or not payload.get("refresh_token"):
        detail = payload.get("error_description") or payload.get("error") or payload
        raise RuntimeError(f"authorization_code token request failed: {detail}")
    return payload


def generate_for_account(account):
    code, verifier, redirect_uri = wait_for_authorization_code(account["client_id"], account["email"])
    token = request_token_by_auth_code(account["client_id"], code, verifier, redirect_uri)
    refresh_token = str(token.get("refresh_token") or "").strip()
    if not refresh_token:
        raise RuntimeError("token response missing refresh_token")
    return "----".join([
        account["email"],
        account["password"],
        account["client_id"],
        refresh_token,
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate Microsoft refresh tokens for GuJumpgate Hotmail account import lines.",
    )
    parser.add_argument(
        "--line",
        action="append",
        help="Existing import line: email----password----client_id----old_refresh_token",
    )
    parser.add_argument(
        "--file",
        help="Text file containing one import line per account.",
    )
    parser.add_argument(
        "--output",
        help="Optional output file for refreshed import lines.",
    )
    args = parser.parse_args(argv)

    raw_lines = []
    if args.file:
        raw_lines.extend(Path(args.file).read_text(encoding="utf-8").splitlines())
    if args.line:
        raw_lines.extend(args.line)
    raw_lines = [line.strip() for line in raw_lines if line.strip()]
    if not raw_lines:
        parser.error("provide --line or --file")

    accounts = [parse_import_line(line) for line in raw_lines]
    refreshed_lines = []
    for account in accounts:
        refreshed_lines.append(generate_for_account(account))

    output_text = "\n".join(refreshed_lines) + "\n"
    if args.output:
        Path(args.output).write_text(output_text, encoding="utf-8")
        print(f"\n已写入: {args.output}", flush=True)
    else:
        print("\n新的插件导入内容:", flush=True)
        sys.stdout.write(output_text)


if __name__ == "__main__":
    main()
