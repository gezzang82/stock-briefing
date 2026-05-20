"""
초기 설정 도구
실행: python setup.py --kakao-auth
"""
import argparse
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

BASE_DIR = Path(__file__).parent


def _load_env():
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    import os
    return {
        "KAKAO_REST_API_KEY": os.getenv("KAKAO_REST_API_KEY", ""),
        "KAKAO_CLIENT_SECRET": os.getenv("KAKAO_CLIENT_SECRET", ""),
    }


def _update_env(key: str, value: str):
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        env_path.write_text("")
    content = env_path.read_text()
    lines = content.splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")
    print(f"✅ {key} 저장 완료")


def kakao_auth():
    """카카오톡 OAuth 인증 (나에게 보내기 권한 획득)"""
    import requests

    env = _load_env()
    rest_api_key = env["KAKAO_REST_API_KEY"]
    if not rest_api_key:
        print("❌ .env에 KAKAO_REST_API_KEY를 먼저 설정해주세요.")
        sys.exit(1)

    # 카카오 개발자 앱에 redirect_uri: http://localhost로 등록 필요
    redirect_uri = "http://localhost"
    params = {
        "client_id": rest_api_key,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "talk_message",
    }
    auth_url = "https://kauth.kakao.com/oauth/authorize?" + urlencode(params)

    print("\n📱 카카오 로그인이 필요합니다.")
    print("1. 아래 URL을 브라우저에서 열어 로그인하세요:")
    print(f"\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("2. 로그인 후 리디렉션된 URL에서 'code=' 뒤의 값을 복사하세요.")
    print("   예) http://localhost/?code=XXXXXX")
    code = input("인증 코드 입력: ").strip()

    # 토큰 교환
    data = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if env["KAKAO_CLIENT_SECRET"]:
        data["client_secret"] = env["KAKAO_CLIENT_SECRET"]

    resp = requests.post("https://kauth.kakao.com/oauth/token", data=data)
    if resp.status_code != 200:
        print(f"❌ 토큰 발급 실패: {resp.text}")
        sys.exit(1)

    tokens = resp.json()
    _update_env("KAKAO_ACCESS_TOKEN", tokens["access_token"])
    _update_env("KAKAO_REFRESH_TOKEN", tokens.get("refresh_token", ""))

    print("\n✅ 카카오톡 인증 완료!")
    print("테스트: python setup.py --test-kakao")


def test_kakao():
    """카카오톡 테스트 메시지 전송"""
    sys.path.insert(0, str(BASE_DIR))
    from kakao_sender import send_message
    result = send_message("🔔 주식 브리핑 시스템 테스트 메시지입니다.")
    print("✅ 전송 성공" if result else "❌ 전송 실패")


def test_kis():
    """KIS API 연결 테스트"""
    sys.path.insert(0, str(BASE_DIR))
    from kis_api import kis
    print("KOSPI 조회...")
    data = kis.get_index("KOSPI")
    if data:
        print(f"✅ KOSPI: {data['current']:,.2f} ({data['change_pct']:+.2f}%)")
    else:
        print("❌ KIS API 연결 실패")


def install_launchd():
    """macOS launchd 스케줄 등록 (매일 06:30)"""
    python = sys.executable
    script = BASE_DIR / "main.py"
    plist_name = "com.stock.briefing"
    plist_path = Path.home() / "Library/LaunchAgents" / f"{plist_name}.plist"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{plist_name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{BASE_DIR}/logs/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{BASE_DIR}/logs/stderr.log</string>
    <key>RunAtLoad</key>
    <false/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>"""

    plist_path.write_text(plist_content)
    print(f"✅ plist 생성: {plist_path}")

    # 기존 등록 해제 후 재등록
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    if result.returncode == 0:
        print("✅ launchd 등록 완료 - 매일 06:30 자동 실행")
    else:
        print(f"❌ launchd 등록 실패: {result.stderr}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="주식 브리핑 설정 도구")
    parser.add_argument("--kakao-auth", action="store_true", help="카카오톡 OAuth 인증")
    parser.add_argument("--test-kakao", action="store_true", help="카카오톡 테스트 전송")
    parser.add_argument("--test-kis", action="store_true", help="KIS API 연결 테스트")
    parser.add_argument("--install", action="store_true", help="macOS launchd 등록 (06:30 자동 실행)")
    args = parser.parse_args()

    if args.kakao_auth:
        kakao_auth()
    elif args.test_kakao:
        test_kakao()
    elif args.test_kis:
        test_kis()
    elif args.install:
        install_launchd()
    else:
        parser.print_help()
