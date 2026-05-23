"""
카카오 OAuth 일회성 셋업 — access_token + refresh_token 발급.

사전 조건 (카카오 개발자 콘솔 https://developers.kakao.com):
  1) 앱 설정 → 플랫폼 → Web 플랫폼 등록
     사이트 도메인: http://localhost:8000
  2) 카카오 로그인 → 활성화 설정 ON
  3) 카카오 로그인 → Redirect URI 등록: http://localhost:8000/callback
  4) 카카오 로그인 → 동의항목 → "카카오톡 메시지 (talk_message)" 사용 설정
  5) 앱 키 페이지에서 REST API 키 복사

사용:
  python kakao_auth.py <REST_API_KEY>

흐름:
  - 브라우저 자동 오픈 → 카카오 로그인 + 동의
  - localhost:8000/callback 으로 리다이렉트 (스크립트가 코드 캡처)
  - 코드 → 토큰 교환
  - access_token + refresh_token 출력
  - GitHub Secret 등록 명령어도 출력
"""
import http.server
import socketserver
import sys
import urllib.parse
import webbrowser

import requests


PORT = 8000
REDIRECT_URI = f"http://localhost:{PORT}/callback"

_captured: dict[str, str] = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/callback"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                _captured["code"] = qs["code"][0]
                msg = "<h1>✅ 인증 완료</h1><p>터미널로 돌아가세요.</p>"
            else:
                _captured["error"] = qs.get("error", ["unknown"])[0]
                msg = f"<h1>❌ 실패</h1><p>{_captured['error']}</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args):
        # http.server 기본 로그 억제
        pass


def exchange_code_for_tokens(rest_api_key: str, code: str) -> dict:
    resp = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": rest_api_key,
            "redirect_uri": REDIRECT_URI,
            "code": code,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    if len(sys.argv) < 2:
        print("사용법: python kakao_auth.py <REST_API_KEY>")
        print()
        print("REST API 키는 카카오 개발자 콘솔 → 내 애플리케이션 → 앱 키 페이지")
        print("https://developers.kakao.com/console/app")
        sys.exit(1)

    rest_api_key = sys.argv[1].strip()
    auth_url = (
        "https://kauth.kakao.com/oauth/authorize?"
        f"response_type=code"
        f"&client_id={rest_api_key}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
        f"&scope=talk_message"
    )

    print("=" * 60)
    print("카카오 OAuth — access_token + refresh_token 발급")
    print("=" * 60)
    print()
    print(f"1) 브라우저 자동 오픈: {auth_url[:80]}...")
    print(f"2) 카카오 로그인 + 권한 동의 (talk_message)")
    print(f"3) localhost:{PORT}/callback 으로 자동 리다이렉트")
    print()
    webbrowser.open(auth_url)

    print(f"localhost:{PORT} 콜백 대기 중... (브라우저에서 로그인하세요)")
    with socketserver.TCPServer(("", PORT), CallbackHandler) as httpd:
        while "code" not in _captured and "error" not in _captured:
            httpd.handle_request()

    if "error" in _captured:
        print(f"\n❌ 인증 실패: {_captured['error']}")
        sys.exit(1)

    code = _captured["code"]
    print(f"\n✅ 인가 코드 캡처 완료 (앞 12자: {code[:12]}...)")

    try:
        tokens = exchange_code_for_tokens(rest_api_key, code)
    except Exception as e:
        print(f"\n❌ 토큰 교환 실패: {e}")
        sys.exit(1)

    access = tokens["access_token"]
    refresh = tokens.get("refresh_token", "")
    expires_in = tokens.get("expires_in", 0)
    refresh_expires_in = tokens.get("refresh_token_expires_in", 0)

    print()
    print("=" * 60)
    print("✅ 토큰 발급 완료")
    print("=" * 60)
    print(f"access_token   ({expires_in}s = {expires_in // 3600}h):")
    print(f"  {access}")
    print()
    print(f"refresh_token  ({refresh_expires_in}s = {refresh_expires_in // 86400}일):")
    print(f"  {refresh}")
    print()
    print("=" * 60)
    print("GitHub Secret 등록 (아래 3줄 복사해서 실행):")
    print("=" * 60)
    print(f"export PATH=\"/opt/homebrew/bin:$PATH\"")
    print(f"printf '%s' '{rest_api_key}' | gh secret set KAKAO_REST_API_KEY --repo gezzang82/stock-briefing")
    print(f"printf '%s' '{access}' | gh secret set KAKAO_ACCESS_TOKEN --repo gezzang82/stock-briefing")
    print(f"printf '%s' '{refresh}' | gh secret set KAKAO_REFRESH_TOKEN --repo gezzang82/stock-briefing")
    print()


if __name__ == "__main__":
    main()
