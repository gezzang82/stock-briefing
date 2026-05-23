"""
카카오 OAuth 일회성 셋업 — access_token + refresh_token 발급.

사전 조건 (카카오 개발자 콘솔 https://developers.kakao.com):
  1) 카카오 로그인 → 사용 설정 ON
  2) 앱 → 플랫폼 키 → REST API 키 → Redirect URI 등록:
     http://localhost/callback
     (카카오는 포트 80/443만 허용 → 8000 같은 임의 포트 불가)
  3) 카카오 로그인 → 동의항목 → "카카오톡 메시지 전송 (talk_message)" 사용 설정
  4) 앱 → 플랫폼 키 → REST API 키 페이지에서 키 값 복사

사용:
  python kakao_auth.py <REST_API_KEY>

흐름:
  1. 브라우저 자동 오픈 → 카카오 로그인 + 동의
  2. 카카오가 http://localhost/callback?code=XXX 으로 리다이렉트
     (로컬 80포트가 열려있지 않아 "사이트에 연결할 수 없음" 페이지가 뜸 — 정상)
  3. 브라우저 주소창에서 "code=" 다음의 긴 문자열을 복사
  4. 터미널에 붙여넣기 → 토큰 교환 → access_token + refresh_token 출력
"""
import sys
import urllib.parse
import webbrowser

import requests


REDIRECT_URI = "http://localhost/callback"


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
        print("REST API 키는 카카오 개발자 콘솔 → 앱 → 플랫폼 키 → REST API 키")
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
    print("1) 브라우저가 자동으로 열려 카카오 로그인 페이지가 뜹니다.")
    print("2) 로그인 + 권한 동의 (talk_message) 클릭.")
    print(f"3) 카카오가 {REDIRECT_URI}?code=XXX 로 리다이렉트합니다.")
    print('   브라우저는 "사이트에 연결할 수 없음" 페이지를 보여줍니다 (정상).')
    print('4) 그 페이지의 주소창에서 "code=" 다음에 오는 긴 문자열만 복사.')
    print('   예: http://localhost/callback?code=AbCdEf12...')
    print('              여기서부터 끝까지 ───↑')
    print()
    input("준비되면 Enter — 브라우저 열림: ")
    webbrowser.open(auth_url)
    print()
    print("브라우저 열기 실패 시 직접 접속:")
    print(f"  {auth_url}")
    print()

    code = input("주소창에서 복사한 code 값 붙여넣기: ").strip()
    if not code:
        print("❌ code 입력 없음")
        sys.exit(1)
    # code= 부분 들어와도 자동 제거, & 뒤 부분도 제거
    if "code=" in code:
        code = code.split("code=", 1)[1]
    if "&" in code:
        code = code.split("&", 1)[0]
    print(f"\n✅ 인가 코드 (앞 12자): {code[:12]}...")

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
